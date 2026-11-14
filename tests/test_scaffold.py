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
from ayasee.config import parse_config_text
from ayasee.github import GitHub

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
    (tmp_path / ".ayasee.yaml").write_text("stages: [test, prod]\nmine: 1\n")
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 1
    err = capsys.readouterr().err
    assert "exist and differ" in err and ".ayasee.yaml" in err
    assert "key only in the scaffold: units_root" in err
    assert "key only in your file (--force drops it): mine" in err
    assert "--- a/.ayasee.yaml (yours)" in err and "+++ b/.ayasee.yaml (scaffold)" in err
    assert (tmp_path / ".ayasee.yaml").read_text() == "stages: [test, prod]\nmine: 1\n"
    assert not (tmp_path / ".github").exists()  # nothing written
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--force") == 0
    assert "mine" not in (tmp_path / ".ayasee.yaml").read_text()


def test_init_can_run_twice(tmp_path, capsys):
    args = ("--profile", "cloudflare-s3", "--iac-root", "infra/iac")
    assert _init(tmp_path, *args) == 0
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    capsys.readouterr()
    assert _init(tmp_path, *args) == 0
    out = capsys.readouterr().out
    assert "wrote " not in out and "unchanged .ayasee.yaml" in out
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
        rels += [f".github/workflows/{w}" for w in p.workflows]
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
