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
    # issue #11: say that a stage is isolated only as far as its tokens' scopes are
    assert "stages are isolated only as far as the token scopes are" in config.DEVIATION_SECRETS


def test_cli_show_has_identity(tmp_path, monkeypatch, capsys):
    p = write(tmp_path, "identity: secrets\n")
    assert _cli(monkeypatch, "show", "--path", str(p)) == 0
    assert json.loads(capsys.readouterr().out)["identity"] == "secrets"


# ---- iac-staged stage layout (issue #1) -------------------------------------------------


def _layout(runs, names):
    """Expected outputs: ``runs`` and ``names`` list the four pre-prod slots, stage-1 first."""
    out = {f"run-stage-{i}": "true" if r else "false" for i, r in enumerate(runs, 1)}
    out.update({f"stage-{i}": n for i, n in enumerate(names, 1)})
    return out


@pytest.mark.parametrize(
    ("stages", "start", "expected"),
    [
        (["dev", "uat", "prod"], "", _layout([0, 0, 1, 1], ["", "", "dev", "uat"])),
        (["dev", "uat", "prod"], "dev", _layout([0, 0, 1, 1], ["", "", "dev", "uat"])),
        (["dev", "uat", "prod"], "uat", _layout([0, 0, 0, 1], ["", "", "dev", "uat"])),
        (["dev", "uat", "prod"], "prod-plan", _layout([0, 0, 0, 0], ["", "", "dev", "uat"])),
        (["staging", "prod"], "", _layout([0, 0, 0, 1], ["", "", "", "staging"])),
        (["staging", "prod"], "staging", _layout([0, 0, 0, 1], ["", "", "", "staging"])),
        (["staging", "prod"], "prod-plan", _layout([0, 0, 0, 0], ["", "", "", "staging"])),
        (["test", "pre_prod", "prod"], "", _layout([0, 0, 1, 1], ["", "", "test", "pre_prod"])),
        (["a", "b", "c", "prod"], "", _layout([0, 1, 1, 1], ["", "a", "b", "c"])),
        (["a", "b", "c", "prod"], "b", _layout([0, 0, 1, 1], ["", "a", "b", "c"])),
        (["a", "b", "c", "d", "prod"], "", _layout([1, 1, 1, 1], ["a", "b", "c", "d"])),
        (["a", "b", "c", "d", "prod"], "a", _layout([1, 1, 1, 1], ["a", "b", "c", "d"])),
        (["a", "b", "c", "d", "prod"], "c", _layout([0, 0, 1, 1], ["a", "b", "c", "d"])),
        (["a", "b", "c", "d", "prod"], "d", _layout([0, 0, 0, 1], ["a", "b", "c", "d"])),
        (["a", "b", "c", "d", "prod"], "prod-plan", _layout([0, 0, 0, 0], ["a", "b", "c", "d"])),
    ],
)
def test_staged_layout(stages, start, expected):
    out = config.staged_layout(stages, start)
    assert out == {**expected, "order": ",".join(stages)}


@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_staged_layout_slots_are_right_aligned_and_skips_are_a_prefix(n):
    """The last slot always runs the stage just before prod; skipped slots precede running ones.

    The workflow's skip rule (a skipped slot passes only when ``run-stage-<i>`` is false) relies
    on this: a slot that runs never follows one that was skipped because it failed upstream.
    """
    pre = [f"s{i}" for i in range(n)]
    for start in ["", *pre, "prod-plan"]:
        out = config.staged_layout([*pre, "prod"], start)
        names = [out[f"stage-{i}"] for i in range(1, config.PRE_PROD_SLOTS + 1)]
        runs = [out[f"run-stage-{i}"] == "true" for i in range(1, config.PRE_PROD_SLOTS + 1)]
        assert names == [""] * (config.PRE_PROD_SLOTS - n) + pre
        assert runs == sorted(runs), (start, runs)  # false..false, true..true
        assert all(names[i] for i, r in enumerate(runs) if r)  # a running slot has a stage
        first = names.index(start or pre[0]) if start != "prod-plan" else len(names)
        assert runs == [i >= first for i in range(len(names))]


@pytest.mark.parametrize(
    ("stages", "start", "match"),
    [
        (["prod"], "", "2 to 5 stages"),
        (["a", "b", "c", "d", "e", "prod"], "", "2 to 5 stages"),
        (["dev", "prod", "uat"], "", "last"),
        (["prod-plan", "prod"], "", "reserved"),
        (["prod-breakglass", "prod"], "", "reserved"),
        (["dev-plan", "prod"], "", "reserved"),
        (["prod-pr-plan", "prod"], "", "reserved"),
        # <stage>-plan is the pull-request plan environment of a first stage (issue #11)
        (["test", "test-plan", "prod"], "", "reserved"),
        (["drift-dev", "prod"], "", "reserved"),
        (["Dev", "prod"], "", "stage name"),
        (["../x", "prod"], "", "stage name"),
        (["dev x", "prod"], "", "stage name"),
        (["dev", "uat", "prod"], "prod", "start-stage"),
        (["dev", "uat", "prod"], "staging", "start-stage"),
        (["staging", "prod"], "dev", "start-stage"),
        (["a", "b", "c", "d", "prod"], "", None),
    ],
)
def test_staged_layout_refuses(stages, start, match):
    if match is None:  # the longest supported list, as a boundary check
        config.staged_layout(stages, start)
        return
    with pytest.raises(ConfigError, match=match):
        config.staged_layout(stages, start)


def test_cli_staged_prints_github_outputs(tmp_path, monkeypatch, capsys):
    p = write(tmp_path, "stages: [staging, prod]\n")
    assert _cli(monkeypatch, "staged", "--path", str(p), "--start-stage", "") == 0
    assert capsys.readouterr().out.splitlines() == [
        "run-stage-1=false",
        "run-stage-2=false",
        "run-stage-3=false",
        "run-stage-4=true",
        "stage-1=",
        "stage-2=",
        "stage-3=",
        "stage-4=staging",
        "order=staging,prod",
    ]


def test_cli_staged_refuses_with_exit_1_and_no_output(tmp_path, monkeypatch, capsys):
    p = write(tmp_path, "stages: [a, b, c, d, e, prod]\n")
    assert _cli(monkeypatch, "staged", "--path", str(p), "--start-stage", "") == 1
    cap = capsys.readouterr()
    assert cap.out == "" and "2 to 5 stages" in cap.err


# ---- pull-request plan environment (issue #11) ------------------------------------------


@pytest.mark.parametrize(
    ("stages", "expected"),
    [
        (["dev", "uat", "prod"], "dev-plan"),
        (["test", "prod"], "test-plan"),
        (["staging", "pre_prod", "prod"], "staging-plan"),
    ],
)
def test_pr_plan_environment_is_first_stage_plan(stages, expected):
    assert config.pr_plan_environment(stages) == expected


@pytest.mark.parametrize(
    ("stages", "match"),
    [
        # a pull request never plans prod in a pull-request environment
        (["prod"], "pre-prod"),
        (["prod", "dev"], "pre-prod"),
        (["Test", "prod"], "stage name"),
        (["../x", "prod"], "stage name"),
        (["drift-dev", "prod"], "reserved"),
        (["prod-pr", "prod"], "reserved"),
        (["x-plan", "prod"], "reserved"),
        (["dev", "dev-plan", "prod"], "reserved"),
        (["prod-breakglass", "prod"], "reserved"),
    ],
)
def test_pr_plan_environment_refuses(stages, match):
    with pytest.raises(ConfigError, match=match):
        config.pr_plan_environment(stages)


def test_cli_pr_plan_environment(tmp_path, monkeypatch, capsys):
    p = write(tmp_path, "stages: [test, prod]\n")
    assert _cli(monkeypatch, "pr-plan-environment", "--path", str(p)) == 0
    assert capsys.readouterr().out == "test-plan\n"


def test_cli_pr_plan_environment_refuses_with_exit_1_and_no_output(tmp_path, monkeypatch, capsys):
    p = write(tmp_path, "stages: [prod]\n")
    assert _cli(monkeypatch, "pr-plan-environment", "--path", str(p)) == 1
    cap = capsys.readouterr()
    assert cap.out == "" and "pre-prod" in cap.err


# ---- plan_encryption.key_provider (issue #1) --------------------------------------------


def test_key_provider_defaults_to_azure_vault():
    assert Config().plan_encryption.key_provider == "azure_vault"


@pytest.mark.parametrize("provider", ["azure_vault", "pbkdf2", "aws_kms", "gcp_kms", "openbao"])
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
        "plan_encryption: {key_provider: vault}",
        "plan_encryption: {key_provider: OPENBAO}",
        "plan_encryption: {key_provider: gcp-kms}",
        "plan_encryption: {key_provider: AWS_KMS}",
        "plan_encryption: {key_provider: 3}",
        "plan_encryption: {passphrase: x}",
    ],
)
def test_key_provider_rejects_anything_else(tmp_path, text):
    with pytest.raises(ConfigError, match="plan_encryption"):
        load_config(write(tmp_path, text + "\n"))


@pytest.mark.parametrize("provider", ["aws_kms", "gcp_kms", "openbao"])
@pytest.mark.parametrize("identity", ["oidc", "secrets"])
def test_kms_providers_are_supported_with_either_identity(tmp_path, identity, provider):
    """issue #10: each uses its own prod-only credential, not the identity's credentials."""
    text = f"identity: {identity}\nplan_encryption: {{key_provider: {provider}}}\n"
    assert config.unsupported(load_config(write(tmp_path, text))) == []


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


@pytest.mark.parametrize(
    "text",
    [
        "iac_root: infra/iac",  # the default units_root, 'stages', is read from the repository root
        "{iac_root: infra/iac, units_root: stages}",
        "{iac_root: infra/iac/, units_root: ./stages/}",
    ],
)
def test_units_root_written_relative_to_iac_root_names_the_fix(tmp_path, text):
    # issue #11: units_root is relative to the repository root; the error says what to write
    with pytest.raises(ConfigError) as err:
        load_config(write(tmp_path, text + "\n"))
    message = str(err.value)
    assert "relative to the repository root, not to iac_root" in message
    assert "if it was written relative to iac_root, write units_root: infra/iac/stages" in message


@pytest.mark.parametrize(
    "text",
    [
        "{iac_root: infra/iac, units_root: infra/iac}",
        "{iac_root: infra/iac, units_root: infra/iacx/stages}",
        "{iac_root: infra/iac, units_root: infra}",
        "{iac_root: infra/iac, units_root: .}",
    ],
)
def test_units_root_outside_iac_root_has_no_hint_when_joining_does_not_help(tmp_path, text):
    # these are already written from the repository root; prefixing iac_root would be wrong
    with pytest.raises(ConfigError) as err:
        load_config(write(tmp_path, text + "\n"))
    assert "not to iac_root" not in str(err.value)


def test_cli_config_check_supported_refuses_units_root_outside_iac_root(
    tmp_path, monkeypatch, capsys
):
    # the first job of every reusable workflow runs this command
    p = write(tmp_path, "iac_root: infra/iac\n")
    assert _cli(monkeypatch, "check-supported", "--path", str(p)) == 1
    assert "units_root: must be a directory inside iac_root" in capsys.readouterr().err


def test_shared_units(tmp_path):
    assert Config().shared_units == []
    cfg = load_config(write(tmp_path, "shared_units: [edge__tunnel, access]\n"))
    assert cfg.shared_units == ["edge__tunnel", "access"]


@pytest.mark.parametrize(
    "text",
    [
        "shared_units: tunnel",
        "shared_units: [tunnel, tunnel]",
        "shared_units: ['']",
        "shared_units: [edge/tunnel]",
        "shared_units: ['../x']",
        "shared_units: ['..']",
        "shared_units: [1]",
    ],
)
def test_invalid_shared_units_rejected(tmp_path, text):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, text + "\n"))


def test_check_shared_layout(tmp_path, monkeypatch, capsys):
    for rel in ("stages/prod/tunnel", "stages/prod/edge/access", "stages/dev/edge/access"):
        (tmp_path / rel).mkdir(parents=True)
        (tmp_path / rel / "terragrunt.hcl").write_text("")
    monkeypatch.chdir(tmp_path)
    p = write(tmp_path, "shared_units: [tunnel]\n")
    assert _cli(monkeypatch, "check-shared", "--path", str(p)) == 0
    p = write(tmp_path, "shared_units: [tunnel, edge__access, missing]\n")
    assert _cli(monkeypatch, "check-shared", "--path", str(p)) == 1
    err = capsys.readouterr().err
    assert "edge__access: also present in stage dev" in err
    assert "missing: no unit at stages/prod/missing" in err
    assert "tunnel" not in err
    p = write(tmp_path, "")
    assert _cli(monkeypatch, "check-shared", "--path", str(p)) == 0


def test_label_keys_and_quiet_parse(capsys):
    cfg = config.parse_config_text("solo_mode: true\nidentity: secrets\n")
    assert capsys.readouterr().err == ""  # unlike load_config, nothing is printed
    assert config.label_keys(cfg) == [
        {"kind": "non-compliant", "setting": "solo_mode"},
        {"kind": "deviation", "setting": "identity"},
    ]
    assert config.labels(cfg) == [config.NON_COMPLIANT, config.DEVIATION_SECRETS]
    assert config.parse_config_text("") == Config()
    for bad in ("bogus: 1", "stages: [unclosed"):
        with pytest.raises(ConfigError):
            config.parse_config_text(bad)


def test_provider_env_declares_names_for_the_secrets_identity(tmp_path):
    cfg = load_config(
        write(tmp_path, "identity: secrets\nprovider_env: [HCLOUD_TOKEN, TF_VAR_ovh_key]\n")
    )
    assert cfg.provider_env == ["HCLOUD_TOKEN", "TF_VAR_ovh_key"]
    assert Config().provider_env == []
    assert load_config(write(tmp_path, "provider_env: []\n")).provider_env == []


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("identity: secrets\nprovider_env: [LD_PRELOAD]\n", "'LD_PRELOAD': reserved"),
        ("identity: secrets\nprovider_env: [GITHUB_TOKEN]\n", "'GITHUB_TOKEN': reserved"),
        ("identity: secrets\nprovider_env: [TF_CLI_ARGS]\n", "'TF_CLI_ARGS': reserved"),
        ("identity: secrets\nprovider_env: [lower]\n", "not an environment variable name"),
        ("identity: secrets\nprovider_env: [A, A]\n", "names must be unique"),
        ("identity: secrets\nprovider_env: A\n", "expected a list"),
        ("provider_env: [HCLOUD_TOKEN]\n", "only with identity: secrets"),
    ],
)
def test_provider_env_refusals(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path, text))


# ---- issue #10: an s3:// ledger endpoint never comes from the file ----


@pytest.mark.parametrize(
    "url",
    [
        "s3://bkt?region=auto&endpoint=https://acct.r2.cloudflarestorage.com",
        "S3://bkt?endpoint=https://evil.example&region=auto",
        "s3://bkt?region=auto&%65ndpoint=https://evil.example",  # percent-encoded key
        "s3://bkt?region=auto&endpoint=",  # even an empty one
        "s3://bkt?region=auto&endpoint=https://a.example&endpoint=https://b.example",
    ],
)
def test_ledger_url_with_s3_endpoint_is_refused(tmp_path, url):
    """The file is code a pull request can change; an endpoint chosen there would receive the
    signed ledger requests of the job. It must come from AYASEE_LEDGER_URL (the variable)."""
    with pytest.raises(ConfigError, match="AYASEE_LEDGER_URL") as exc:
        load_config(write(tmp_path, f'ledger: {{url: "{url}"}}\n'))
    assert "evil" not in str(exc.value)


@pytest.mark.parametrize(
    "url",
    [
        "s3://bkt?region=eu-west-1&prefix=p",
        "git-branch:///repo?branch=ayasee-ledger&remote=origin",
        "file:///tmp/l?endpoint=https://x.example",  # only s3 sends signed requests
    ],
)
def test_ledger_url_without_s3_endpoint_is_accepted(tmp_path, url):
    assert load_config(write(tmp_path, f'ledger: {{url: "{url}"}}\n')).ledger.url == url


def test_endpoint_refusal_covers_every_url_open_store_reads_an_endpoint_from(monkeypatch):
    """The config check and open_store parse the URL the same way: whatever open_store would
    send to an endpoint, the config check refuses."""
    from ayasee.ledger import names_s3_endpoint, open_store

    monkeypatch.setenv("AWS_REGION", "auto")
    for url in (
        "s3://bkt?endpoint=https://h.example",
        "S3://bkt?endpoint=https://h.example",
        "s3://bkt?%65ndpoint=https://h.example",
        " s3://bkt?endpoint=https://h.example",
    ):
        store = open_store(url)
        assert store.client.base == "https://h.example"
        assert names_s3_endpoint(url)
