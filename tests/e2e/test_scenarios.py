"""Every scenario against FakeSandbox: passes as expected, fails when one fact is off."""

import importlib

import pytest
from e2e.sandbox import SandboxTimeout, ScenarioFailure
from e2e.scenarios import SCENARIOS
from fake_sandbox import FakeSandbox

# scenario -> faults, each of which must make that scenario fail
FAULTS = {
    "valid": ["no_ledger_prod", "no_ledger_dev", "no_approver"],
    "scan_fail": ["scan_passes", "staged_runs_for_pr"],
    "pr_plan_fail": ["plan_passes", "staged_runs_for_pr"],
    "dev_apply_fail": ["no_stop_issue", "uat_runs"],
    "gate_reject": ["no_rejected_card", "no_prod_result", "prod_applied_on_reject"],
    "drift": ["no_drift_issue", "no_stop_label"],
    "drift_cleared": ["no_drift_issue", "issue_not_closed", "no_cleared_card"],
    "supersession": ["no_supersession"],
    "stale_plan": ["stale_applies", "no_failed_card"],
    "cascade": ["cascade_not_refused"],
    "empty_prod": ["gate_not_skipped", "ledger_not_moved"],
    "breakglass": ["no_post_approval", "no_incident", "breakglass_partial"],
    "solo": ["no_solo_label", "self_approval_refused", "approver_not_self", "no_post_approval"],
}


def scenario(name):
    return importlib.import_module(f"e2e.scenarios.{name}").run


def test_every_scenario_is_listed_with_faults():
    assert set(FAULTS) == set(SCENARIOS)


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario_passes_on_the_expected_outcome(name):
    scenario(name)(FakeSandbox())


@pytest.mark.parametrize(
    ("name", "fault"), [(n, f) for n, faults in FAULTS.items() for f in faults]
)
def test_scenario_fails_when_the_outcome_is_wrong(name, fault):
    with pytest.raises((ScenarioFailure, SandboxTimeout)):
        scenario(name)(FakeSandbox({fault}))


def test_scenarios_leave_no_open_issues_behind():
    for name in ("dev_apply_fail", "drift", "drift_cleared", "breakglass"):
        fake = FakeSandbox()
        scenario(name)(fake)
        assert fake.issues(["env:prod"]) == [] and fake.issues(["env:dev"]) == []


def test_finally_blocks_reject_leftover_gates_without_masking_the_error():
    from e2e.scenarios import supersession

    class Boom(FakeSandbox):
        def ledger(self, env):  # fails after A is at its gate and B has been merged
            if env == "uat" and self.runs_by_id and len(self.workflow_runs) >= 3:
                raise RuntimeError("original error")
            return super().ledger(env)

    fake = Boom()
    with pytest.raises(RuntimeError, match="original error"):
        supersession.run(fake)
    assert all(not r["pending"] for r in fake.workflow_runs)


def test_cleanup_failure_in_a_finally_does_not_mask_the_scenario_error():
    class Broken(FakeSandbox):
        def pending_deployments(self, run_id):
            raise RuntimeError("api down")

    with pytest.raises(ScenarioFailure):
        scenario("stale_plan")(Broken({"stale_applies"}))


@pytest.mark.parametrize(
    ("name", "fault"),
    [
        ("stale_plan", "stale_applies"),
        ("drift", "no_drift_issue"),
        ("drift_cleared", "no_drift_issue"),
        ("dev_apply_fail", "no_stop_issue"),
        ("breakglass", "no_incident"),
    ],
)
def test_a_broken_cleanup_is_logged_and_the_scenario_error_propagates(name, fault, capsys):
    class BrokenCleanup(FakeSandbox):
        def cleanup(self):
            raise RuntimeError("issues api down")

    with pytest.raises(ScenarioFailure):
        scenario(name)(BrokenCleanup({fault}))
    assert "cleanup step" in capsys.readouterr().err


def test_broken_state_restore_does_not_mask_the_error(capsys):
    class BrokenEdit(FakeSandbox):
        def edit_state(self, path, change):
            if self.state_marker != self.baseline_marker or getattr(self, "armed", False):
                raise RuntimeError("git api down")
            self.armed = True
            return super().edit_state(path, change)

    with pytest.raises(ScenarioFailure):
        scenario("drift")(BrokenEdit({"no_drift_issue"}))
    assert "restore state" in capsys.readouterr().err


def test_cascade_rejects_a_gate_that_should_not_exist():
    fake = FakeSandbox({"cascade_not_refused"})
    with pytest.raises((ScenarioFailure, SandboxTimeout)):
        scenario("cascade")(fake)
    assert all(not r["pending"] for r in fake.workflow_runs)


def test_the_second_prod_job_queues_behind_the_first():
    fake = FakeSandbox()
    a = fake.ship({"e2e-marker.txt": "a"}, "a")
    b = fake.ship({"e2e-marker.txt": "b"}, "b")
    run_a, run_b = fake.runs("main.yml", a)[0], fake.runs("main.yml", b)[0]
    assert run_a["pending"] == {"prod"} and run_b["pending"] == set()
    with pytest.raises(SandboxTimeout):
        fake.wait_for_pending(run_b["id"], "prod", 1)
    fake.review(run_a["id"], "prod", approve=False)
    assert fake.wait_for_pending(run_b["id"], "prod", 1)


def test_fake_waits_honour_what_they_wait_for():
    fake = FakeSandbox()
    sha = fake.ship({"e2e-marker.txt": "a"}, "a")
    assert fake.wait_for_run("main.yml", sha, 1, until="waiting")
    with pytest.raises(SandboxTimeout):
        fake.wait_for_run("main.yml", sha, 1, until="completed")
    with pytest.raises(SandboxTimeout):
        fake.wait_for_completed(fake.runs("main.yml", sha)[0]["id"], 1)
