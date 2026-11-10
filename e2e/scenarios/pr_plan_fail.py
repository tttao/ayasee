"""A pull request whose dev plan fails: plan-dev fails and no staged run starts."""

from e2e.sandbox import PR_WORKFLOW, STAGED_WORKFLOW, Sandbox, expect
from e2e.scenarios.common import RUN_TIMEOUT, unit_files

BAD_PLAN = """resource "terraform_data" "bad" {
  input = tonumber("not-a-number")
}
"""


def run(sandbox: Sandbox) -> None:
    pr = sandbox.open_pr(unit_files("dev", "badplan", BAD_PLAN), "e2e: plan failure")
    try:
        done = sandbox.wait_for_run(PR_WORKFLOW, pr.head_sha, RUN_TIMEOUT, event="pull_request")
        expect(done["conclusion"] == "failure", f"PR run concluded {done['conclusion']}")
        plan = sandbox.job_conclusion(done["id"], "plan-dev")
        expect(plan == "failure", f"plan-dev job: {plan}")
        started = sandbox.runs(STAGED_WORKFLOW, pr.head_sha)
        expect(not started, "a staged run started for the failing pull request")
    finally:
        sandbox.close_pr(pr)
