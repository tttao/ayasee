"""State changes between prod-plan and approval: the apply fails and nothing is applied.

The saved plan is refused by the engine (the state serial moved), not by the binding check,
whose inputs (plan files, locks, SHA, engine version) did not change. Engine output stays in the
runner's temp dir, so the job log only shows the failed apply step: that and the ledger are what
we assert."""

from e2e.sandbox import Sandbox, expect
from e2e.scenarios.common import (
    PROD_BASE_STATE,
    best_effort,
    change,
    deploy,
    finish,
    reject_leftovers,
    run_to_gate,
)

BINDING_REFUSAL = "approved plan no longer matches"  # apply.sh, binding verify exit 3


def run(sandbox: Sandbox) -> None:
    deploy(sandbox, "e2e: baseline for stale_plan")
    before = sandbox.ledger("prod")
    sha = sandbox.ship(change(sandbox), "e2e: change whose plan goes stale")
    run_ = run_to_gate(sandbox, sha)
    try:
        sandbox.edit_state(PROD_BASE_STATE, lambda state: None)  # only the serial moves
        sandbox.review(run_["id"], "prod", approve=True)
        done = finish(sandbox, run_["id"])
        expect(done["conclusion"] == "failure", f"run concluded {done['conclusion']}")
        prod = sandbox.job(run_["id"], "prod")
        expect(prod is not None and prod["conclusion"] == "failure", "prod job did not fail")
        failed = [s for s in prod.get("steps", []) if s.get("conclusion") == "failure"]
        expect(
            failed and all("apply" in str(s.get("name", "")).lower() for s in failed),
            f"prod failed outside the apply step: {[s.get('name') for s in failed]}",
        )
        log = sandbox.job_log(run_["id"], "prod")
        expect("base apply: failed" in log, "the first unit's apply did not fail")
        expect(BINDING_REFUSAL not in log, "the binding check refused; expected the engine to")
        expect("grants apply: ok" not in log, "a dependent unit was applied after the failure")
        after = sandbox.ledger("prod")
        if after is not None and after != before:
            expect(after.sha == sha and not after.converged, "prod ledger claims a converged apply")
            applied = [n for n, u in after.units.items() if u.status == "applied"]
            expect(not applied, f"units recorded as applied: {applied}")
        titles = [e.title for e in sandbox.events_for(sha)]
        expect(
            any(t.startswith("Prod failed") for t in titles),
            f"no prod_failed card for {sha[:12]}; cards: {titles}",
        )
    finally:
        reject_leftovers(sandbox, [run_["id"]])
        best_effort("close issues", sandbox.cleanup)  # a failed apply opens a stop-the-line issue
