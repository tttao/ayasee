"""One module per scenario; each defines ``run(sandbox) -> None`` and raises on failure."""

SCENARIOS = [
    "valid",
    "scan_fail",
    "pr_plan_fail",
    "dev_apply_fail",
    "gate_reject",
    "drift",
    "drift_cleared",
    "supersession",
    "stale_plan",
    "cascade",
    "empty_prod",
    "breakglass",
]
