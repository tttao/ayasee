"""Static checks on the iac-nightly reusable workflow."""

import re

import pytest
import yaml
from conftest import AYASEE_USES, SHA_REF, WORKFLOWS
from conftest import index as _index
from conftest import steps as _steps

PATH = WORKFLOWS / "iac-nightly.yml"
TEXT = PATH.read_text()


@pytest.fixture(scope="module")
def wf() -> dict:
    return yaml.safe_load(TEXT)


def _uses(step):
    return step.get("uses", "")


def _live_checkout(step):
    """The checkout of the live SHA (the drift job also checks out the run's commit first)."""
    return "actions/checkout@" in _uses(step) and step["with"].get("ref") != "${{ github.sha }}"


def test_trigger_is_workflow_call_only_and_top_level_permissions_empty(wf):
    assert list(wf[True]) == ["workflow_call"]
    assert wf["permissions"] == {}
    assert "config-path" in wf[True]["workflow_call"]["inputs"]


def test_resolve_emits_the_stages_minus_prod_plan(wf):
    step = _steps(wf, "resolve")[-1]
    assert "ayasee config show" in step["run"]
    assert "prod-plan" in step["run"] and ".stages[]" in step["run"]
    assert wf["jobs"]["resolve"]["outputs"]["envs"]
    assert wf["jobs"]["drift"]["strategy"]["matrix"]["env"] == (
        "${{ fromJSON(needs.resolve.outputs.envs) }}"
    )
    assert wf["jobs"]["drift"]["strategy"]["fail-fast"] is False


def test_drift_concurrency_is_separate_from_apply_and_never_cancels(wf):
    conc = wf["jobs"]["drift"]["concurrency"]
    assert conc["group"] == "ayasee-drift-${{ github.repository }}-${{ matrix.env }}"
    assert conc["cancel-in-progress"] is False
    staged = yaml.safe_load((WORKFLOWS / "iac-staged.yml").read_text())
    apply_groups = {
        j["concurrency"]["group"] for j in staged["jobs"].values() if "concurrency" in j
    }
    assert conc["group"] not in apply_groups
    assert all(not g.startswith("ayasee-drift") for g in apply_groups)


def test_every_job_uses_drift_environment_and_no_prod_environment(wf):
    assert wf["jobs"]["drift"]["environment"] == "drift-${{ matrix.env }}"
    for name, job in wf["jobs"].items():
        env = job.get("environment")
        assert env is None or env == "drift-${{ matrix.env }}", name
    for forbidden in ("environment: prod", "environment: uat", "environment: dev\n"):
        assert forbidden not in TEXT
    assert "prod-breakglass" not in TEXT


def test_drift_permissions(wf):
    assert wf["jobs"]["drift"]["permissions"] == {
        "id-token": "write",
        "contents": "read",
        "issues": "write",
        "actions": "read",
    }


def test_drift_checks_out_the_ledger_sha_not_main(wf):
    steps = _steps(wf, "drift")
    checkout = steps[_index(steps, _live_checkout)]
    assert checkout["with"]["ref"] == "${{ steps.ledger.outputs.sha }}"
    assert checkout["with"]["fetch-depth"] == 0
    assert checkout["with"]["persist-credentials"] is False
    ledger = steps[_index(steps, lambda s: s.get("id") == "ledger")]
    assert "ayasee ledger show" in ledger["run"]
    plan = steps[_index(steps, lambda s: "/actions/plan@" in _uses(s))]
    assert plan["env"]["SG_SHA"] == "${{ steps.ledger.outputs.sha }}"
    # checkout comes after the ledger read and before the plan
    ids = [s.get("id") or _uses(s) for s in steps]
    assert ids.index("ledger") < _index(steps, _live_checkout) < ids.index("plan")
    # only the failure notice and the clone for the ledger read use main's SHA
    rest = [
        s
        for s in steps
        if s.get("with", {}).get("event") != "stage_failed" and not _run_commit_checkout(s)
    ]
    assert "github.sha" not in yaml.dump(rest)


def _run_commit_checkout(step):
    return "actions/checkout@" in _uses(step) and step["with"].get("ref") == "${{ github.sha }}"


def test_drift_checks_out_the_run_commit_before_reading_the_ledger(wf):
    """I1: a git-branch:// ledger store needs a clone before `ledger show` runs."""
    steps = _steps(wf, "drift")
    first = _index(steps, _run_commit_checkout)
    assert steps[first]["with"] == {"ref": "${{ github.sha }}", "persist-credentials": False}
    assert "if" not in steps[first]
    assert first < _index(steps, lambda s: s.get("id") == "ledger")
    assert first == _index(steps, lambda s: "actions/checkout@" in _uses(s))


def test_resolve_refuses_unsupported_settings(wf):
    steps = _steps(wf, "resolve")
    i = _index(steps, lambda s: "config check-supported" in s.get("run", ""))
    assert steps[i]["env"]["CONFIG_PATH"] == "${{ inputs.config-path }}"
    assert "if" not in steps[i] and "exit 1" in steps[i]["run"]
    assert i < _index(steps, lambda s: s.get("id") == "resolve")


def test_findings_count_fails_on_cli_error(wf, tmp_path):
    """M1: the count is assigned before it is echoed, so a CLI error fails the step."""
    import os
    import subprocess

    steps = _steps(wf, "rescan")
    step = steps[_index(steps, lambda s: s.get("id") == "findings")]
    assert 'count="$(ayasee findings new' in step["run"]
    shim = tmp_path / "shim"
    shim.mkdir()
    out = tmp_path / "out"

    def run(body):
        (shim / "ayasee").write_text("#!/bin/bash\n" + body)
        (shim / "ayasee").chmod(0o755)
        out.write_text("")
        return subprocess.run(
            ["bash", "-e", "-c", step["run"]],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{shim}:{os.environ['PATH']}",
                "GITHUB_OUTPUT": str(out),
                "REPO": "o/r",
            },
        )

    assert run("echo 3\n").returncode == 0 and out.read_text() == "count=3\n"
    failed = run("echo 'findings: error: boom' >&2; exit 1\n")
    assert failed.returncode != 0 and out.read_text() == ""
    assert run("echo\n").returncode != 0 and out.read_text() == ""


def test_missing_ledger_record_is_a_notice_and_skip(wf):
    steps = _steps(wf, "drift")
    ledger = steps[_index(steps, lambda s: s.get("id") == "ledger")]
    assert "::notice::" in ledger["run"] and "skip=true" in ledger["run"]
    for step in steps:
        if step.get("id") in {"failed"} or _live_checkout(step):
            assert "skip" in step["if"]


def test_apply_failed_issue_skips_the_env(wf):
    steps = _steps(wf, "drift")
    failed = steps[_index(steps, lambda s: s.get("id") == "failed")]
    assert "--label ayasee:apply-failed" in failed["run"]
    assert '--label "env:$DRIFT_ENV"' in failed["run"]
    for step in steps[_index(steps, lambda s: s.get("id") == "failed") + 1 :]:
        if step.get("id") in {"plan", "sync"} or _live_checkout(step):
            assert "steps.failed.outputs.skip == 'false'" in step["if"]


def test_plan_is_drift_mode_with_the_normal_lock(wf):
    steps = _steps(wf, "drift")
    plan = steps[_index(steps, lambda s: "/actions/plan@" in _uses(s))]
    assert plan["with"]["binding-mode"] == "drift"
    assert plan["with"]["drift"] == "true" and plan["with"]["encrypt"] == "false"
    assert "lock" not in plan["with"]  # the default takes the state lock


def test_evaluate_then_sync_then_notify(wf):
    steps = _steps(wf, "drift")
    ev = _index(steps, lambda s: "ayasee drift evaluate" in s.get("run", ""))
    sy = _index(steps, lambda s: "ayasee drift sync" in s.get("run", ""))
    assert ev < sy
    events = {s["with"]["event"]: s["if"] for s in steps if "/actions/notify@" in _uses(s)}
    assert events == {
        "drift_found": "${{ steps.sync.outputs.change == 'opened' }}",
        "drift_cleared": "${{ steps.sync.outputs.change == 'cleared' }}",
        "stage_failed": "${{ failure() }}",
    }


def test_rescan_runs_scanners_uploads_scrubbed_sarif_and_notifies(wf):
    steps = _steps(wf, "rescan")
    text = yaml.dump(steps)
    trivy = steps[_index(steps, lambda s: "trivy-action" in _uses(s))]
    assert trivy["with"]["cache"] == "false"
    assert "checkov@$CHECKOV_VERSION" in text
    assert text.count("ayasee sarif-scrub") == 2
    uploads = [s for s in steps if "upload-sarif" in _uses(s)]
    assert {u["with"]["category"] for u in uploads} == {"ayasee-trivy", "ayasee-checkov"}
    assert "trivy.sarif" in uploads[0]["with"]["sarif_file"]  # the scrubbed file, not .raw
    assert "raw" not in text.split("upload-sarif")[1]
    findings = _index(steps, lambda s: s.get("id") == "findings")
    assert "ayasee findings new" in steps[findings]["run"]
    assert "--since-hours 26" in steps[findings]["run"]
    notify = steps[_index(steps, lambda s: "/actions/notify@" in _uses(s))]
    assert notify["with"]["event"] == "new_findings"
    assert notify["if"] == "${{ steps.findings.outputs.count != '0' }}"
    assert wf["jobs"]["rescan"]["permissions"]["security-events"] == "write"
    assert wf["jobs"]["rescan"]["if"] == "${{ github.ref == 'refs/heads/main' }}"


def test_security_events_only_in_rescan_and_documented(wf):
    for name, job in wf["jobs"].items():
        scoped = "security-events" in job.get("permissions", {})
        assert scoped == (name == "rescan"), name
    assert "main is trusted" in TEXT.lower() or "Main is trusted" in TEXT


def test_every_uses_is_sha_pinned_with_one_ayasee_ref(wf):
    refs = set()
    for name, job in wf["jobs"].items():
        for step in job["steps"]:
            uses = step.get("uses")
            if uses is None:
                continue
            assert SHA_REF.search(uses), f"{name}: {uses}"
            m = AYASEE_USES.match(uses)
            if m:
                assert uses.startswith("OWNER/ayasee/actions/")
                refs.add(m.group(1))
            else:
                assert re.search(r"# v\d", TEXT[TEXT.index(uses) :].split("\n")[0]), uses
    assert len(refs) == 1
    assert "AYASEE_REF" in TEXT


def test_no_uv_cache_and_inputs_only_via_env(wf):
    assert wf["jobs"]["rescan"]["env"]["UV_NO_CACHE"] == "1"
    for job in wf["jobs"].values():
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), step.get("name")


def test_evaluate_and_sync_have_no_status_function(wf):
    for step in _steps(wf, "drift"):
        if "ayasee drift" in step.get("run", ""):
            cond = str(step.get("if", ""))
            for fn in ("always()", "failure()", "!cancelled()", "cancelled()"):
                assert fn not in cond, step.get("name")


def test_job_failure_notifies_stage_failed_for_the_drift_env(wf):
    steps = _steps(wf, "drift")
    notes = [
        s for s in steps if "/actions/notify@" in _uses(s) and s["with"]["event"] == "stage_failed"
    ]
    assert len(notes) == 1
    assert notes[0]["if"] == "${{ failure() }}"
    assert notes[0]["with"]["env"] == "drift-${{ matrix.env }}"
    others = [s for s in steps if s is not notes[0] and "failure()" in str(s.get("if", ""))]
    assert not others


def test_plan_verifies_the_checkout_is_the_ledger_sha(wf):
    steps = _steps(wf, "drift")
    plan = steps[_index(steps, lambda s: "/actions/plan@" in _uses(s))]
    assert plan["with"]["expect-sha"] == "true"


def test_header_does_not_claim_local_mode():
    head = TEXT.split("name: iac-nightly")[0]
    assert "not supported" in head and "--local" in head
    assert "(or to ./actions for in-repo runs)" not in head
