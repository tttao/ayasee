import json

import pytest

from ayasee import cli, config
from ayasee.config import Config, ConfigError, load_config


def write(tmp_path, text):
    p = tmp_path / ".ayasee.yaml"
    p.write_text(text)
    return p


def test_defaults_when_file_missing(tmp_path, capsys):
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg == Config()
    assert cfg.stages == ["dev", "uat", "prod"]
    assert (cfg.engine, cfg.terragrunt, cfg.units_root, cfg.gate) == (
        "opentofu",
        True,
        "stages",
        "always",
    )
    assert cfg.solo_mode is False and cfg.ledger.url is None and cfg.notify.teams is True
    assert (cfg.extra_rules, cfg.drift_noise) == ([], {})
    assert capsys.readouterr().err == ""


def test_empty_file_is_defaults(tmp_path):
    assert load_config(write(tmp_path, "")) == Config()


def test_defaults_are_not_shared():
    a, b = Config(), Config()
    a.stages.append("x")
    a.ledger.url = "u"
    assert b.stages == ["dev", "uat", "prod"] and b.ledger.url is None


def test_solo_mode_warns_exact_text(tmp_path, capsys):
    cfg = load_config(write(tmp_path, "solo_mode: true\n"))
    assert cfg.solo_mode is True
    assert capsys.readouterr().err == (
        "NON-COMPLIANT: solo_mode disables independent approval "
        "(DORA RTS 2024/1774 Art. 17(1)(b))\n"
    )
    load_config(write(tmp_path, "solo_mode: false\n"))
    assert capsys.readouterr().err == ""


def test_full_example(tmp_path):
    cfg = load_config(
        write(
            tmp_path,
            """
stages: [dev, prod]
engine: terraform
terragrunt: false
units_root: live
ledger: {url: "file:///tmp/l"}
gate: risky
extra_rules: [a.yaml]
drift_noise:
  databricks_job: [email_notifications, "tags.x"]
notify: {teams: false}
""",
        )
    )
    assert cfg.stages == ["dev", "prod"] and cfg.gate == "risky"
    assert cfg.ledger.url == "file:///tmp/l" and cfg.notify.teams is False
    assert cfg.drift_noise == {"databricks_job": ["email_notifications", "tags.x"]}


def test_terraform_engine_selects_d1b(tmp_path):
    assert load_config(write(tmp_path, "engine: terraform\n")).binding_mode == "replan"
    assert Config().binding_mode == "encrypted_plan"
    with pytest.raises(AttributeError):
        Config().binding_mode = "x"


@pytest.mark.parametrize(
    "text",
    [
        "bogus: 1",
        "ledger: {bogus: 1}",
        "notify: {bogus: 1}",
        "binding_mode: replan",
        "engine: pulumi",
        "gate: never",
        "terragrunt: yes please",
        "terragrunt: 1",
        "solo_mode: 'true'",
        "units_root: 3",
        "stages: []",
        "stages: [dev, dev, prod]",
        "stages: [dev, uat]",
        "stages: prod",
        "stages: [dev, 1, prod]",
        "ledger: x",
        "ledger: {url: 3}",
        "extra_rules: a.yaml",
        "drift_noise: [a]",
        "drift_noise: {t: a}",
        "drift_noise: {t: [1]}",
        "notify: {teams: maybe}",
        "- a",
        "stages: [unclosed",
    ],
)
def test_invalid_rejected(tmp_path, text):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, text + "\n"))


def test_unknown_key_rejected_names_key(tmp_path):
    with pytest.raises(ConfigError, match="bogus"):
        load_config(write(tmp_path, "bogus: 1\n"))


def _cli(monkeypatch, *argv):
    monkeypatch.setattr(cli, "_COMMANDS", {"config": config._setup})
    return cli.main(["config", *argv])


def test_cli_show_prints_json(tmp_path, monkeypatch, capsys):
    p = write(tmp_path, "engine: terraform\n")
    assert _cli(monkeypatch, "show", "--path", str(p)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["binding_mode"] == "replan" and out["engine"] == "terraform"
    assert out["ledger"] == {"url": None} and out["notify"] == {"teams": True}


def test_cli_show_error_exit_1(tmp_path, monkeypatch, capsys):
    p = write(tmp_path, "bogus: 1\n")
    assert _cli(monkeypatch, "show", "--path", str(p)) == 1
    cap = capsys.readouterr()
    assert cap.out == "" and "bogus" in cap.err and "Traceback" not in cap.err


def test_allow_cascade_defaults_false_and_is_strict(tmp_path):
    assert load_config(write(tmp_path, "")).allow_cascade is False
    assert load_config(write(tmp_path, "allow_cascade: true\n")).allow_cascade is True
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, "allow_cascade: yes please\n"))


# ---- check-supported (R35, R73) ---------------------------------------------------------


def test_check_supported_accepts_the_defaults_and_the_example(tmp_path, capsys):
    assert cli.main(["config", "check-supported", "--path", str(tmp_path / "none.yaml")]) == 0
    p = write(
        tmp_path, "engine: opentofu\nterragrunt: true\nextra_rules: []\nnotify:\n  teams: true\n"
    )
    assert cli.main(["config", "check-supported", "--path", str(p)]) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("gate: risky", "gate: risky is not supported yet"),
        ("engine: terraform", "engine: not supported yet"),
        ("terragrunt: false", "terragrunt: not supported yet"),
        ("extra_rules: [rules/extra.yaml]", "extra_rules: not supported yet"),
        ("notify:\n  teams: false", "notify.teams: not supported yet"),
    ],
)
def test_check_supported_refuses_each_unsupported_key(tmp_path, capsys, text, message):
    p = write(tmp_path, text + "\n")
    assert cli.main(["config", "check-supported", "--path", str(p)]) == 1
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 1 and err[0].startswith(message)


def test_check_supported_lists_every_problem(tmp_path, capsys):
    p = write(tmp_path, "engine: terraform\nterragrunt: false\nextra_rules: [x]\n")
    assert cli.main(["config", "check-supported", "--path", str(p)]) == 1
    keys = [line.split(":")[0] for line in capsys.readouterr().err.splitlines()]
    assert keys == ["engine", "terragrunt", "extra_rules"]


def test_check_supported_invalid_config_exit_1(tmp_path, capsys):
    p = write(tmp_path, "bogus: 1\n")
    assert cli.main(["config", "check-supported", "--path", str(p)]) == 1
    assert "config error" in capsys.readouterr().err


def test_unsupported_is_empty_for_defaults():
    assert config.unsupported(Config()) == []


# ---- identity profile (issue #1) --------------------------------------------------------


def test_identity_defaults_to_oidc_and_is_strict(tmp_path, capsys):
    assert Config().identity == "oidc"
    assert load_config(write(tmp_path, "identity: oidc\n")).identity == "oidc"
    assert capsys.readouterr().err == ""
    with pytest.raises(ConfigError, match="identity"):
        load_config(write(tmp_path, "identity: keys\n"))


def test_identity_secrets_prints_the_deviation(tmp_path, capsys):
    cfg = load_config(write(tmp_path, "identity: secrets\n"))
    assert cfg.identity == "secrets"
    assert capsys.readouterr().err == config.DEVIATION_SECRETS + "\n"
    assert config.DEVIATION_SECRETS.startswith("DEVIATION: identity: secrets")


def test_cli_show_has_identity(tmp_path, monkeypatch, capsys):
    p = write(tmp_path, "identity: secrets\n")
    assert _cli(monkeypatch, "show", "--path", str(p)) == 0
    assert json.loads(capsys.readouterr().out)["identity"] == "secrets"


# ---- iac-staged stage layout (issue #1) -------------------------------------------------


def _layout(run_dev, run_uat, first, pre):
    return {
        "run-dev": run_dev,
        "run-uat": run_uat,
        "first-stage": first,
        "pre-prod-stage": pre,
    }


@pytest.mark.parametrize(
    ("stages", "start", "expected"),
    [
        (["dev", "uat", "prod"], "", _layout("true", "true", "dev", "uat")),
        (["dev", "uat", "prod"], "dev", _layout("true", "true", "dev", "uat")),
        (["dev", "uat", "prod"], "uat", _layout("false", "true", "dev", "uat")),
        (["dev", "uat", "prod"], "prod-plan", _layout("false", "false", "dev", "uat")),
        (["staging", "prod"], "", _layout("false", "true", "", "staging")),
        (["staging", "prod"], "staging", _layout("false", "true", "", "staging")),
        (["staging", "prod"], "prod-plan", _layout("false", "false", "", "staging")),
        (["test", "pre_prod", "prod"], "", _layout("true", "true", "test", "pre_prod")),
    ],
)
def test_staged_layout(stages, start, expected):
    out = config.staged_layout(stages, start)
    assert out == {**expected, "order": ",".join(stages)}


@pytest.mark.parametrize(
    ("stages", "start", "match"),
    [
        (["prod"], "", "two or three stages"),
        (["a", "b", "c", "prod"], "", "two or three stages"),
        (["dev", "prod", "uat"], "", "last"),
        (["prod-plan", "prod"], "", "reserved"),
        (["prod-breakglass", "prod"], "", "reserved"),
        (["dev-plan", "prod"], "", "reserved"),
        (["drift-dev", "prod"], "", "reserved"),
        (["Dev", "prod"], "", "stage name"),
        (["../x", "prod"], "", "stage name"),
        (["dev x", "prod"], "", "stage name"),
        (["dev", "uat", "prod"], "prod", "start-stage"),
        (["dev", "uat", "prod"], "staging", "start-stage"),
        (["staging", "prod"], "dev", "start-stage"),
    ],
)
def test_staged_layout_refuses(stages, start, match):
    with pytest.raises(ConfigError, match=match):
        config.staged_layout(stages, start)


def test_cli_staged_prints_github_outputs(tmp_path, monkeypatch, capsys):
    p = write(tmp_path, "stages: [staging, prod]\n")
    assert _cli(monkeypatch, "staged", "--path", str(p), "--start-stage", "") == 0
    assert capsys.readouterr().out.splitlines() == [
        "run-dev=false",
        "run-uat=true",
        "first-stage=",
        "pre-prod-stage=staging",
        "order=staging,prod",
    ]


def test_cli_staged_refuses_with_exit_1_and_no_output(tmp_path, monkeypatch, capsys):
    p = write(tmp_path, "stages: [a, b, c, prod]\n")
    assert _cli(monkeypatch, "staged", "--path", str(p), "--start-stage", "") == 1
    cap = capsys.readouterr()
    assert cap.out == "" and "two or three stages" in cap.err


# ---- plan_encryption.key_provider (issue #1) --------------------------------------------


def test_key_provider_defaults_to_azure_vault():
    assert Config().plan_encryption.key_provider == "azure_vault"


@pytest.mark.parametrize("provider", ["azure_vault", "pbkdf2"])
def test_key_provider_accepts_the_wired_providers(tmp_path, provider):
    cfg = load_config(write(tmp_path, f"plan_encryption:\n  key_provider: {provider}\n"))
    assert cfg.plan_encryption.key_provider == provider


@pytest.mark.parametrize(
    ("iac_root", "units_root", "want"),
    [
        (None, None, (".", "stages")),
        ("infra/iac", "infra/iac/stages", ("infra/iac", "infra/iac/stages")),
        ("infra/iac/", "infra/iac/stages/", ("infra/iac", "infra/iac/stages")),
        ("./infra/iac", "infra/iac/live", ("infra/iac", "infra/iac/live")),
        (".", "infra/iac/stages", (".", "infra/iac/stages")),
    ],
)
def test_iac_root_is_normalised(tmp_path, iac_root, units_root, want):
    lines = []
    if iac_root is not None:
        lines.append(f"iac_root: {iac_root}")
    if units_root is not None:
        lines.append(f"units_root: {units_root}")
    cfg = load_config(write(tmp_path, "\n".join(lines) + "\n"))
    assert (cfg.iac_root, cfg.units_root) == want


@pytest.mark.parametrize(
    "text",
    [
        "plan_encryption: pbkdf2",
        "plan_encryption: {key_provider: openbao}",
        "plan_encryption: {key_provider: aws_kms}",
        "plan_encryption: {key_provider: 3}",
        "plan_encryption: {passphrase: x}",
    ],
)
def test_key_provider_rejects_anything_else(tmp_path, text):
    with pytest.raises(ConfigError, match="plan_encryption"):
        load_config(write(tmp_path, text + "\n"))


def test_cli_show_has_the_key_provider(tmp_path, monkeypatch, capsys):
    p = write(tmp_path, "plan_encryption: {key_provider: pbkdf2}\n")
    assert _cli(monkeypatch, "show", "--path", str(p)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["plan_encryption"] == {"key_provider": "pbkdf2"}


# ---- labels: the deviations a run summary shows (issue #1) ------------------------------


def test_labels_are_empty_for_the_defaults():
    assert config.labels(Config()) == []


def test_labels_name_each_deviation():
    cfg = Config(solo_mode=True, identity="secrets")
    assert config.labels(cfg) == [config.NON_COMPLIANT, config.DEVIATION_SECRETS]
    assert config.labels(Config(identity="secrets")) == [config.DEVIATION_SECRETS]


def test_cli_labels_prints_one_line_each_on_stdout(tmp_path, monkeypatch, capsys):
    p = write(tmp_path, "identity: secrets\nsolo_mode: true\n")
    assert _cli(monkeypatch, "labels", "--path", str(p)) == 0
    assert capsys.readouterr().out.splitlines() == [
        config.NON_COMPLIANT,
        config.DEVIATION_SECRETS,
    ]
    p = write(tmp_path, "")
    assert _cli(monkeypatch, "labels", "--path", str(p)) == 0
    assert capsys.readouterr().out == ""


def test_cli_labels_invalid_config_exit_1(tmp_path, monkeypatch, capsys):
    p = write(tmp_path, "identity: maybe\n")
    assert _cli(monkeypatch, "labels", "--path", str(p)) == 1
    assert "config error" in capsys.readouterr().err


@pytest.mark.parametrize(
    "text",
    [
        "iac_root: 3",
        "iac_root: ''",
        "iac_root: /infra",
        "iac_root: ../elsewhere",
        "iac_root: infra/../../x",
        "units_root: /abs",
        "units_root: ../x",
        # units_root must sit inside iac_root
        "iac_root: infra/iac",  # default units_root 'stages' is outside it
        "{iac_root: infra/iac, units_root: infra/iacx/stages}",
        "{iac_root: infra/iac, units_root: infra/iac}",
    ],
)
def test_invalid_iac_root_rejected(tmp_path, text):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, text + "\n"))
