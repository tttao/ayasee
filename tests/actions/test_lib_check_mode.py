"""sg_check_mode from actions/lib/ayasee-lib.sh, sourced in bash (no tofu needed)."""

import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[2] / "actions" / "lib" / "ayasee-lib.sh"


def _check(env, mode="same-job", encrypt="false"):
    script = f'. "{LIB}"; sg_check_mode "$1" "$2" "$3" opentofu false'
    return subprocess.run(
        ["bash", "-c", script, "_", env, mode, encrypt], capture_output=True, text=True
    )


@pytest.mark.parametrize("env", ["dev", "uat", "staging", "pre_prod", "test-1"])
def test_same_job_is_allowed_for_any_pre_prod_stage(env):
    proc = _check(env)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("env", ["prod", "prod-plan", "prod-breakglass", "dev-plan", "drift-dev"])
def test_same_job_is_refused_for_prod_and_fixed_role_environments(env):
    proc = _check(env)
    assert proc.returncode != 0
    assert f"only allowed for a pre-prod stage, not {env}" in proc.stderr
