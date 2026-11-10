"""An apply that fails in dev: uat and prod are skipped and a stop-the-line issue is open."""

from ayasee.labels import STOP_LABEL
from e2e.sandbox import Sandbox, expect
from e2e.scenarios.common import best_effort, finish, remove_files, start_run, unit_files

# Plans cleanly, fails when applied.
BOOM = """resource "terraform_data" "boom" {
  provisioner "local-exec" {
    command = "exit 1"
  }
}
"""


def run(sandbox: Sandbox) -> None:
    sha = sandbox.ship(unit_files("dev", "boom", BOOM), "e2e: dev apply failure")
    try:
        run_ = start_run(sandbox, sha)
        done = finish(sandbox, run_["id"])
        expect(done["conclusion"] == "failure", f"run concluded {done['conclusion']}")
        dev = sandbox.job_conclusion(run_["id"], "dev")
        expect(dev == "failure", f"dev job: {dev}")
        for stage in ("uat", "prod-plan", "prod"):
            got = sandbox.job_conclusion(run_["id"], stage)
            expect(got in (None, "skipped"), f"{stage} job should be skipped, got {got}")
        expect(not sandbox.pending_deployments(run_["id"]), "the run is waiting for a reviewer")
        issues = sandbox.issues([STOP_LABEL, "env:dev"])
        expect(len(issues) >= 1, "no open stop-the-line issue for dev")
    finally:
        best_effort(
            "close issues", sandbox.cleanup
        )  # the open issue would stop every later scenario
        best_effort(
            "remove the failing unit",
            lambda: sandbox.ship(remove_files("dev", "boom"), "e2e: remove the failing unit"),
        )
