"""Drift opens an issue; undoing the edit lets the next nightly run close it and notify."""

from ayasee.labels import DRIFT_LABEL, STOP_LABEL
from e2e.sandbox import NIGHTLY_WORKFLOW, Sandbox, expect
from e2e.scenarios.common import (
    RUN_TIMEOUT,
    STEP_TIMEOUT,
    best_effort,
    deploy,
    tamper_marker,
)


def _nightly(sandbox: Sandbox) -> None:
    started = sandbox.dispatch(NIGHTLY_WORKFLOW, {}, STEP_TIMEOUT)
    done = sandbox.wait_for_completed(started["id"], RUN_TIMEOUT)
    expect(done["conclusion"] == "success", f"nightly concluded {done['conclusion']}")


def run(sandbox: Sandbox) -> None:
    sha, _ = deploy(sandbox, "e2e: baseline for drift_cleared")
    old = tamper_marker(sandbox, "edited-by-hand")
    try:
        _nightly(sandbox)
        expect(
            len(sandbox.issues([DRIFT_LABEL, STOP_LABEL, "env:prod"])) == 1,
            "the edit did not open a drift issue",
        )
        tamper_marker(sandbox, old)
        _nightly(sandbox)
        expect(not sandbox.issues([DRIFT_LABEL, "env:prod"]), "the drift issue is still open")
        closed = sandbox.issues([DRIFT_LABEL, "env:prod"], state="closed")
        expect(len(closed) >= 1, "the drift issue was not closed")
        titles = [e.title for e in sandbox.events_for(sha) if e.env == "prod"]
        expect(
            any(t.startswith("Drift cleared") for t in titles),
            f"no drift_cleared card for {sha[:12]}; cards: {titles}",
        )
    finally:
        best_effort("close issues", sandbox.cleanup)
