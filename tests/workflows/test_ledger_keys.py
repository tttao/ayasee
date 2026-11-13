"""Issue #10: the s3:// ledger has its own keys. The guard and ledger steps get the dedicated
ledger key (AYASEE_LEDGER_AWS_*) as AWS_*, and nothing else does; those steps never get the state
key of the plan and apply steps. cockpit-collect has its own read-only pair (AYASEE_COCKPIT_AWS_*)
for its collect step only."""

import pytest
import yaml
from conftest import WORKFLOWS

LEDGER_SECRETS = ("AYASEE_LEDGER_AWS_ACCESS_KEY_ID", "AYASEE_LEDGER_AWS_SECRET_ACCESS_KEY")
LEDGER_ENV = {
    "AWS_ACCESS_KEY_ID": "${{ secrets.AYASEE_LEDGER_AWS_ACCESS_KEY_ID }}",
    "AWS_SECRET_ACCESS_KEY": "${{ secrets.AYASEE_LEDGER_AWS_SECRET_ACCESS_KEY }}",
}
# the state and provider credentials of the plan and apply steps
STATE = (
    "secrets.AWS_ACCESS_KEY_ID",
    "secrets.AWS_SECRET_ACCESS_KEY",
    "secrets.CLOUDFLARE_API_TOKEN",
    "secrets.AYASEE_PROVIDER_ENV",
    "secrets.AYASEE_KMS_",
    "secrets.AYASEE_PLAN_PASSPHRASE",
    "secrets.extra-env-secret",
)
# every step that reads or writes the ledger, as (job, step): the steps that also get ledger-token
EXPECTED = {
    "iac-staged.yml": [
        *[(f"stage-{i}", kind) for i in range(1, 5) for kind in ("guard", "ledger")],
        ("prod-plan", "guard"),
        ("prod", "guard"),
        ("prod", "ledger"),
        ("prod-in-sync", "guard"),
        ("prod-in-sync", "ledger"),
    ],
    "iac-nightly.yml": [("drift", "ledger show")],
    "iac-breakglass.yml": [("breakglass", "ledger show"), ("breakglass", "ledger")],
}


def _wf(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _kind(step):
    uses = step.get("uses", "")
    if "/actions/" in uses:
        return uses.split("/actions/")[1].split("@")[0]
    return "ledger show" if "ayasee ledger show" in step.get("run", "") else step.get("name")


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_ledger_key_reaches_exactly_the_guard_and_ledger_steps(name):
    wf = _wf(name)
    seen = []
    for job_name, job in wf["jobs"].items():
        assert "AYASEE_LEDGER_AWS" not in str(job.get("env") or {}), job_name
        assert "AWS_" not in str(job.get("env") or {}), job_name
        for step in job.get("steps", []):
            env = step.get("env") or {}
            ledger_step = "AYASEE_LEDGER_TOKEN" in env  # the guard and ledger steps (R32)
            if not any(s in str(step) for s in LEDGER_SECRETS):
                assert not ledger_step, (name, job_name, step)
                continue
            assert ledger_step, (name, job_name, step)
            for key, value in LEDGER_ENV.items():
                assert env[key] == value, (name, job_name, key)
            # no session token: a stored secret cannot hold temporary credentials
            assert "AWS_SESSION_TOKEN" not in env
            # the ledger step never holds the state key, nor any plan or apply credential
            assert not any(s in str(step) for s in STATE), (name, job_name, step)
            seen.append((job_name, _kind(step)))
    assert seen == EXPECTED[name]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_plan_and_apply_steps_never_get_the_ledger_key(name):
    for job in _wf(name)["jobs"].values():
        for step in job.get("steps", []):
            if "/actions/plan@" in step.get("uses", "") or "/actions/apply@" in step.get(
                "uses", ""
            ):
                assert "AYASEE_LEDGER_AWS" not in str(step)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_ledger_key_is_an_optional_environment_secret(name):
    declared = _wf(name)[True]["workflow_call"]["secrets"]
    for secret in LEDGER_SECRETS:
        assert declared[secret]["required"] is False
    text = " ".join(declared["AYASEE_LEDGER_AWS_ACCESS_KEY_ID"]["description"].split())
    assert "environment secret of" in text and "never the state key" in text
    assert "Only the guard and ledger steps receive it" in text
    assert "inherit" not in (WORKFLOWS / name).read_text()


def test_the_trunk_guard_step_does_not_get_the_ledger_key():
    for name in ("iac-staged.yml", "iac-nightly.yml"):
        for step in _wf(name)["jobs"]["resolve"]["steps"]:
            assert "AYASEE_LEDGER_AWS" not in str(step), name


def test_the_pull_request_workflow_does_not_read_the_ledger():
    text = (WORKFLOWS / "iac-pr.yml").read_text()
    assert "AYASEE_LEDGER" not in text and "ledger-token" not in text


# ---- cockpit-collect -----------------------------------------------------------------

COCKPIT_SECRETS = ("AYASEE_COCKPIT_AWS_ACCESS_KEY_ID", "AYASEE_COCKPIT_AWS_SECRET_ACCESS_KEY")


def test_cockpit_key_reaches_only_the_collect_step():
    wf = _wf("cockpit-collect.yml")
    declared = wf[True]["workflow_call"]["secrets"]
    for secret in COCKPIT_SECRETS:
        assert declared[secret]["required"] is False
    text = " ".join(declared["AYASEE_COCKPIT_AWS_ACCESS_KEY_ID"]["description"].split())
    assert "s3:GetObject" in text and "s3:ListBucket" in text and "read-only" in text
    seen = []
    for job_name, job in wf["jobs"].items():
        assert "AWS" not in str(job.get("env") or {})
        for step in job.get("steps", []):
            if not any(s in str(step) for s in COCKPIT_SECRETS):
                assert "AWS_" not in str(step), step
                continue
            assert step["env"]["AWS_ACCESS_KEY_ID"] == (
                "${{ secrets.AYASEE_COCKPIT_AWS_ACCESS_KEY_ID }}"
            )
            assert step["env"]["AWS_SECRET_ACCESS_KEY"] == (
                "${{ secrets.AYASEE_COCKPIT_AWS_SECRET_ACCESS_KEY }}"
            )
            assert "AWS_SESSION_TOKEN" not in step["env"]
            # the collect step holds the read token, never the write or issue token
            assert "write-token" not in str(step) and "issue-token" not in str(step)
            seen.append((job_name, step["name"]))
    assert seen == [("collect", "Collect the snapshot")]
