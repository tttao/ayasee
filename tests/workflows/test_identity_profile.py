"""Issue #1: azure/login and the ARM_* variables apply to `identity: oidc` only, everywhere."""

import re

import pytest
import yaml
from conftest import WORKFLOWS

ARM = ("ARM_USE_OIDC", "ARM_CLIENT_ID", "ARM_TENANT_ID", "ARM_SUBSCRIPTION_ID")
# workflow -> (the expression that holds the identity, where the ARM variables live)
CASES = {
    "iac-staged.yml": ("needs.resolve.outputs.identity", "job"),
    "iac-nightly.yml": ("needs.resolve.outputs.identity", "job"),
    "iac-pr.yml": ("steps.changed.outputs.identity", "step"),
    "iac-breakglass.yml": ("steps.cfg.outputs.identity", "step"),
}
# jobs whose identity expression differs from the workflow's (job -> expression)
JOB_IDENT = {
    "iac-pr.yml": {"plan-shared": "needs.shared-changes.outputs.identity"},
}


def _ident(name, job_name):
    return JOB_IDENT.get(name, {}).get(job_name, CASES[name][0])


def _wf(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_azure_login_is_conditional_on_the_oidc_identity(name):
    logins = 0
    for job_name, job in _wf(name)["jobs"].items():
        ident = _ident(name, job_name)
        for step in job.get("steps", []):
            if "azure/login" in step.get("uses", ""):
                logins += 1
                assert f"{ident} == 'oidc'" in step["if"], name
    assert logins >= 1


@pytest.mark.parametrize("name", sorted(CASES))
def test_arm_variables_are_set_only_for_the_oidc_identity(name):
    _, where = CASES[name]
    seen = 0
    for job_name, job in _wf(name)["jobs"].items():
        ident = _ident(name, job_name)
        scopes = [job.get("env") or {}] + [s.get("env") or {} for s in job.get("steps", [])]
        for env in scopes:
            for key, value in env.items():
                if key in ARM or key == "DATABRICKS_AUTH_TYPE":
                    seen += 1
                    assert re.fullmatch(
                        r"\$\{\{ " + re.escape(ident) + r" == 'oidc' && \S+ \|\| '' \}\}", value
                    ), (name, key, value)
        if where == "step":
            assert not set(ARM) & set(job.get("env") or {}), name
    assert seen >= 5


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_identity_is_read_from_the_configuration(name):
    text = (WORKFLOWS / name).read_text()
    assert "'.identity'" in text


# the three named tokens, and the declared provider variables of issue #11 in one JSON secret
TOKENS = (
    "CLOUDFLARE_API_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AYASEE_PROVIDER_ENV",
)
# issue #10: the guard and ledger steps get the dedicated s3:// ledger key under the same names
LEDGER_KEY_ENV = {
    "AWS_ACCESS_KEY_ID": "${{ secrets.AYASEE_LEDGER_AWS_ACCESS_KEY_ID }}",
    "AWS_SECRET_ACCESS_KEY": "${{ secrets.AYASEE_LEDGER_AWS_SECRET_ACCESS_KEY }}",
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_provider_tokens_are_optional_and_never_inherited(name):
    wf = _wf(name)
    declared = wf[True]["workflow_call"]["secrets"]
    for token in TOKENS:
        assert declared[token]["required"] is False
        assert "environment secret" in " ".join(declared[token]["description"].split())
    assert "inherit" not in (WORKFLOWS / name).read_text()


@pytest.mark.parametrize("name", sorted(CASES))
def test_provider_tokens_reach_only_plan_and_apply_steps_for_the_secrets_identity(name):
    ident, _ = CASES[name]
    found = 0
    for job_name, job in _wf(name)["jobs"].items():
        for token in TOKENS:
            assert token not in str(job.get("env") or {}), (name, job_name)
        for step in job.get("steps", []):
            env = dict(step.get("env") or {})
            for key, value in LEDGER_KEY_ENV.items():
                if env.get(key) == value:  # the ledger's own key, see test_ledger_keys.py
                    del env[key]
            step = {**step, "env": env}
            has = [t for t in TOKENS if t in env]
            if not has:
                assert not any(t in str(step) for t in TOKENS), (name, job_name, step)
                continue
            assert re.search(r"/actions/(plan|apply)@", step.get("uses", "")), (name, step)
            assert has == list(TOKENS)
            ident = _ident(name, job_name)
            for token in TOKENS:
                expected = f"${{{{ {ident} == 'secrets' && secrets.{token} || '' }}}}"
                assert env[token] == expected, (name, token)
            found += 1
    assert found >= 1


@pytest.mark.parametrize("name", sorted(CASES))
def test_provider_env_names_come_from_the_configuration(name):
    """Every step that receives AYASEE_PROVIDER_ENV also gets the declared names, read from
    .ayasee.yaml by the step or job that resolves the identity (issue #11)."""
    wf = _wf(name)
    text = (WORKFLOWS / name).read_text()
    steps = 0
    for job_name, job in wf["jobs"].items():
        for step in job.get("steps", []):
            env = step.get("env") or {}
            if "AYASEE_PROVIDER_ENV" not in env:
                assert "AYASEE_PROVIDER_ENV_NAMES" not in env, (name, job_name)
                continue
            source = _ident(name, job_name).rsplit(".", 1)[0]
            assert env["AYASEE_PROVIDER_ENV_NAMES"] == f"${{{{ {source}.provider-env-names }}}}"
            steps += 1
            if source.startswith("needs."):
                producer = source.split(".")[1]
                outputs = wf["jobs"][producer]["outputs"]
                assert outputs["provider-env-names"].endswith(".outputs.provider-env-names }}")
    assert steps >= 1
    assert """echo "provider-env-names=$(jq -r '.provider_env | join(",")' """ in text


@pytest.mark.parametrize("name", sorted(CASES))
def test_deviations_are_labelled_in_the_run_summary(name):
    labelled = []
    for job_name, job in _wf(name)["jobs"].items():
        for step in job.get("steps", []):
            run = step.get("run", "")
            if "ayasee config labels" in run:
                labelled.append(job_name)
                assert step["env"]["CONFIG_PATH"] == "${{ inputs.config-path }}"
                assert '"::warning::$line"' in run and "$GITHUB_STEP_SUMMARY" in run
                assert "if" not in step and "continue-on-error" not in step
    assert len(labelled) == 1, labelled


def test_label_step_behaviour(tmp_path):
    import os
    import subprocess

    step = next(
        s
        for s in _wf("iac-staged.yml")["jobs"]["resolve"]["steps"]
        if "ayasee config labels" in s.get("run", "")
    )
    shim = tmp_path / "bin"
    shim.mkdir()
    fake = shim / "ayasee"
    fake.write_text('#!/bin/bash\necho "DEVIATION: one"\necho "NON-COMPLIANT: two"\n')
    fake.chmod(0o755)
    summary = tmp_path / "summary.md"
    env = {
        **os.environ,
        "PATH": f"{shim}:{os.environ['PATH']}",
        "CONFIG_PATH": "x",
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    proc = subprocess.run(
        ["bash", "-e", "-c", step["run"]], env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "::warning::DEVIATION: one",
        "::warning::NON-COMPLIANT: two",
    ]
    assert summary.read_text() == "> **DEVIATION: one**\n> **NON-COMPLIANT: two**\n"
    fake.write_text("#!/bin/bash\nexit 0\n")
    summary.unlink()
    proc = subprocess.run(
        ["bash", "-e", "-c", step["run"]], env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0 and proc.stdout == "" and not summary.exists()
    fake.write_text("#!/bin/bash\nexit 1\n")
    proc = subprocess.run(
        ["bash", "-e", "-c", step["run"]], env=env, capture_output=True, text=True
    )
    assert proc.returncode != 0
