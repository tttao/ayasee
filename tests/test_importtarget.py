"""`ayasee import-target` (issue #58): the stage and unit of an iac-import run, checked against
the configuration before any environment or credential is used."""

import pytest

from ayasee import cli, importtarget  # noqa: F401  (registers the subcommand)

CONFIG = """\
stages: [test, prod]
units_root: stages
identity: secrets
provider_env: [TF_VAR_extra]
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ayasee.yaml").write_text(CONFIG)
    for unit in ("stages/test/dns", "stages/test/net/edge", "stages/prod/dns"):
        (tmp_path / unit).mkdir(parents=True)
        (tmp_path / unit / "terragrunt.hcl").write_text("")
    (tmp_path / "stages/test/empty").mkdir()
    return tmp_path


def _target(capsys, stage="", unit="", path=".ayasee.yaml"):
    code = cli.main(["import-target", "--path", path, "--stage", stage, "--unit", unit])
    out, err = capsys.readouterr()
    lines = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return code, lines, err


def test_one_unit_of_the_first_stage(repo, capsys):
    code, out, _ = _target(capsys, "test", "dns")
    assert code == 0
    assert out == {
        "environment": "test-plan",
        "stage": "test",
        "units-root": "stages",
        "engine": "opentofu",
        "identity": "secrets",
        "provider-env-names": "TF_VAR_extra",
        "only-units": "dns",
    }


def test_empty_stage_is_the_first_and_empty_unit_is_every_unit(repo, capsys):
    code, out, _ = _target(capsys)
    assert code == 0
    assert out["stage"] == "test" and out["only-units"] == ""


def test_nested_unit_gets_its_plan_name(repo, capsys):
    code, out, _ = _target(capsys, "test", "net/edge")
    assert code == 0 and out["only-units"] == "net__edge"


def test_environment_prefix_is_kept(repo, capsys):
    (repo / ".ayasee.yaml").write_text(CONFIG + "environment_prefix: iac-\n")
    code, out, _ = _target(capsys, "test", "dns")
    assert code == 0 and out["environment"] == "iac-test-plan"


@pytest.mark.parametrize("stage", ["prod", "uat", "test-plan", "prod-plan"])
def test_any_other_stage_is_refused_with_the_local_cli_named(repo, capsys, stage):
    code, out, err = _target(capsys, stage, "dns")
    assert code == 1 and out == {}
    assert "first stage (test)" in err and "ayasee import-plan" in err


@pytest.mark.parametrize(
    "unit",
    [
        "../prod/dns",
        "dns/..",
        "/etc",
        "dns/",
        ".hidden",
        "a b",
        "dns\nx=y",
        "a__b",
        "empty",  # no terragrunt.hcl
        "missing",
    ],
)
def test_unsafe_or_missing_unit_is_refused(repo, capsys, unit):
    code, out, err = _target(capsys, "test", unit)
    assert code == 1 and out == {}
    assert "unit" in err


def test_bad_configuration_is_refused(repo, capsys):
    (repo / ".ayasee.yaml").write_text("stages: [prod]\n")
    code, out, err = _target(capsys)
    assert code == 1 and out == {}
    assert "config error" in err
