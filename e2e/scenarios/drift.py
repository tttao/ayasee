"""A manual state edit in prod: the nightly run opens a drift issue with stop-the-line."""

from ayasee.labels import DRIFT_LABEL, STOP_LABEL
from e2e.sandbox import NIGHTLY_WORKFLOW, Sandbox, expect
from e2e.scenarios.common import (
    RUN_TIMEOUT,
    STEP_TIMEOUT,
    best_effort,
    deploy,
    tamper_marker,
)


def run(sandbox: Sandbox) -> None:
    deploy(sandbox, "e2e: baseline for drift")
    expect(not sandbox.issues([DRIFT_LABEL, "env:prod"]), "a drift issue is open before the edit")
    old = tamper_marker(sandbox, "edited-by-hand")
    try:
        started = sandbox.dispatch(NIGHTLY_WORKFLOW, {}, STEP_TIMEOUT)
        done = sandbox.wait_for_completed(started["id"], RUN_TIMEOUT)
        expect(done["conclusion"] == "success", f"nightly concluded {done['conclusion']}")
        issues = sandbox.issues([DRIFT_LABEL, STOP_LABEL, "env:prod"])
        expect(len(issues) == 1, f"expected one drift issue with stop-the-line, got {len(issues)}")
    finally:
        best_effort("restore state", lambda: tamper_marker(sandbox, old))
        best_effort("close issues", sandbox.cleanup)
