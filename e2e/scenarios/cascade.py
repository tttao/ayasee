"""R52: a commit that changes base (an output grants reads) and grants together.

dev and uat apply unit by unit with a fresh plan each, so they converge. prod-plan cannot remake an
approved plan, so it refuses ("cascading change") and the gate never opens. A second commit puts
the files back, which leaves nothing for prod."""

from e2e.sandbox import Sandbox, expect
from e2e.scenarios.common import best_effort, deploy, finish, reject_leftovers, start_run

GENERATION = "e2e-generation.txt"
TAG = "e2e-grants-tag.txt"
REFUSAL = "cascading change: grants depends on base, which also changes; land base first"


def run(sandbox: Sandbox) -> None:
    deploy(sandbox, "e2e: baseline for cascade")
    before = sandbox.ledger("prod")
    n = sandbox.nonce()
    sha = sandbox.ship(
        {GENERATION: f"gen-{n}\n", TAG: f"tag-{n}\n"}, "e2e: base and grants together"
    )
    runs: list[int] = []
    try:
        run_ = start_run(sandbox, sha)
        runs.append(run_["id"])
        done = finish(sandbox, run_["id"])
        expect(done["conclusion"] == "failure", f"run concluded {done['conclusion']}")
        for stage in ("dev", "uat"):
            got = sandbox.job_conclusion(run_["id"], stage)
            expect(got == "success", f"{stage} job: {got}")
            rec = sandbox.ledger(stage)
            expect(rec is not None and rec.sha == sha and rec.converged, f"{stage} not converged")
        plan = sandbox.job_conclusion(run_["id"], "prod-plan")
        expect(plan == "failure", f"prod-plan job: {plan}")
        expect(REFUSAL in sandbox.job_log(run_["id"], "prod-plan"), "no cascading-change refusal")
        for stage in ("prod", "prod-in-sync"):
            got = sandbox.job_conclusion(run_["id"], stage)
            expect(got in (None, "skipped"), f"{stage} should be skipped, got {got}")
        expect(not sandbox.pending_deployments(run_["id"]), "the run waits for a reviewer")
        expect(sandbox.ledger("prod") == before, "prod ledger moved although prod-plan refused")
    finally:
        reject_leftovers(sandbox, runs)  # a refused run must have no gate; never leave one open

        def restore() -> None:
            # Put the repository back in line with prod so later scenarios plan cleanly.
            back = sandbox.ship({GENERATION: "g0\n", TAG: "t0\n"}, "e2e: restore base and grants")
            finish(sandbox, start_run(sandbox, back)["id"])

        best_effort("restore base and grants", restore)
