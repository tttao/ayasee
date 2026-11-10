"""Merge A, merge B while A waits at the gate, approve A: A's prod job fails at the guard.

The prod jobs share a concurrency group, so B's prod job only reaches its own gate once A's prod
job has ended. B's prod-plan has succeeded (and uat is live at B) before A is approved."""

from e2e.sandbox import Sandbox, expect
from e2e.scenarios.common import (
    RUN_TIMEOUT,
    STEP_TIMEOUT,
    change,
    deploy,
    finish,
    reject_leftovers,
    run_to_gate,
    start_run,
)


def run(sandbox: Sandbox) -> None:
    deploy(sandbox, "e2e: baseline for supersession")
    runs: list[int] = []
    try:
        a = sandbox.ship(change(sandbox), "e2e: change A")
        run_a = run_to_gate(sandbox, a)
        runs.append(run_a["id"])
        b = sandbox.ship(change(sandbox), "e2e: change B")
        run_b = start_run(sandbox, b)
        runs.append(run_b["id"])
        plan_b = sandbox.wait_for_job(run_b["id"], "prod-plan", STEP_TIMEOUT)
        expect(plan_b["conclusion"] == "success", f"B's prod-plan: {plan_b['conclusion']}")
        uat = sandbox.ledger("uat")
        expect(uat is not None and uat.sha == b, "uat is not live at B")

        sandbox.review(run_a["id"], "prod", approve=True)
        done_a = finish(sandbox, run_a["id"])
        expect(done_a["conclusion"] == "failure", f"run A concluded {done_a['conclusion']}")
        prod_a = sandbox.job(run_a["id"], "prod")
        expect(
            prod_a is not None and prod_a["conclusion"] == "failure", "A's prod job did not fail"
        )
        failed = [s for s in prod_a.get("steps", []) if s.get("conclusion") == "failure"]
        expect(
            failed and all("guard" in str(s.get("name", "")).lower() for s in failed),
            f"A's prod job failed outside the guard: {[s.get('name') for s in failed]}",
        )
        expect("superseded" in sandbox.job_log(run_a["id"], "prod"), "A's guard said no superseded")
        prod = sandbox.ledger("prod")
        expect(prod is None or prod.sha != a, "prod was applied at the superseded commit")

        # With A out of the concurrency group, B's prod job reaches its gate and applies.
        sandbox.wait_for_pending(run_b["id"], "prod", STEP_TIMEOUT)
        sandbox.review(run_b["id"], "prod", approve=True)
        done_b = sandbox.wait_for_completed(run_b["id"], RUN_TIMEOUT)
        expect(done_b["conclusion"] == "success", f"run B concluded {done_b['conclusion']}")
        prod = sandbox.ledger("prod")
        expect(prod is not None and prod.sha == b, "prod ledger is not at B")
    finally:
        reject_leftovers(sandbox, runs)
