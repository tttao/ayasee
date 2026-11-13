"""`ayasee init` (issue #11): a profile scaffold pinned to one release commit, no placeholder."""

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
    return cli.main(["init", "--dir", str(tmp_path), "--ref", SHA, *args])


def test_cloudflare_in_a_monorepo(tmp_path, capsys):
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--iac-root", "infra/iac") == 0
    cfg = parse_config_text((tmp_path / ".ayasee.yaml").read_text())
    assert cfg.stages == ["test", "prod"]
    assert (cfg.iac_root, cfg.units_root) == ("infra/iac", "infra/iac/stages")
    assert cfg.shared_units == [] and cfg.identity == "secrets"
    for stage in ("test", "prod"):
        assert (tmp_path / f"infra/iac/stages/{stage}/.gitkeep").is_file()
    assert (tmp_path / "infra/iac/root.hcl").is_file()
    main = yaml.safe_load((tmp_path / ".github/workflows/main.yml").read_text())
    assert main[True]["push"]["paths"] == ["infra/iac/**", ".ayasee.yaml", ".github/**"]
    for name in ("pr", "main", "nightly", "breakglass"):
        text = (tmp_path / f".github/workflows/{name}.yml").read_text()
        assert "tttao/ayasee/.github/workflows/iac-" in text and f"@{SHA} #" in text
        assert "OWNER" not in text and "0" * 40 not in text
    pr = yaml.safe_load((tmp_path / ".github/workflows/pr.yml").read_text())
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
    main = yaml.safe_load((tmp_path / ".github/workflows/main.yml").read_text())
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
    text = (tmp_path / ".github/workflows/main.yml").read_text()
    assert f"acme/ayasee-mirror/.github/workflows/iac-staged.yml@{SHA}" in text


def test_the_scaffold_passes_doctor_offline(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--iac-root", "infra") == 0
    capsys.readouterr()
    assert cli.main(["doctor", "--root", str(tmp_path), "--repo", "acme/estate"]) == 2
    out = capsys.readouterr().out
    assert "0 failed" in out


def test_existing_files_are_kept(tmp_path, capsys):
    (tmp_path / ".ayasee.yaml").write_text("mine\n")
    assert _init(tmp_path, "--profile", "cloudflare-s3") == 1
    err = capsys.readouterr().err
    assert "these files exist" in err and ".ayasee.yaml" in err
    assert (tmp_path / ".ayasee.yaml").read_text() == "mine\n"
    assert not (tmp_path / ".github").exists()  # nothing written
    assert _init(tmp_path, "--profile", "cloudflare-s3", "--force") == 0
    assert (tmp_path / ".ayasee.yaml").read_text() != "mine\n"


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
