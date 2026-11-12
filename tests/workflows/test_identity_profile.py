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


def _wf(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_azure_login_is_conditional_on_the_oidc_identity(name):
    ident, _ = CASES[name]
    logins = 0
    for job in _wf(name)["jobs"].values():
        for step in job.get("steps", []):
            if "azure/login" in step.get("uses", ""):
                logins += 1
                assert f"{ident} == 'oidc'" in step["if"], name
    assert logins >= 1


@pytest.mark.parametrize("name", sorted(CASES))
def test_arm_variables_are_set_only_for_the_oidc_identity(name):
    ident, where = CASES[name]
    seen = 0
    for job in _wf(name)["jobs"].values():
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
