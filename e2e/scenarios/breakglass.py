"""A valid break-glass: prod applied, post-approval issue opened, incident in the ledger.

Break-glass has no unit scope (R71): it plans the whole prod tree, so the ledger record of prod
names every fixture unit (unchanged ones as no-ops)."""

from ayasee.labels import POST_APPROVAL_LABEL
from e2e.sandbox import BREAKGLASS_WORKFLOW, Sandbox, expect
from e2e.scenarios.common import (
    RUN_TIMEOUT,
    STEP_TIMEOUT,
    best_effort,
    change,
    deploy,
    reject_leftovers,
    run_to_gate,
)

INCIDENT = "INC123"
PROD_UNITS = {"base", "grants"}  # e2e/fixture/stages/prod


def run(sandbox: Sandbox) -> None:
    deploy(sandbox, "e2e: baseline for breakglass")
    sha = sandbox.ship(change(sandbox), "e2e: change shipped by break-glass")
    runs: list[int] = []
    try:
        normal = run_to_gate(sandbox, sha)  # the normal run waits; break-glass goes around it
        runs.append(normal["id"])
        started = sandbox.dispatch(
            BREAKGLASS_WORKFLOW, {"incident": INCIDENT, "sha": sha}, STEP_TIMEOUT
        )
        sandbox.wait_for_pending(started["id"], "prod-breakglass", STEP_TIMEOUT)
        sandbox.review(started["id"], "prod-breakglass", approve=True)
        done = sandbox.wait_for_completed(started["id"], RUN_TIMEOUT)
        expect(done["conclusion"] == "success", f"break-glass concluded {done['conclusion']}")
        prod = sandbox.ledger("prod")
        expect(prod is not None and prod.sha == sha, "prod ledger is not at the break-glass SHA")
        expect(
            prod.breakglass_incident == INCIDENT,
            f"ledger incident: {prod.breakglass_incident}",
        )
        expect(
            set(prod.units) == PROD_UNITS,
            f"break-glass recorded units {sorted(prod.units)}, not the whole prod tree",
        )
        issues = sandbox.issues([POST_APPROVAL_LABEL, "env:prod"])
        expect(
            any(INCIDENT in str(i.get("title", "")) for i in issues),
            "no post-approval issue for the incident",
        )
        cards = [e.title for e in sandbox.events_for(sha)]
        expect(
            any(t.startswith("Break-glass used") for t in cards),
            f"no breakglass_used card for {sha[:12]}; cards: {cards}",
        )
    finally:
        reject_leftovers(sandbox, runs)  # the normal gate is moot now
        best_effort("close issues", sandbox.cleanup)
