"""`ayasee init` (issue #11): a profile scaffold pinned to one release commit, no placeholder."""

import base64
import re
import tomllib
from pathlib import Path

import httpx
import pytest
import respx
import yaml

from ayasee import cli, doctor, scaffold  # noqa: F401  (registers the subcommands)
from ayasee.codescanning import AVAILABLE, UNAVAILABLE, CodeScanning
from ayasee.codescanning import UNKNOWN as UNKNOWN_CS
from ayasee.config import REVIEWER_PLACEHOLDER, ConfigError, parse_config_text
from ayasee.github import GitHub
from ayasee.plansupport import (
    FREE_MISSING,
    REVIEWERS,
    SUPPORTED,
    UNKNOWN,
    UNSUPPORTED,
    PlanSupport,
)

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
SHA = "b" * 40


def _init(tmp_path, *args):
    return cli.main(["init", "--dir", str(tmp_path), "--ref", SHA, "--unverified-ref", *args])


def test_cloudflare_in_a_monorepo(tmp_path, capsys):
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--iac-root", "infra/iac") == 0
    cfg = parse_config_text((tmp_path / ".ayasee.yaml").read_text())
    assert cfg.stages == ["test", "prod"]
    assert (cfg.iac_root, cfg.units_root) == ("infra/iac", "infra/iac/stages")
    assert cfg.shared_units == [] and cfg.identity == "secrets"
    for stage in ("test", "prod"):
        assert (tmp_path / f"infra/iac/stages/{stage}/.gitkeep").is_file()
    assert (tmp_path / "infra/iac/root.hcl").is_file()
    main = yaml.safe_load((tmp_path / ".github/workflows/iac-staged.yml").read_text())
    assert main[True]["push"]["paths"] == [
        "infra/iac/**",
        ".ayasee.yaml",
        ".github/workflows/iac-*.yml",
    ]
    for name in ("pr", "staged", "nightly", "breakglass"):
        text = (tmp_path / f".github/workflows/iac-{name}.yml").read_text()
        assert f"\nname: iac-{name}\n" in text
        assert "tttao/ayasee/.github/workflows/iac-" in text and f"@{SHA} #" in text
        assert "OWNER" not in text and "0" * 40 not in text
    pr = yaml.safe_load((tmp_path / ".github/workflows/iac-pr.yml").read_text())
    assert "paths" not in (pr[True]["pull_request"] or {})
    boot = (tmp_path / "infra/iac/bootstrap/main.tf").read_text()
    assert 'stages     = ["test", "prod"]' in boot
    assert (
        f'"git::https://github.com/tttao/ayasee.git//modules/github-environments?ref={SHA}"' in boot
    )
    assert "shared_units_pr_plan = false" in boot and "../modules" not in boot
    assert f"Pinned to tttao/ayasee@{SHA}" in capsys.readouterr().out


def test_at_the_repository_root_no_paths_filter(tmp_path):
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--stages", "dev,staging,prod") == 0
    cfg = parse_config_text((tmp_path / ".ayasee.yaml").read_text())
    assert cfg.stages == ["dev", "staging", "prod"] and cfg.units_root == "stages"
    assert "iac_root" not in (tmp_path / ".ayasee.yaml").read_text()
    main = yaml.safe_load((tmp_path / ".github/workflows/iac-staged.yml").read_text())
    assert "paths" not in main[True]["push"]
    assert 'stages     = ["dev", "staging", "prod"]' in (tmp_path / "bootstrap/main.tf").read_text()


def test_databricks(tmp_path):
    assert _init(tmp_path, "--profile", "databricks-azure", "--source", "acme/ayasee-mirror") == 0
    cfg = parse_config_text((tmp_path / ".ayasee.yaml").read_text())
    assert cfg.stages == ["dev", "uat", "prod"] and cfg.identity == "oidc"
    boot = (tmp_path / "bootstrap/main.tf").read_text()
    assert (
        f"git::https://github.com/acme/ayasee-mirror.git//modules/azure-stage-identity?ref={SHA}"
        in boot
    )
    assert 'stages = ["dev", "uat", "prod"]' in boot
    for name in ("variables.tf", "outputs.tf"):
        assert (tmp_path / "bootstrap" / name).is_file()
    assert not (tmp_path / ".github/workflows/cockpit.yml").exists()
    text = (tmp_path / ".github/workflows/iac-staged.yml").read_text()
    assert f"acme/ayasee-mirror/.github/workflows/iac-staged.yml@{SHA}" in text


def test_the_scaffold_passes_doctor_offline(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--iac-root", "infra") == 0
    capsys.readouterr()
    assert cli.main(["doctor", "--root", str(tmp_path), "--repo", "acme/estate"]) == 2
    out = capsys.readouterr().out
    assert "0 failed" in out


def test_a_file_that_differs_is_kept_and_diffed(tmp_path, capsys):
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 0
    caller = tmp_path / ".github/workflows/iac-nightly.yml"
    caller.write_text(caller.read_text() + "# mine\n")
    capsys.readouterr()
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 1
    err = capsys.readouterr().err
    assert "exist and differ" in err and "iac-nightly.yml" in err
    assert "--- a/.github/workflows/iac-nightly.yml (yours)" in err
    assert caller.read_text().endswith("# mine\n")
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--force") == 0
    assert not caller.read_text().endswith("# mine\n")


# ---- an existing .ayasee.yaml (issue #48) ------------------------------------------------------


def _customised(tmp_path, *args):
    """A configuration init wrote, then customised, with no caller yet."""
    assert _init(tmp_path, "--profile", "cloudflare-s3", *args) == 0
    cfg = tmp_path / ".ayasee.yaml"
    text = cfg.read_text().replace(REVIEWER_PLACEHOLDER, "tttao") + "solo_mode: true\n"
    for p in sorted(tmp_path.rglob("*"), reverse=True):
        if p != cfg and p.is_file():
            p.unlink()
    cfg.write_text(text)
    return cfg, text


def test_an_existing_configuration_is_kept_and_the_rest_written(tmp_path, capsys):
    args = ("--iac-root", "infra/iac", "--plan", "free")
    cfg, text = _customised(tmp_path, *args)
    capsys.readouterr()
    assert _init(tmp_path, "--profile", "cloudflare-s3", *args) == 0
    out, err = capsys.readouterr()
    assert cfg.read_text() == text  # reviewers.prod and solo_mode survive
    assert "kept .ayasee.yaml" in out and "wrote .github/workflows/iac-approve.yml" in out
    assert "init keeps it" in err and "key only in your file: solo_mode" in err
    assert (tmp_path / "infra/iac/root.hcl").is_file()
    boot = (tmp_path / "infra/iac/bootstrap/main.tf").read_text()
    assert re.search(r'variable "private_repository_plan" \{[^}]*default\s*=\s*"free"', boot, re.S)


def test_force_never_replaces_a_configuration_that_loads(tmp_path):
    cfg, text = _customised(tmp_path, "--plan", "free")
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--plan", "free", "--force") == 0
    assert cfg.read_text() == text


def test_replace_config_writes_the_template(tmp_path):
    cfg, _ = _customised(tmp_path, "--plan", "free")
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--plan", "free", "--replace-config") == 0
    assert REVIEWER_PLACEHOLDER in cfg.read_text() and "solo_mode" not in cfg.read_text()


def test_the_layout_is_read_from_the_configuration(tmp_path, capsys):
    """No flag: stages, iac_root and the prefix come from the file, not the profile defaults."""
    cfg = tmp_path / ".ayasee.yaml"
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--stages", "dev,staging,prod",
                 "--iac-root", "infra", "--environment-prefix", "ops-") == 0  # fmt: skip
    text = cfg.read_text()
    for p in [p for p in tmp_path.rglob("*") if p.is_file() and p != cfg]:
        p.unlink()
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 0
    assert cfg.read_text() == text
    boot = (tmp_path / "infra/bootstrap/main.tf").read_text()
    assert '["dev", "staging", "prod"]' in boot
    assert re.search(r'variable "environment_prefix" \{[^}]*default\s*=\s*"ops-"', boot, re.S)
    assert (tmp_path / "infra/stages/staging/.gitkeep").is_file()


@pytest.mark.parametrize(
    ("flag", "msg"),
    [
        (["--stages", "dev,prod"], "--stages dev,prod (it has test,prod)"),
        (["--iac-root", "infra"], "--iac-root infra (it has .)"),
        (["--environment-prefix", "x-"], "--environment-prefix 'x-' (it has '')"),
        (["--gate-mechanism", "workflow"], "contradicts gate_mechanism: environment"),
    ],
)
def test_a_flag_that_contradicts_the_configuration_is_refused(tmp_path, capsys, flag, msg):
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 0
    (tmp_path / "root.hcl").unlink()
    capsys.readouterr()
    assert _init(tmp_path, "--profile", "cloudflare-s3", *flag) == 1
    assert msg in capsys.readouterr().err
    assert not (tmp_path / "root.hcl").exists()  # nothing written


def test_a_gate_the_detection_would_not_choose_needs_the_flag(tmp_path, capsys):
    cfg, text = _customised(tmp_path, "--plan", "free")  # gate_mechanism: workflow
    capsys.readouterr()
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 1  # no plan known: environment
    err = capsys.readouterr().err
    assert "sets gate_mechanism: workflow, but init would choose environment" in err
    assert "--gate-mechanism workflow" in err
    assert not [p for p in (tmp_path / ".github").rglob("*") if p.is_file()]  # nothing written
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--gate-mechanism", "workflow") == 0
    assert cfg.read_text() == text


def test_a_configuration_that_does_not_load_is_refused_untouched(tmp_path, capsys):
    cfg = tmp_path / ".ayasee.yaml"
    cfg.write_text("stages: [test, prod]\nmine: 1\n")
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--force") == 1
    err = capsys.readouterr().err
    assert "does not load" in err and "unknown key(s): mine" in err and "--replace-config" in err
    assert cfg.read_text() == "stages: [test, prod]\nmine: 1\n"
    assert not (tmp_path / ".github").exists()
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--replace-config") == 0
    assert "mine" not in cfg.read_text()


def test_a_units_root_init_does_not_scaffold_is_refused(tmp_path, capsys):
    (tmp_path / ".ayasee.yaml").write_text("stages: [test, prod]\nunits_root: live\n")
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 1
    assert "units_root: live, but init scaffolds stages" in capsys.readouterr().err
    assert not (tmp_path / ".github").exists()


def test_upgrade_refuses_replace_config(tmp_path, capsys):
    assert _init(tmp_path, "--upgrade", "--replace-config") == 1
    assert "--replace-config does not apply" in capsys.readouterr().err


def test_init_can_run_twice(tmp_path, capsys):
    args = ("--profile", "cloudflare-s3", "--iac-root", "infra/iac")
    assert _init(tmp_path, *args) == 0
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    capsys.readouterr()
    assert _init(tmp_path, *args) == 0
    out = capsys.readouterr().out
    assert "wrote " not in out and "kept .ayasee.yaml" in out
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_a_gitkeep_with_content_or_a_missing_file_does_not_refuse(tmp_path, capsys):
    keep = tmp_path / "stages/test/.gitkeep"
    keep.parent.mkdir(parents=True)
    keep.write_text("kept by the team\n")
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 0
    assert keep.read_text() == "kept by the team\n"
    assert (tmp_path / "stages/prod/.gitkeep").is_file()


def test_a_partly_prepared_repository_only_gets_the_missing_files(tmp_path):
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 0
    (tmp_path / ".github/workflows/iac-nightly.yml").unlink()
    (tmp_path / "root.hcl").unlink()
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 0
    assert (tmp_path / ".github/workflows/iac-nightly.yml").is_file()
    assert (tmp_path / "root.hcl").is_file()


def test_existing_application_workflows_are_not_in_the_way(tmp_path):
    wf = tmp_path / ".github/workflows"
    wf.mkdir(parents=True)
    for name in ("pr.yml", "main.yml", "nightly.yml"):
        (wf / name).write_text("name: app\n")
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--iac-root", "infra/iac") == 0
    assert (wf / "main.yml").read_text() == "name: app\n"


def test_a_symlink_out_of_the_repository_is_refused(tmp_path, capsys):
    outside = tmp_path / "outside"
    outside.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".github").symlink_to(outside)
    assert _init(repo, "--profile", "cloudflare-s3") == 1
    assert "outside the repository" in capsys.readouterr().err
    assert not any(outside.iterdir())


def test_the_pr_caller_says_why_it_has_no_paths_filter(tmp_path):
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--iac-root", "infra") == 0
    text = (tmp_path / ".github/workflows/iac-pr.yml").read_text()
    assert "no paths: filter on purpose" in text


# ---- --upgrade ---------------------------------------------------------------------------------

NEW = "e" * 40


def _upgrade(tmp_path, *args, ref=NEW):
    return cli.main(
        ["init", "--upgrade", "--dir", str(tmp_path), "--ref", ref, "--unverified-ref", *args]
    )


def test_upgrade_moves_only_the_pin(tmp_path, capsys):
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--iac-root", "infra/iac") == 0
    staged = tmp_path / ".github/workflows/iac-staged.yml"
    edited = staged.read_text().replace(
        'paths: ["infra/iac/**", ".ayasee.yaml", ".github/workflows/iac-*.yml"]',
        'paths: ["infra/iac/**"]\n    tags-ignore: ["x"]',
    )
    staged.write_text(edited)
    cfg = (tmp_path / ".ayasee.yaml").read_text()
    capsys.readouterr()
    assert _upgrade(tmp_path) == 0
    assert SHA not in staged.read_text() and NEW in staged.read_text()
    assert staged.read_text() == edited.replace(SHA, NEW)  # nothing else moved
    assert (tmp_path / ".ayasee.yaml").read_text() == cfg
    boot = (tmp_path / "infra/iac/bootstrap/main.tf").read_text()
    assert f"?ref={NEW}" in boot and SHA not in boot
    for name in ("pr", "nightly", "breakglass"):
        assert f"@{NEW} #" in (tmp_path / f".github/workflows/iac-{name}.yml").read_text()
    assert "triggers untouched" in capsys.readouterr().out


def test_the_import_caller_is_scaffolded_pinned_and_upgraded(tmp_path):
    """iac-import (issue #58): a dispatch-only caller pinned like the others, its secrets by
    name and exactly those the reusable workflow declares; Cloudflare only (lookups)."""
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 0
    path = tmp_path / ".github/workflows/iac-import.yml"
    text = path.read_text()
    assert f"tttao/ayasee/.github/workflows/iac-import.yml@{SHA} #" in text
    assert "OWNER" not in text and "0" * 40 not in text and "inherit" not in text
    caller = yaml.safe_load(text)
    assert list(caller[True]) == ["workflow_dispatch"]
    (job,) = caller["jobs"].values()
    reusable = yaml.safe_load((ROOT / ".github/workflows/iac-import.yml").read_text())
    call = reusable[True]["workflow_call"]
    assert set(job["secrets"]) <= set(call["secrets"])
    assert all(v == f"${{{{ secrets.{k} }}}}" for k, v in job["secrets"].items())
    assert set(job["with"]) <= set(call["inputs"])
    needed: dict[str, str] = {}
    for called in reusable["jobs"].values():
        needed.update(called.get("permissions", {}))
    assert job["permissions"] == needed
    assert _upgrade(tmp_path) == 0
    assert f"iac-import.yml@{NEW} #" in path.read_text()
    other = tmp_path / "db"
    assert _init(other, "--profile", "databricks-azure") == 0
    assert not (other / ".github/workflows/iac-import.yml").exists()


def test_the_packaged_templates_carry_the_import_caller():
    force = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"]
    text = str(force)
    assert "examples/cloudflare-s3/.github/workflows/iac-import.yml" in text


def test_upgrade_twice_changes_nothing(tmp_path, capsys):
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 0
    assert _upgrade(tmp_path, ref=SHA) == 0
    assert "nothing changed" in capsys.readouterr().out


def test_upgrade_leaves_other_sources_and_other_actions_alone(tmp_path):
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 0
    other = tmp_path / ".github/workflows/deploy.yml"
    text = (
        "jobs:\n  a:\n    uses: acme/ayasee/.github/workflows/iac-pr.yml@" + SHA + "\n"
        "  b:\n    uses: actions/checkout@" + SHA + " # v6\n"
    )
    other.write_text(text)
    assert _upgrade(tmp_path) == 0
    assert other.read_text() == text


def test_upgrade_of_a_mirror_needs_its_source(tmp_path, capsys):
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--source", "acme/mirror") == 0
    assert _upgrade(tmp_path) == 1
    assert "nothing to upgrade" in capsys.readouterr().err
    assert _upgrade(tmp_path, "--source", "acme/mirror") == 0
    assert NEW in (tmp_path / ".github/workflows/iac-pr.yml").read_text()


def test_upgrade_does_not_write_through_a_symlinked_directory(tmp_path, capsys):
    outside = tmp_path / "outside"
    outside.mkdir()
    text = "uses: tttao/ayasee/.github/workflows/iac-pr.yml@" + SHA + "\n"
    (outside / "x.yml").write_text(text)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".github").symlink_to(outside.parent / "outside")
    (outside / "workflows").mkdir()
    (outside / "workflows" / "iac-pr.yml").write_text(text)
    assert _upgrade(repo) == 1
    assert "outside the repository" in capsys.readouterr().err
    assert (outside / "workflows" / "iac-pr.yml").read_text() == text


@respx.mock
def test_upgrade_verifies_the_new_pin(tmp_path, capsys):
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 0
    _pin("0.0.1")  # NEW is the release commit of another version than these templates
    rc = cli.main(["init", "--upgrade", "--dir", str(tmp_path), "--ref", SHA])
    err = capsys.readouterr().err
    assert rc == 1 and "release commit of 0.0.1" in err
    assert SHA in (tmp_path / ".github/workflows/iac-pr.yml").read_text()  # untouched


@respx.mock
def test_upgrade_to_a_verified_release_commit(tmp_path):
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--ref", "a" * 40, "--unverified-ref") == 0
    _pin(scaffold.__version__)
    assert cli.main(["init", "--upgrade", "--dir", str(tmp_path), "--ref", SHA]) == 0
    assert f"@{SHA} #" in (tmp_path / ".github/workflows/iac-pr.yml").read_text()


def test_upgrade_without_callers_refuses(tmp_path, capsys):
    assert _upgrade(tmp_path) == 1
    assert "nothing to upgrade" in capsys.readouterr().err


@pytest.mark.parametrize("flag", [["--profile", "cloudflare-s3"], ["--force"], ["--stages", "a"]])
def test_upgrade_takes_no_scaffold_options(tmp_path, capsys, flag):
    assert _upgrade(tmp_path, *flag) == 1
    assert "only moves the release pin" in capsys.readouterr().err


def test_upgrade_with_a_source_checkout_needs_ref(tmp_path, capsys):
    assert cli.main(["init", "--upgrade", "--dir", str(tmp_path)]) == 1
    assert "pass --ref" in capsys.readouterr().err


def test_a_pin_cannot_carry_text(tmp_path):
    with pytest.raises(SystemExit):
        _upgrade(tmp_path, ref=NEW[:39] + "\n; rm")


def test_init_requires_a_profile(tmp_path, capsys):
    assert _init(tmp_path) == 1
    assert "--profile is required" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("args", "msg"),
    [
        (["--iac-root", "../x"], "--iac-root"),
        (["--iac-root", "/abs"], None),  # normalised to "abs": a relative directory
        (["--stages", "prod,test"], "--stages"),
        (["--stages", "prod-plan,prod"], "--stages"),
    ],
)
def test_arguments(tmp_path, capsys, args, msg):
    rc = _init(tmp_path, "--profile", "cloudflare-s3", *args)
    if msg is None:
        assert rc == 0
    else:
        assert rc == 1 and msg in capsys.readouterr().err
        assert not any(tmp_path.iterdir())


def test_a_source_checkout_needs_ref(tmp_path, capsys):
    assert cli.main(["init", "--dir", str(tmp_path), "--profile", "cloudflare-s3"]) == 1
    assert "pass --ref" in capsys.readouterr().err


def test_ref_must_be_a_full_sha(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["init", "--dir", str(tmp_path), "--profile", "cloudflare-s3", "--ref", "v1"])


# ---- the release lookup -----------------------------------------------------------------------

TAG_SHA = "c" * 40
REL_SHA = "d" * 40


def _release(tag_obj=None, parents=(TAG_SHA,), branch=True):
    respx.get(f"{API}/repos/tttao/ayasee/git/ref/tags/v1.2.3").mock(
        return_value=httpx.Response(
            200, json={"object": tag_obj or {"sha": TAG_SHA, "type": "commit"}}
        )
    )
    respx.get(f"{API}/repos/tttao/ayasee/git/ref/heads/release/v1.2.3").mock(
        return_value=httpx.Response(200, json={"object": {"sha": REL_SHA}})
        if branch
        else httpx.Response(404, json={})
    )
    respx.get(f"{API}/repos/tttao/ayasee/git/commits/{REL_SHA}").mock(
        return_value=httpx.Response(200, json={"parents": [{"sha": p} for p in parents]})
    )


@pytest.fixture
def gh():
    client = GitHub(API, "")
    yield client
    client.close()


@respx.mock
def test_release_is_the_branch_head_on_top_of_the_tag(gh):
    _release()
    assert scaffold.resolve_release(gh, "tttao/ayasee", "1.2.3") == REL_SHA
    assert "authorization" not in respx.calls.last.request.headers  # anonymous


@respx.mock
def test_an_annotated_tag_is_followed(gh):
    _release(tag_obj={"sha": "e" * 40, "type": "tag"})
    respx.get(f"{API}/repos/tttao/ayasee/git/tags/{'e' * 40}").mock(
        return_value=httpx.Response(200, json={"object": {"sha": TAG_SHA, "type": "commit"}})
    )
    assert scaffold.resolve_release(gh, "tttao/ayasee", "1.2.3") == REL_SHA


@respx.mock
@pytest.mark.parametrize(
    ("kwargs", "msg"),
    [
        ({"parents": ("f" * 40,)}, "not one commit on top"),
        ({"parents": (TAG_SHA, "f" * 40)}, "not one commit on top"),
        ({"branch": False}, "no branch release/v1.2.3"),
    ],
)
def test_release_refusals(gh, kwargs, msg):
    _release(**kwargs)
    with pytest.raises(scaffold.InitError, match=msg):
        scaffold.resolve_release(gh, "tttao/ayasee", "1.2.3")


@respx.mock
def test_no_tag(gh):
    respx.get(f"{API}/repos/tttao/ayasee/git/ref/tags/v1.2.3").mock(
        return_value=httpx.Response(404, json={})
    )
    with pytest.raises(scaffold.InitError, match="no tag v1.2.3"):
        scaffold.resolve_release(gh, "tttao/ayasee", "1.2.3")


# ---- --ref is verified (issue #32) -------------------------------------------------------------


def _pin(version, tag_sha=TAG_SHA, parents=(TAG_SHA,), init_file=None, head=None):
    text = init_file if init_file is not None else f'__version__ = "{version}"\n'
    respx.get(f"{API}/repos/tttao/ayasee/contents/src/ayasee/__init__.py").mock(
        return_value=httpx.Response(
            200, json={"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}
        )
    )
    respx.get(f"{API}/repos/tttao/ayasee/git/ref/tags/v{version}").mock(
        return_value=httpx.Response(200, json={"object": {"sha": tag_sha, "type": "commit"}})
    )
    respx.get(f"{API}/repos/tttao/ayasee/git/commits/{SHA}").mock(
        return_value=httpx.Response(200, json={"parents": [{"sha": p} for p in parents]})
    )
    respx.get(f"{API}/repos/tttao/ayasee/git/ref/heads/release/v{version}").mock(
        return_value=httpx.Response(200, json={"object": {"sha": head or SHA}})
    )


@respx.mock
def test_ref_of_this_versions_release_commit_is_accepted(tmp_path):
    _pin(scaffold.__version__)
    rc = cli.main(["init", "--dir", str(tmp_path), "--profile", "cloudflare-s3", "--ref", SHA])
    assert rc == 0
    assert f"@{SHA}" in (tmp_path / ".github/workflows/iac-pr.yml").read_text()


@respx.mock
def test_ref_of_another_release_is_refused(tmp_path, capsys):
    # the trap of issue #32: the rc.2 commit under rc.3 templates
    _pin("0.0.1")
    rc = cli.main(["init", "--dir", str(tmp_path), "--profile", "cloudflare-s3", "--ref", SHA])
    err = capsys.readouterr().err
    assert rc == 1 and "release commit of 0.0.1" in err and scaffold.__version__ in err
    assert not any(tmp_path.iterdir())


@respx.mock
@pytest.mark.parametrize(
    ("kwargs", "msg"),
    [
        ({"parents": ("f" * 40,)}, "not the one commit on top of tag"),
        ({"parents": (TAG_SHA, "f" * 40)}, "not the one commit on top of tag"),
        ({"head": "f" * 40}, "not the head of release/v"),
        ({"init_file": "x = 1\n"}, "no src/ayasee/__init__.py with a __version__"),
        ({"init_file": '__version__ = "banana"\n'}, "not a MAJOR.MINOR.PATCH"),
    ],
)
def test_ref_that_is_not_a_release_commit_is_refused(tmp_path, capsys, kwargs, msg):
    _pin(scaffold.__version__, **kwargs)
    rc = cli.main(["init", "--dir", str(tmp_path), "--profile", "cloudflare-s3", "--ref", SHA])
    err = capsys.readouterr().err
    assert rc == 1 and msg in err and "--unverified-ref" in err
    assert not any(tmp_path.iterdir())


@respx.mock
def test_ref_with_no_tag_or_no_file_is_refused(tmp_path, capsys):
    respx.get(f"{API}/repos/tttao/ayasee/contents/src/ayasee/__init__.py").mock(
        return_value=httpx.Response(404, json={})
    )
    args = ["init", "--dir", str(tmp_path), "--profile", "cloudflare-s3", "--ref", SHA]
    assert cli.main(args) == 1
    assert "no src/ayasee/__init__.py" in capsys.readouterr().err
    _pin(scaffold.__version__)
    respx.get(f"{API}/repos/tttao/ayasee/git/ref/tags/v{scaffold.__version__}").mock(
        return_value=httpx.Response(404, json={})
    )
    assert cli.main(args) == 1
    assert "has no tag" in capsys.readouterr().err


@respx.mock
def test_an_api_error_never_verifies_the_ref(tmp_path, capsys):
    respx.get(f"{API}/repos/tttao/ayasee/contents/src/ayasee/__init__.py").mock(
        return_value=httpx.Response(500, json={})
    )
    rc = cli.main(["init", "--dir", str(tmp_path), "--profile", "cloudflare-s3", "--ref", SHA])
    assert rc == 1 and "init: error" in capsys.readouterr().err
    assert not any(tmp_path.iterdir())


def test_unverified_ref_warns_and_makes_no_request(tmp_path, capsys):
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 0  # respx not active: a call would fail
    assert "not verified" in capsys.readouterr().err


# ---- packaging ---------------------------------------------------------------------------------


def test_the_wheel_packages_exactly_the_files_the_profiles_read():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    included = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    templates = {k: v for k, v in included.items() if v.startswith("ayasee/templates/")}
    wanted = {}
    for p in scaffold.PROFILES.values():
        rels = [".ayasee.yaml", "root.hcl"]
        rels += [f".github/workflows/{w}" for w in (*p.workflows, *p.gate_workflows)]
        rels += [f"bootstrap/{b}" for b in p.bootstrap]
        for rel in rels:
            wanted[f"examples/{p.name}/{rel}"] = f"ayasee/templates/{p.name}/{rel}"
    assert templates == wanted
    for src in wanted:
        assert (ROOT / src).is_file(), src


# ---- environment_prefix (issue #31) ------------------------------------------------------


@pytest.mark.parametrize("profile", ["cloudflare-s3", "databricks-azure"])
def test_a_monorepo_gets_the_iac_prefix_in_the_config_and_the_bootstrap(tmp_path, profile):
    assert _init(tmp_path, "--profile", profile, "--iac-root", "infra") == 0
    cfg = parse_config_text((tmp_path / ".ayasee.yaml").read_text())
    assert cfg.environment_prefix == "iac-"
    boot = "".join(p.read_text() for p in (tmp_path / "infra/bootstrap").glob("*.tf"))
    assert re.search(r'variable "environment_prefix" \{[^}]*default\s*=\s*"iac-"', boot, re.S)
    assert re.search(
        r'variable "adopt_existing_environments" \{[^}]*default\s*=\s*false', boot, re.S
    )


@pytest.mark.parametrize("profile", ["cloudflare-s3", "databricks-azure"])
def test_a_repository_root_scaffold_keeps_todays_names(tmp_path, profile):
    assert _init(tmp_path, "--profile", profile) == 0
    assert "environment_prefix" not in (tmp_path / ".ayasee.yaml").read_text()
    assert parse_config_text((tmp_path / ".ayasee.yaml").read_text()).environment_prefix == ""
    boot = "".join(p.read_text() for p in (tmp_path / "bootstrap").glob("*.tf"))
    assert re.search(r'variable "environment_prefix" \{[^}]*default\s*=\s*""', boot, re.S)


def test_the_flag_sets_and_clears_the_prefix(tmp_path):
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--environment-prefix", "ayasee_") == 0
    assert (
        parse_config_text((tmp_path / ".ayasee.yaml").read_text()).environment_prefix == "ayasee_"
    )
    other = tmp_path / "other"
    assert (
        _init(other, "--profile", "cloudflare-s3", "--iac-root", "infra", "--environment-prefix=")
        == 0
    )
    assert "environment_prefix" not in (other / ".ayasee.yaml").read_text()
    boot = (other / "infra/bootstrap/main.tf").read_text()
    assert re.search(r'variable "environment_prefix" \{[^}]*default\s*=\s*""', boot, re.S)


def test_a_bad_prefix_is_refused_and_nothing_is_written(tmp_path, capsys):
    with pytest.raises(SystemExit):
        _init(tmp_path, "--profile", "cloudflare-s3", "--environment-prefix", "Bad Prefix")
    assert not (tmp_path / ".ayasee.yaml").exists()


def test_the_prefixed_scaffold_passes_doctor_offline(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--iac-root", "infra") == 0
    capsys.readouterr()
    (tmp_path / "infra/stages/test").mkdir(parents=True, exist_ok=True)
    assert cli.main(["doctor", "--root", str(tmp_path), "--repo", "acme/estate"]) in (0, 2)
    assert "no other workflow uses iac-* environments" in capsys.readouterr().out


@pytest.mark.integration
@pytest.mark.parametrize("profile", ["cloudflare-s3", "databricks-azure"])
def test_the_prefixed_bootstrap_validates_and_plans_prefixed_environment_names(tmp_path, profile):
    """Scaffold with the default monorepo prefix, point the modules at this checkout, and let tofu
    validate the result (the module sources are pinned to a release in the real output)."""
    import shutil
    import subprocess

    tofu = shutil.which("tofu")
    if not tofu:
        pytest.skip("tofu is not installed")
    assert _init(tmp_path, "--profile", profile, "--iac-root", "infra") == 0
    boot = tmp_path / "infra" / "bootstrap"
    main = boot / "main.tf"
    text = re.sub(
        r"git::https://github\.com/tttao/ayasee\.git//modules/([a-z-]+)\?ref=[0-9a-f]{40}",
        lambda m: str(ROOT / "modules" / m.group(1)),
        main.read_text(),
    )
    main.write_text(text)
    for cmd in ([tofu, "init", "-backend=false", "-input=false"], [tofu, "validate"]):
        done = subprocess.run(cmd, cwd=boot, capture_output=True, text=True, check=False)
        assert done.returncode == 0, done.stdout + done.stderr
    assert 'default     = "iac-"' in "".join(p.read_text() for p in boot.glob("*.tf"))


def test_a_prefixed_init_is_rerunnable_and_upgrade_leaves_the_prefix_alone(tmp_path, capsys):
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--iac-root", "infra") == 0
    capsys.readouterr()
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--iac-root", "infra") == 0  # unchanged
    assert "environment_prefix: iac-" in (tmp_path / ".ayasee.yaml").read_text()
    # --upgrade moves the pin only: it refuses the flag instead of ignoring it
    assert _init(tmp_path, "--upgrade", "--environment-prefix", "other-") == 1
    assert "environment_prefix: iac-" in (tmp_path / ".ayasee.yaml").read_text()


# ---- the GitHub plan (issue #39) ---------------------------------------------------------------

UPGRADE = "Upgrade to GitHub Pro or make this repository public to enable this feature."


def _mock_estate(visibility):
    respx.get(f"{API}/repos/acme/estate").mock(
        return_value=httpx.Response(
            200, json={"visibility": visibility, "owner": {"login": "acme", "type": "Organization"}}
        )
    )
    # code scanning (issue #55): what a private repository without Code Security answers
    respx.get(f"{API}/repos/acme/estate/code-scanning/alerts").mock(
        return_value=httpx.Response(403, json={"message": NOT_ENABLED})
    )


@respx.mock
def test_init_warns_before_scaffolding_a_free_private_repository(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    _mock_estate("private")
    respx.get(f"{API}/orgs/acme").mock(return_value=httpx.Response(200, json={"login": "acme"}))
    respx.get(f"{API}/repos/acme/estate/rulesets").mock(
        return_value=httpx.Response(403, json={"message": UPGRADE})
    )
    rc = _init(tmp_path, "--profile", "cloudflare-s3", "--repo", "acme/estate")
    err = capsys.readouterr().err
    assert rc == 0 and (tmp_path / ".ayasee.yaml").is_file()  # a warning, the adopter decides
    assert "init: warning: a private repository on the GitHub Free plan" in err
    assert "required reviewers on environments" in err and "#40" in err
    assert "HTTP 403" not in err


@respx.mock
def test_init_says_nothing_for_a_public_repository(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    _mock_estate("public")
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--repo", "acme/estate") == 0
    assert "plan" not in capsys.readouterr().err


@respx.mock
def test_init_notes_a_plan_it_cannot_check(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    respx.get(f"{API}/repos/acme/estate").mock(return_value=httpx.Response(404, json={}))
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--repo", "acme/estate") == 0
    err = capsys.readouterr().err
    assert "init: note: cannot confirm" in err and "ayasee doctor" in err


def test_init_without_a_repository_makes_no_plan_request(tmp_path, capsys):
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 0  # respx not active
    err = capsys.readouterr().err
    assert "no repository to check the GitHub plan of" in err and "--gate-mechanism" in err


# ---- the gate chosen from the plan, and the iac-approve caller (issue #40) ---------------------

PUBLIC = PlanSupport(SUPPORTED, "public", evidence=("public",))
ENTERPRISE = PlanSupport(SUPPORTED, "private", "enterprise", evidence=("the organization's plan",))
INFERRED = PlanSupport(SUPPORTED, "private", evidence=("required reviewers are set on prod",))
FREE = PlanSupport(UNSUPPORTED, "private", "free", FREE_MISSING, ("the organization's plan",))
TEAM = PlanSupport(UNSUPPORTED, "private", "team", (REVIEWERS,), ("the organization's plan",))
NOT_SEEN = PlanSupport(UNKNOWN, "private", evidence=("no environment carries required reviewers",))


@pytest.mark.parametrize(
    "support, gate, plan, want",
    [
        # detected, no flag
        (None, None, None, ("environment", "")),
        (PUBLIC, None, None, ("environment", "")),
        (ENTERPRISE, None, None, ("environment", "enterprise")),
        (INFERRED, None, None, ("environment", "")),  # stated, never inferred
        (FREE, None, None, ("workflow", "free")),
        (TEAM, None, None, ("workflow", "team")),
        (NOT_SEEN, None, None, ("environment", "")),  # unknown: never a guess
        # --plan alone
        (None, None, "free", ("workflow", "free")),
        (NOT_SEEN, None, "pro", ("workflow", "pro")),
        (None, None, "enterprise", ("environment", "enterprise")),
        (PUBLIC, None, "team", ("environment", "team")),  # public: the stronger gate stays
        # --gate-mechanism wins over the detection, both ways
        (FREE, "environment", None, ("environment", "free")),
        (PUBLIC, "workflow", None, ("workflow", "")),
        (NOT_SEEN, "workflow", "free", ("workflow", "free")),
    ],
)
def test_the_gate_follows_the_plan_and_the_flags(support, gate, plan, want):
    choice = scaffold.choose_gate(support, gate, plan)
    assert (choice.gate, choice.plan) == want


def _says(choice) -> str:
    return "\n".join(line for _, lines in choice.messages for line in lines)


def test_an_unknown_plan_says_why_and_which_flag_to_pass():
    for support in (None, NOT_SEEN):
        text = _says(scaffold.choose_gate(support, None, None))
        assert "gate_mechanism: environment" in text
        assert "--gate-mechanism workflow --plan" in text and "--plan enterprise" in text


def test_a_choice_against_the_detection_is_warned():
    assert "doctor fails" in _says(scaffold.choose_gate(FREE, "environment", None))
    assert "stronger" in _says(scaffold.choose_gate(PUBLIC, "workflow", None))
    assert "detected" in _says(scaffold.choose_gate(ENTERPRISE, None, "free"))
    assert "refuses" in _says(scaffold.choose_gate(None, "environment", "team"))


def _approve_caller(tmp_path):
    return yaml.safe_load((tmp_path / ".github/workflows/iac-approve.yml").read_text())


@pytest.mark.parametrize("profile", ["cloudflare-s3", "databricks-azure"])
def test_the_workflow_gate_scaffolds_the_approve_caller_and_a_refused_placeholder(
    tmp_path, capsys, profile
):
    assert _init(tmp_path, "--profile", profile, "--plan", "team") == 0
    text = (tmp_path / ".ayasee.yaml").read_text()
    assert "gate_mechanism: workflow" in text and REVIEWER_PLACEHOLDER in text
    with pytest.raises(ConfigError, match="placeholder"):  # fail closed until a person fills it
        parse_config_text(text)
    filled = parse_config_text(text.replace(REVIEWER_PLACEHOLDER, "alice"))
    assert filled.gate_mechanism == "workflow" and filled.reviewers.prod == ["alice"]
    approve = (tmp_path / ".github/workflows/iac-approve.yml").read_text()
    assert f"tttao/ayasee/.github/workflows/iac-approve.yml@{SHA} #" in approve
    assert "OWNER" not in approve and "0" * 40 not in approve
    assert _approve_caller(tmp_path)[True] == {
        "workflow_dispatch": {
            "inputs": {
                "sha": {
                    "description": "Commit SHA from the prod approval issue.",
                    "required": True,
                },
                "manifest-digest": {
                    "description": "manifest-digest from the prod approval issue.",
                    "required": True,
                },
                # a break-glass approval of the workflow gate (#40)
                "incident": {
                    "description": "Only to approve a break-glass request, its incident.",
                    "required": False,
                    "default": "",
                },
            }
        }
    }
    boot = "".join(p.read_text() for p in (tmp_path / "bootstrap").glob("*.tf"))
    assert re.search(r'variable "gate_mechanism" \{[^}]*default\s*=\s*"workflow"', boot, re.S)
    assert re.search(r'variable "private_repository_plan" \{[^}]*default\s*=\s*"team"', boot, re.S)
    out = capsys.readouterr().out
    assert "wrote .github/workflows/iac-approve.yml" in out
    assert "reviewers.prod" in out and "prod_reviewers" not in out


def test_the_approve_caller_holds_exactly_what_the_reusable_jobs_need(tmp_path):
    """A called job cannot hold more than its caller: the union of the jobs of iac-approve."""
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--gate-mechanism", "workflow") == 0
    reusable = yaml.safe_load((ROOT / ".github/workflows/iac-approve.yml").read_text())
    needed: dict[str, str] = {}
    for job in reusable["jobs"].values():
        needed.update(job.get("permissions", {}))
    caller = _approve_caller(tmp_path)
    assert caller["permissions"] == {}
    (job,) = caller["jobs"].values()
    assert job["permissions"] == needed
    call = reusable[True]["workflow_call"]
    # the required inputs, and incident for a break-glass approval (#40)
    required = {k for k, v in call["inputs"].items() if v.get("required")}
    assert set(job["with"]) == required | {"incident"}
    assert set(job["secrets"]) <= set(call["secrets"])
    staged = yaml.safe_load((tmp_path / ".github/workflows/iac-staged.yml").read_text())
    (staged_job,) = staged["jobs"].values()
    assert job["secrets"] == staged_job["secrets"]  # the same secrets as the staged caller


@pytest.mark.parametrize("profile", ["cloudflare-s3", "databricks-azure"])
def test_the_environment_gate_writes_no_approve_caller(tmp_path, profile):
    assert _init(tmp_path, "--profile", profile) == 0
    assert not (tmp_path / ".github/workflows/iac-approve.yml").exists()
    text = (tmp_path / ".ayasee.yaml").read_text()
    assert "gate_mechanism" not in text and "reviewers" not in text
    boot = "".join(p.read_text() for p in (tmp_path / "bootstrap").glob("*.tf"))
    assert re.search(r'variable "gate_mechanism" \{[^}]*default\s*=\s*"environment"', boot, re.S)
    assert re.search(r'variable "private_repository_plan" \{[^}]*default\s*=\s*""', boot, re.S)


def test_doctor_refuses_the_workflow_scaffold_until_a_reviewer_is_named(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--plan", "free") == 0
    capsys.readouterr()
    assert cli.main(["doctor", "--root", str(tmp_path), "--repo", "acme/estate"]) == 1
    assert "placeholder REPLACE_WITH_GITHUB_LOGIN" in capsys.readouterr().out
    cfg = tmp_path / ".ayasee.yaml"
    cfg.write_text(cfg.read_text().replace(REVIEWER_PLACEHOLDER, "alice"))
    cli.main(["doctor", "--root", str(tmp_path), "--repo", "acme/estate"])
    out = capsys.readouterr().out
    assert "no workflow calls iac-approve" not in out and "placeholder" not in out
    assert "0 failed" in out


def test_a_workflow_scaffold_is_rerunnable_and_upgrade_moves_the_approve_pin(tmp_path, capsys):
    args = ("--profile", "cloudflare-s3", "--plan", "free")
    assert _init(tmp_path, *args) == 0
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert _init(tmp_path, *args) == 0
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    # the same repository without the flags: init would choose another gate, so nothing is written
    capsys.readouterr()
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 1
    assert "sets gate_mechanism: workflow, but init would choose" in capsys.readouterr().err
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    other = "c" * 40
    upgrade = ["init", "--dir", str(tmp_path), "--upgrade", "--ref", other, "--unverified-ref"]
    assert cli.main(upgrade) == 1  # the configuration does not load until a reviewer is named
    assert "placeholder" in capsys.readouterr().err
    cfg = tmp_path / ".ayasee.yaml"
    cfg.write_text(cfg.read_text().replace(REVIEWER_PLACEHOLDER, "alice"))
    assert cli.main(upgrade) == 0
    approve = (tmp_path / ".github/workflows/iac-approve.yml").read_text()
    assert f"iac-approve.yml@{other}" in approve


@pytest.mark.parametrize("flag", [("--gate-mechanism", "workflow"), ("--plan", "free")])
def test_upgrade_refuses_the_gate_flags(tmp_path, capsys, flag):
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 0
    capsys.readouterr()
    assert _init(tmp_path, "--upgrade", *flag) == 1
    assert flag[0] in capsys.readouterr().err


@pytest.mark.parametrize("args", [("--gate-mechanism", "github"), ("--plan", "business")])
def test_the_gate_flags_take_known_values(tmp_path, args):
    with pytest.raises(SystemExit):
        _init(tmp_path, "--profile", "cloudflare-s3", *args)
    assert not (tmp_path / ".ayasee.yaml").exists()


def test_databricks_on_free_is_warned_that_it_needs_environment_variables(tmp_path, capsys):
    assert _init(tmp_path, "--profile", "databricks-azure", "--plan", "free") == 0
    err = capsys.readouterr().err
    assert "environment variables" in err and "GitHub Free" in err


@respx.mock
def test_a_detected_free_private_repository_gets_the_workflow_gate(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secretvalue")
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    _mock_estate("private")
    respx.get(f"{API}/orgs/acme").mock(
        return_value=httpx.Response(200, json={"login": "acme", "plan": {"name": "free"}})
    )
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--repo", "acme/estate") == 0
    captured = capsys.readouterr()
    assert "gate_mechanism: workflow" in (tmp_path / ".ayasee.yaml").read_text()
    assert (tmp_path / ".github/workflows/iac-approve.yml").is_file()
    assert "--gate-mechanism environment" in captured.err  # how to undo the choice
    assert "ghp_secretvalue" not in captured.out + captured.err


@respx.mock
def test_a_plan_it_cannot_read_keeps_the_environment_gate(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    respx.get(f"{API}/repos/acme/estate").mock(return_value=httpx.Response(500, json={}))
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--repo", "acme/estate") == 0
    assert "gate_mechanism" not in (tmp_path / ".ayasee.yaml").read_text()
    assert not (tmp_path / ".github/workflows/iac-approve.yml").exists()
    assert "--gate-mechanism workflow --plan" in capsys.readouterr().err


@respx.mock
def test_a_detected_enterprise_plan_is_stated_in_the_bootstrap(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    _mock_estate("private")
    respx.get(f"{API}/orgs/acme").mock(
        return_value=httpx.Response(200, json={"login": "acme", "plan": {"name": "enterprise"}})
    )
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--repo", "acme/estate") == 0
    boot = (tmp_path / "bootstrap/main.tf").read_text()
    assert re.search(
        r'variable "private_repository_plan" \{[^}]*default\s*=\s*"enterprise"', boot, re.S
    )
    assert not (tmp_path / ".github/workflows/iac-approve.yml").exists()


@pytest.mark.integration
@pytest.mark.parametrize(
    "profile, plan",
    [("cloudflare-s3", "free"), ("cloudflare-s3", "team"), ("databricks-azure", "pro")],
)
def test_the_workflow_gate_bootstrap_validates(tmp_path, profile, plan):
    import shutil
    import subprocess

    tofu = shutil.which("tofu")
    if not tofu:
        pytest.skip("tofu is not installed")
    assert _init(tmp_path, "--profile", profile, "--plan", plan) == 0
    boot = tmp_path / "bootstrap"
    main = boot / "main.tf"
    main.write_text(
        re.sub(
            r"git::https://github\.com/tttao/ayasee\.git//modules/([a-z-]+)\?ref=[0-9a-f]{40}",
            lambda m: str(ROOT / "modules" / m.group(1)),
            main.read_text(),
        )
    )
    for cmd in ([tofu, "init", "-backend=false", "-input=false"], [tofu, "validate"]):
        done = subprocess.run(cmd, cwd=boot, capture_output=True, text=True, check=False)
        assert done.returncode == 0, done.stdout + done.stderr


def test_the_documented_approve_caller_is_the_one_init_writes():
    doc = (ROOT / "docs/concepts/staged-run.md").read_text()
    block = re.search(r"```yaml\n# \.github/workflows/iac-approve\.yml\n(.*?)```", doc, re.S)
    assert block
    shipped = (ROOT / "examples/cloudflare-s3/.github/workflows/iac-approve.yml").read_text()
    shipped = re.sub(r"^#[^\n]*\n", "", shipped, flags=re.M).lstrip("\n")
    shipped = re.sub(
        r"OWNER/ayasee/(\S+)@0{40} # [^\n]*",
        r"tttao/ayasee/\1@<release SHA> # ayasee vX.Y.Z (release/vX.Y.Z)",
        shipped,
    )
    assert block.group(1) == shipped


# ---- code scanning (issue #55) -----------------------------------------------------------------

NOT_ENABLED = "Code scanning is not enabled for this repository"
CS_ON = CodeScanning(AVAILABLE, "private", ("GitHub Code Security is enabled",))
CS_OFF = CodeScanning(UNAVAILABLE, "private", ("the code-scanning API says it is not enabled",))
CS_UNKNOWN = CodeScanning(UNKNOWN_CS, "private", ("cannot read the code-scanning API (403)",))
CS_PUBLIC = CodeScanning(AVAILABLE, "public", ("a public repository: code scanning is free",))


@pytest.mark.parametrize(
    "cs, visibility, plan, flag, want",
    [
        (None, None, "", None, True),  # nothing read: the default, doctor checks later
        (CS_PUBLIC, "public", "", None, True),
        (CS_ON, "private", "team", None, True),
        (CS_OFF, "private", "enterprise", None, False),
        (CS_UNKNOWN, "private", "free", None, False),
        (CS_UNKNOWN, "private", "pro", None, False),
        (CS_UNKNOWN, "private", "team", None, False),
        (CS_UNKNOWN, "private", "enterprise", None, True),  # may well have it: keep, note
        (CS_UNKNOWN, "private", "", None, True),
        (None, None, "free", None, False),  # --plan free without a repository: private
        (CS_OFF, "private", "free", True, True),  # the flag wins, with a warning
        (CS_ON, "private", "team", False, False),
    ],
)
def test_code_scanning_follows_the_repository_and_the_flag(cs, visibility, plan, flag, want):
    assert scaffold.choose_code_scanning(cs, visibility, plan, flag).enabled is want


def test_code_scanning_choices_say_why_and_how_to_undo():
    def says(*args):
        return _says(scaffold.choose_code_scanning(*args))

    assert says(CS_PUBLIC, "public", "", None) == ""
    off = says(CS_OFF, "private", "free", None)
    assert "code-scanning: false" in off and "--code-scanning" in off and "artifact" in off
    assert "Free, Pro or Team" in says(CS_UNKNOWN, "private", "team", None)
    assert "--no-code-scanning" in says(CS_UNKNOWN, "private", "", None)
    assert "fails on every pull request" in says(CS_OFF, "private", "free", True)
    assert "Security tab" in says(CS_ON, "private", "team", False)


def _no_upload(tmp_path):
    for name in ("iac-pr.yml", "iac-nightly.yml"):
        doc = yaml.safe_load((tmp_path / ".github/workflows" / name).read_text())
        job = next(iter(doc["jobs"].values()))
        assert job["with"]["code-scanning"] is False, name
        # the caller still grants it: a nested job may not ask for more, run or not
        assert job["permissions"]["security-events"] == "write"


@pytest.mark.parametrize("profile", ["cloudflare-s3", "databricks-azure"])
def test_no_code_scanning_writes_the_input_into_both_callers(tmp_path, profile):
    assert _init(tmp_path, "--profile", profile, "--no-code-scanning") == 0
    _no_upload(tmp_path)
    for name in ("iac-staged.yml", "iac-breakglass.yml"):
        assert "code-scanning" not in (tmp_path / ".github/workflows" / name).read_text()


def test_the_default_scaffold_uploads(tmp_path):
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 0
    for name in ("iac-pr.yml", "iac-nightly.yml"):
        assert "code-scanning" not in (tmp_path / ".github/workflows" / name).read_text()


@respx.mock
def test_a_free_private_repository_gets_no_upload(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secretvalue")
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    _mock_estate("private")
    respx.get(f"{API}/orgs/acme").mock(
        return_value=httpx.Response(200, json={"login": "acme", "plan": {"name": "free"}})
    )
    respx.get(f"{API}/repos/acme/estate/code-scanning/alerts").mock(
        return_value=httpx.Response(403, json={"message": NOT_ENABLED})
    )
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--repo", "acme/estate") == 0
    _no_upload(tmp_path)
    err = capsys.readouterr().err
    assert "code-scanning: false" in err and "ghp_secretvalue" not in err


@respx.mock
def test_a_public_repository_uploads(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    _mock_estate("public")
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--repo", "acme/estate") == 0
    assert "code-scanning" not in (tmp_path / ".github/workflows/iac-pr.yml").read_text()


def test_the_no_upload_scaffold_passes_doctor_offline(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--no-code-scanning") == 0
    capsys.readouterr()
    cli.main(["doctor", "--root", str(tmp_path), "--repo", "acme/estate"])
    out = capsys.readouterr().out
    assert "0 failed" in out and "code-scanning: false" in out


def test_upgrade_keeps_the_input_and_refuses_the_flag(tmp_path, capsys):
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--no-code-scanning") == 0
    assert _upgrade(tmp_path) == 0
    _no_upload(tmp_path)
    for flag in ("--code-scanning", "--no-code-scanning"):
        assert _upgrade(tmp_path, flag) == 1
        assert "--upgrade only moves the release pin" in capsys.readouterr().err
