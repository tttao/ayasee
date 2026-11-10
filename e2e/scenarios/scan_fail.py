"""A pull request with an open security group fails the PR check; no staged run starts."""

from e2e.sandbox import PR_WORKFLOW, STAGED_WORKFLOW, Sandbox, expect
from e2e.scenarios.common import RUN_TIMEOUT

INSECURE = """resource "aws_security_group_rule" "open_ssh" {
  type              = "ingress"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = "sg-0123456789abcdef0"
}
"""


def run(sandbox: Sandbox) -> None:
    pr = sandbox.open_pr({"scratch/insecure.tf": INSECURE}, "e2e: scan failure")
    try:
        done = sandbox.wait_for_run(PR_WORKFLOW, pr.head_sha, RUN_TIMEOUT, event="pull_request")
        expect(done["conclusion"] == "failure", f"PR run concluded {done['conclusion']}")
        checks = sandbox.job_conclusion(done["id"], "checks")
        expect(checks == "failure", f"checks job: {checks}")
        started = sandbox.runs(STAGED_WORKFLOW, pr.head_sha)
        expect(not started, "a staged run started for the failing pull request")
    finally:
        sandbox.close_pr(pr)
