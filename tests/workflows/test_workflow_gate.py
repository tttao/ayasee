"""gate_mechanism: workflow (issue #40, D15) in the workflows: iac-staged stops after prod-plan
and opens an approval issue, iac-approve checks the reviewer before any secret and applies the
bound plan, iac-pr switches the shared-unit plan off, iac-breakglass requests a break-glass plan
that iac-approve applies with the incident."""

import json
import os
import re
import subprocess
import sys

import pytest
import yaml
from conftest import AYASEE_USES, ROOT, SHA_REF, WORKFLOWS
from conftest import index as _index
from conftest import steps as _steps

STAGED = yaml.safe_load((WORKFLOWS / "iac-staged.yml").read_text())
APPROVE_TEXT = (WORKFLOWS / "iac-approve.yml").read_text()
APPROVE = yaml.safe_load(APPROVE_TEXT)
PR = yaml.safe_load((WORKFLOWS / "iac-pr.yml").read_text())
BREAKGLASS = yaml.safe_load((WORKFLOWS / "iac-breakglass.yml").read_text())
SENTINEL = "ayasee-fixture-secret-marker"

REAL_AYASEE = (
    f'PYTHONPATH="{ROOT / "src"}" exec "{sys.executable}" '
    '-c "from ayasee.cli import entrypoint; entrypoint()" "$@"\n'
)


def _run(tmp_path, script, ayasee_body=REAL_AYASEE, cwd=None, **env):
    shim = tmp_path / "shim"
    shim.mkdir(exist_ok=True)
    fake = shim / "ayasee"
    fake.write_text("#!/bin/bash\n" + ayasee_body)
    fake.chmod(0o755)
    return subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, "PATH": f"{shim}:{os.environ['PATH']}", **env},
    )


def _outputs(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


# ---- iac-staged ------------------------------------------------------------------------------


def _resolve(tmp_path, text):
    step = next(s for s in STAGED["jobs"]["resolve"]["steps"] if s.get("id") == "resolve")
    cfg = tmp_path / "ayasee.yaml"
    cfg.write_text(text)
    out = tmp_path / "out.txt"
    out.write_text("")
    proc = _run(
        tmp_path,
        step["run"],
        CONFIG_PATH=str(cfg),
        START_STAGE="",
        RUNNER_TEMP=str(tmp_path),
        GITHUB_OUTPUT=str(out),
    )
    return proc, _outputs(out)


def test_resolve_names_the_gate_mechanism(tmp_path):
    assert STAGED["jobs"]["resolve"]["outputs"]["gate-mechanism"] == (
        "${{ steps.resolve.outputs.gate-mechanism }}"
    )
    proc, out = _resolve(tmp_path, "")
    assert proc.returncode == 0 and out["gate-mechanism"] == "environment"
    proc, out = _resolve(tmp_path, "gate_mechanism: workflow\nreviewers: {prod: [rita]}\n")
    assert proc.returncode == 0 and out["gate-mechanism"] == "workflow"


def test_the_prod_job_runs_only_with_the_environment_gate():
    cond = STAGED["jobs"]["prod"]["if"]
    assert "needs.resolve.outputs.gate-mechanism != 'workflow'" in cond
    assert "needs.prod-plan.outputs.has-changes == 'true'" in cond
    # anything but workflow (an empty output included) keeps the reviewers check of prod-gate
    step = next(s for s in _steps(STAGED, "prod-gate") if "require-reviewers" in s.get("run", ""))
    assert step["if"] == "${{ needs.resolve.outputs.gate-mechanism != 'workflow' }}"


def test_prod_approval_opens_the_issue_with_no_environment_and_no_secret():
    job = STAGED["jobs"]["prod-approval"]
    assert job["needs"] == ["resolve", "prod-plan"]
    assert "github.ref == 'refs/heads/main'" in job["if"]
    assert "needs.prod-plan.outputs.has-changes == 'true'" in job["if"]
    assert "needs.resolve.outputs.gate-mechanism == 'workflow'" in job["if"]
    assert "environment" not in job
    assert job["permissions"] == {"contents": "read", "issues": "write"}
    assert "secrets." not in json.dumps(job)
    step = next(s for s in job["steps"] if "approve issue" in s.get("run", ""))
    assert step["env"]["DIGEST"] == "${{ needs.prod-plan.outputs.manifest-digest }}"
    assert step["env"]["SHA"] == "${{ github.sha }}"


def test_prod_plan_hands_on_counts_and_the_digest_only(tmp_path):
    """Sentinel: the changeset holds addresses and a value-like marker; the outputs are numbers
    and the manifest's SHA-256."""
    step = next(s for s in _steps(STAGED, "prod-plan") if s.get("id") == "summary")
    changeset = tmp_path / "changeset.json"
    changeset.write_text(
        json.dumps(
            {
                "risk": {"critical": 1, "high": 0},
                "items": [{"address": f"x.{SENTINEL}", "after": SENTINEL}, {"address": "y"}],
            }
        )
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"units": {"u": {}}}\n')
    out = tmp_path / "out.txt"
    out.write_text("")
    proc = _run(
        tmp_path,
        step["run"],
        "exit 1\n",
        CHANGESET=str(changeset),
        MANIFEST=str(manifest),
        GITHUB_OUTPUT=str(out),
    )
    assert proc.returncode == 0, proc.stderr
    got = _outputs(out)
    assert got["changes"] == "2" and got["critical"] == "1" and got["high"] == "0"
    import hashlib

    assert got["manifest-digest"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert SENTINEL not in out.read_text() + proc.stdout + proc.stderr


# ---- iac-approve -----------------------------------------------------------------------------


def test_iac_approve_is_only_callable():
    on = APPROVE[True]
    assert set(on) == {"workflow_call"}
    assert set(on["workflow_call"]["inputs"]) == {
        "sha",
        "manifest-digest",
        "incident",
        "tool-mirror",
        "config-path",
    }
    assert on["workflow_call"]["inputs"]["incident"]["default"] == ""
    staged_secrets = set(STAGED[True]["workflow_call"]["secrets"])
    assert set(on["workflow_call"]["secrets"]) <= staged_secrets
    assert all(v["required"] is False for v in on["workflow_call"]["secrets"].values())
    assert APPROVE["permissions"] == {}
    assert "inherit" not in APPROVE_TEXT


def test_verify_holds_no_environment_and_no_secret():
    job = APPROVE["jobs"]["verify"]
    assert job["if"] == (
        "${{ github.ref == 'refs/heads/main' && github.event_name == 'workflow_dispatch' }}"
    )
    assert "environment" not in job
    assert job["permissions"] == {
        "contents": "read",
        "actions": "read",
        "pull-requests": "read",
    }
    assert "secrets." not in json.dumps(job)
    step = next(s for s in job["steps"] if s.get("id") == "authorize")
    # the actor that chose the inputs and the one that started this attempt, both checked
    assert step["env"]["ACTOR"] == "${{ github.actor }}"
    assert step["env"]["TRIGGERING_ACTOR"] == "${{ github.triggering_actor }}"
    assert step["env"]["REF"] == "${{ github.ref }}"
    assert step["env"]["HEAD"] == "${{ github.sha }}"
    steps = job["steps"]
    checkout = _index(steps, lambda s: "actions/checkout" in s.get("uses", ""))
    assert "ref" not in steps[checkout].get("with", {})  # main, never the commit to approve
    authorize = _index(steps, lambda s: s.get("id") == "authorize")
    download = _index(steps, lambda s: "download-artifact" in s.get("uses", ""))
    digest = _index(steps, lambda s: "approve digest" in s.get("run", ""))
    assert checkout < authorize < download < digest
    assert steps[download]["with"]["run-id"] == "${{ steps.authorize.outputs.run-id }}"


def test_apply_runs_in_prod_after_verify_with_the_checks_before_any_secret():
    job = APPROVE["jobs"]["apply"]
    assert job["needs"] == "verify"
    assert "github.ref == 'refs/heads/main'" in job["if"]
    assert "needs.verify.result == 'success'" in job["if"]
    assert "inputs.incident == ''" in job["if"]
    assert "needs.verify.outputs.kind == 'prod'" in job["if"]
    assert job["environment"] == "${{ needs.verify.outputs.env-prod }}"
    assert job["concurrency"] == {
        "group": "ayasee-${{ github.repository }}-prod",  # one queue with the staged prod jobs
        "cancel-in-progress": False,
    }
    assert job["permissions"] == {
        "id-token": "write",
        "contents": "read",
        "deployments": "write",
        "issues": "write",
        "actions": "read",
        "pull-requests": "read",
    }
    steps = job["steps"]
    guard = _index(steps, lambda s: "/actions/guard@" in s.get("uses", ""))
    baseline = _index(steps, lambda s: "approve baseline" in s.get("run", ""))
    download = _index(steps, lambda s: "download-artifact" in s.get("uses", ""))
    digest = _index(steps, lambda s: "approve digest" in s.get("run", ""))
    apply = _index(steps, lambda s: "/actions/apply@" in s.get("uses", ""))
    ledger = _index(steps, lambda s: "/actions/ledger@" in s.get("uses", ""))
    assert guard < baseline < download < digest < apply < ledger
    assert steps[guard]["with"]["sha"] == "${{ inputs.sha }}"
    assert steps[apply]["with"]["binding-mode"] == "encrypted_plan"
    assert steps[apply]["env"]["SG_SHA"] == "${{ inputs.sha }}"
    assert steps[ledger]["with"]["approver"] == "${{ needs.verify.outputs.approver }}"
    assert steps[ledger]["with"]["sha"] == "${{ inputs.sha }}"
    # provider and plan-key secrets reach the apply step only; ledger keys the ledger steps only
    ledger_keys = {"ledger-token", "AYASEE_LEDGER_AWS_ACCESS_KEY_ID"}
    ledger_keys.add("AYASEE_LEDGER_AWS_SECRET_ACCESS_KEY")
    for i, step in enumerate(steps):
        names = set(re.findall(r"secrets\.([A-Za-z_-]+)", json.dumps(step)))
        if i == apply:
            continue
        if names - {"teams-webhook"}:
            assert names <= ledger_keys, (step.get("name") or step.get("uses"), names)
            assert i in (guard, baseline, ledger)


def test_iac_approve_pins_and_runs_no_expression():
    refs = set()
    for name, job in APPROVE["jobs"].items():
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), f"{name}: {step.get('name')}"
            uses = step.get("uses")
            if uses:
                assert SHA_REF.search(uses), uses
                m = AYASEE_USES.match(uses)
                if m:
                    refs.add(m.group(1))
    assert len(refs) == 1 and "AYASEE_REF" in APPROVE_TEXT
    for line in APPROVE_TEXT.splitlines():
        if "uses:" in line and "/ayasee/actions/" not in line:
            assert re.search(r"@[0-9a-f]{40} # v\d", line), line


AUTHORIZE = next(s for s in APPROVE["jobs"]["verify"]["steps"] if s.get("id") == "authorize")


def test_the_authorize_step_maps_refusals_and_notes(tmp_path):
    out = tmp_path / "out.txt"
    out.write_text("")
    summary = tmp_path / "summary.md"
    env = dict(RUNNER_TEMP=str(tmp_path), GITHUB_OUTPUT=str(out), GITHUB_STEP_SUMMARY=str(summary))
    body = (
        'echo "warning: DEVIATION: no branch policy" >&2\n'
        'echo "notice: first approval" >&2\n'
        'echo "run-id=20"; echo "approver=rita"\n'
    )
    proc = _run(tmp_path, AUTHORIZE["run"], body, **env)
    assert proc.returncode == 0, proc.stderr
    assert "::warning::DEVIATION: no branch policy" in proc.stdout
    assert "::notice::first approval" in proc.stdout
    assert _outputs(out) == {"run-id": "20", "approver": "rita"}
    out.write_text("")
    proc = _run(
        tmp_path, AUTHORIZE["run"], 'echo "run-id=1"; echo "approve: no" >&2; exit 1\n', **env
    )
    assert proc.returncode == 1 and "::error::no" in proc.stdout
    assert out.read_text() == ""  # a refusal hands nothing to the apply job


# ---- iac-pr: the shared-unit plan moves to iac-pr-shared with the workflow gate -----------------


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


SHARED = "stages: [dev, prod]\nshared_units: [edge]\n"
WORKFLOW = "gate_mechanism: workflow\nreviewers: {prod: [rita]}\n"


@pytest.mark.parametrize(
    "base, head, planned",
    [
        (SHARED, SHARED, True),
        (SHARED + WORKFLOW, SHARED + WORKFLOW, False),
        (SHARED + WORKFLOW, SHARED, False),  # a PR cannot switch the plan back on
        (SHARED, SHARED + WORKFLOW, False),
        (None, SHARED, True),  # no configuration on the base: the defaults
    ],
    ids=["environment", "workflow", "pr-switches-back", "pr-switches-to-workflow", "new-file"],
)
def test_shared_changes_switches_the_plan_off_with_the_workflow_gate(tmp_path, base, head, planned):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@example.invalid", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "README").write_text("x")
    if base is not None:
        (repo / ".ayasee.yaml").write_text(base)
    _git("add", ".", cwd=repo)
    _git("commit", "-q", "-m", "base", cwd=repo)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    (repo / ".ayasee.yaml").write_text(head)
    _git("add", ".", cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", "head", cwd=repo)
    step = next(s for s in _steps(PR, "shared-changes") if s.get("id") == "shared")
    out = tmp_path / "out.txt"
    out.write_text("")
    # changed-units is the one call that is faked: it reports the shared unit as changed
    body = 'if [ "$1" = changed-units ]; then echo edge; exit 0; fi\n' + REAL_AYASEE
    proc = _run(
        tmp_path,
        step["run"],
        body,
        cwd=repo,
        CONFIG_PATH=".ayasee.yaml",
        BASE_SHA=base_sha,
        HEAD_SHA="HEAD",
        RUNNER_TEMP=str(tmp_path),
        GITHUB_OUTPUT=str(out),
        GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"),
    )
    assert proc.returncode == 0, proc.stderr
    got = _outputs(out)
    assert got["units"] == ("edge" if planned else "")
    assert got["pending"] == ("false" if planned else "true")  # a reviewer starts iac-pr-shared
    assert ("::warning::gate_mechanism: workflow" in proc.stdout) is not planned


# ---- iac-breakglass with the workflow gate: a request, approved with iac-approve -------------

BG_CFG = "gate_mechanism: workflow\nreviewers: {prod: [rita], breakglass: [bruno]}\n"


@pytest.mark.parametrize(
    "text, rc, gate",
    [
        ("", 0, "environment"),
        (BG_CFG, 0, "workflow"),
        (WORKFLOW, 1, None),  # nobody could approve: refused before anything runs
    ],
)
def test_breakglass_reads_the_gate_and_its_reviewers_from_main(tmp_path, text, rc, gate):
    step = next(s for s in _steps(BREAKGLASS, "validate") if s.get("id") == "prefix")
    cfg = tmp_path / "ayasee.yaml"
    cfg.write_text(text)
    out = tmp_path / "out.txt"
    out.write_text("")
    proc = _run(
        tmp_path,
        step["run"],
        CONFIG_PATH=str(cfg),
        RUNNER_TEMP=str(tmp_path),
        GITHUB_OUTPUT=str(out),
    )
    assert proc.returncode == rc, proc.stderr
    if gate:
        assert _outputs(out)["gate-mechanism"] == gate
    else:
        assert "::error::gate_mechanism: workflow, but reviewers.breakglass" in proc.stdout


def test_breakglass_jobs_split_on_the_gate_of_main():
    jobs = BREAKGLASS["jobs"]
    assert jobs["validate"]["outputs"]["gate-mechanism"] == (
        "${{ steps.prefix.outputs.gate-mechanism }}"
    )
    # anything but workflow (an empty output included) keeps the environment gate and its check
    assert jobs["breakglass"]["if"] == "${{ needs.validate.outputs.gate-mechanism != 'workflow' }}"
    reviewers = next(
        s for s in _steps(BREAKGLASS, "validate") if "require-reviewers" in s.get("run", "")
    )
    assert reviewers["if"] == "${{ steps.prefix.outputs.gate-mechanism != 'workflow' }}"
    assert jobs["request"]["if"] == "${{ needs.validate.outputs.gate-mechanism == 'workflow' }}"
    assert "needs.validate.outputs.gate-mechanism == 'workflow'" in jobs["request-issue"]["if"]


def test_the_request_plans_with_the_plan_identity_and_applies_nothing():
    job = BREAKGLASS["jobs"]["request"]
    assert job["environment"] == "${{ needs.validate.outputs.environment-prefix }}prod-plan"
    assert job["permissions"] == {"id-token": "write", "contents": "read"}
    assert job["concurrency"]["group"] == "ayasee-${{ github.repository }}-prod-plan"
    steps = job["steps"]
    uses = [s.get("uses", "") for s in steps]
    assert not any("/actions/apply@" in u or "/actions/ledger@" in u for u in uses)
    ancestor = _index(
        steps, lambda s: 'merge-base --is-ancestor "$SHA" origin/main' in s.get("run", "")
    )
    live = _index(steps, lambda s: s.get("id") == "live")
    plan = _index(steps, lambda s: "/actions/plan@" in s.get("uses", ""))
    policy = _index(steps, lambda s: "/actions/policy@" in s.get("uses", ""))
    summary = _index(steps, lambda s: s.get("id") == "summary")
    upload = _index(steps, lambda s: "upload-artifact" in s.get("uses", ""))
    assert ancestor < live < plan < policy < summary < upload
    assert steps[plan]["with"]["binding-mode"] == "encrypted_plan"
    assert steps[plan]["with"]["encrypt"] == "true"
    assert steps[plan]["with"]["expect-sha"] == "true"
    assert "only-units" not in steps[plan]["with"]  # R71: the whole prod tree
    assert steps[upload]["with"]["name"] == (
        "ayasee-breakglass-plan-${{ needs.validate.outputs.sha }}"
    )
    assert "request-path" in steps[upload]["with"]["path"]
    assert "json-dir" not in steps[upload]["with"]["path"]  # plan JSON never leaves the job
    # provider and plan-key secrets reach the plan step only; ledger keys the rollback check
    for i, step in enumerate(steps):
        names = set(re.findall(r"secrets\.([A-Za-z_-]+)", json.dumps(step)))
        if i == plan:
            continue
        assert names <= {
            "ledger-token",
            "AYASEE_LEDGER_AWS_ACCESS_KEY_ID",
            "AYASEE_LEDGER_AWS_SECRET_ACCESS_KEY",
        }, (step.get("name") or step.get("uses"), names)
        if names:
            assert i == live


def test_the_request_issue_job_has_no_environment_and_no_plan_secret():
    job = BREAKGLASS["jobs"]["request-issue"]
    assert "environment" not in job
    assert job["permissions"] == {"contents": "read", "issues": "write"}
    assert set(re.findall(r"secrets\.([A-Za-z_-]+)", json.dumps(job))) <= {"teams-webhook"}
    step = next(s for s in job["steps"] if "breakglass-issue" in s.get("run", ""))
    assert step["env"]["DIGEST"] == "${{ needs.request.outputs.manifest-digest }}"
    assert step["env"]["REQUESTER"] == "${{ github.actor }}"


def test_the_request_summary_writes_counts_and_the_digest_only(tmp_path):
    """Sentinel: the change set holds a value-like marker; request.json and the outputs carry
    the incident, the SHA, allow-rollback, counts and the manifest's SHA-256 only."""
    import hashlib

    step = next(s for s in _steps(BREAKGLASS, "request") if s.get("id") == "summary")
    changeset = tmp_path / "changeset.json"
    changeset.write_text(
        json.dumps(
            {
                "risk": {"critical": 1, "high": 0, "medium": 2},
                "items": [{"address": f"x.{SENTINEL}", "after": SENTINEL}, {"address": "y"}],
            }
        )
    )
    plan_dir = tmp_path / "prod"
    plan_dir.mkdir()
    manifest = plan_dir / "manifest.json"
    manifest.write_text('{"units": {"u": {}}}\n')
    out = tmp_path / "out.txt"
    out.write_text("")
    sha = "a" * 40
    proc = _run(
        tmp_path,
        step["run"],
        "exit 1\n",
        CHANGESET=str(changeset),
        MANIFEST=str(manifest),
        INCIDENT="INC42",
        SHA=sha,
        ALLOW_ROLLBACK="true",
        GITHUB_OUTPUT=str(out),
    )
    assert proc.returncode == 0, proc.stderr
    got = _outputs(out)
    assert got["manifest-digest"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert (got["critical"], got["high"], got["medium"], got["total"]) == ("1", "0", "2", "2")
    request = json.loads((plan_dir / "request.json").read_text())
    assert request == {
        "schema": "ayasee.breakglass-request/v1",
        "incident": "INC42",
        "sha": sha,
        "allow_rollback": True,
        "counts": {"critical": 1, "high": 0, "medium": 2, "total": 2},
    }
    from ayasee.approve import check_request_file

    assert check_request_file(plan_dir / "request.json", incident="INC42", sha=sha).allow_rollback
    text = out.read_text() + proc.stdout + proc.stderr + (plan_dir / "request.json").read_text()
    assert SENTINEL not in text


def test_breakglass_approval_runs_in_prod_breakglass_with_the_checks_before_any_secret():
    job = APPROVE["jobs"]["breakglass"]
    assert job["needs"] == "verify"
    for cond in (
        "github.ref == 'refs/heads/main'",
        "needs.verify.result == 'success'",
        "inputs.incident != ''",
        "needs.verify.outputs.kind == 'breakglass'",
    ):
        assert cond in job["if"]
    assert job["environment"] == "${{ needs.verify.outputs.env-breakglass }}"
    assert job["concurrency"] == {
        "group": "ayasee-${{ github.repository }}-prod",
        "cancel-in-progress": False,
    }
    assert job["permissions"] == APPROVE["jobs"]["apply"]["permissions"]
    steps = job["steps"]
    assert steps[0]["name"] == "Refuse a re-run by another person"
    assert steps[0]["run"] == APPROVE["jobs"]["apply"]["steps"][0]["run"]
    # no guard: stop-the-line is not enforced on the emergency path, as with the environment gate
    assert not any("/actions/guard@" in s.get("uses", "") for s in steps)
    ancestor = _index(
        steps, lambda s: 'merge-base --is-ancestor "$SHA" origin/main' in s.get("run", "")
    )
    download = _index(steps, lambda s: "download-artifact" in s.get("uses", ""))
    request = _index(steps, lambda s: s.get("id") == "request")
    live = _index(steps, lambda s: s.get("id") == "live")
    baseline = _index(steps, lambda s: "approve baseline" in s.get("run", ""))
    apply = _index(steps, lambda s: "/actions/apply@" in s.get("uses", ""))
    ledger = _index(steps, lambda s: "/actions/ledger@" in s.get("uses", ""))
    post = _index(steps, lambda s: "issue post-approval" in s.get("run", ""))
    assert ancestor < download < request < live < baseline < apply < ledger < post
    assert steps[download]["with"]["name"] == "${{ needs.verify.outputs.artifact }}"
    assert steps[download]["with"]["run-id"] == "${{ needs.verify.outputs.run-id }}"
    assert "approve digest" in steps[request]["run"] and "approve request" in steps[request]["run"]
    assert "--role breakglass" in steps[baseline]["run"]
    assert (
        steps[baseline]["env"]["PLAN_STARTED_AT"] == "${{ needs.verify.outputs.plan-started-at }}"
    )
    assert steps[live]["env"]["ALLOW_ROLLBACK"] == "${{ steps.request.outputs.allow-rollback }}"
    assert steps[apply]["with"]["binding-mode"] == "encrypted_plan"
    assert steps[apply]["env"]["SG_SHA"] == "${{ inputs.sha }}"
    w = steps[ledger]["with"]
    assert w["approver"] == "${{ needs.verify.outputs.approver }}"
    assert w["requester"] == "${{ needs.verify.outputs.requester }}"
    assert w["breakglass-incident"] == "${{ inputs.incident }}"
    assert (
        "always()" in steps[post]["if"] and "steps.apply.outcome != 'skipped'" in steps[post]["if"]
    )
    ledger_keys = {"ledger-token", "AYASEE_LEDGER_AWS_ACCESS_KEY_ID"}
    ledger_keys.add("AYASEE_LEDGER_AWS_SECRET_ACCESS_KEY")
    for i, step in enumerate(steps):
        names = set(re.findall(r"secrets\.([A-Za-z_-]+)", json.dumps(step)))
        if i == apply:
            continue
        if names - {"teams-webhook"}:
            assert names <= ledger_keys, (step.get("name") or step.get("uses"), names)
            assert i in (live, baseline, ledger)


def test_verify_downloads_the_artifact_that_authorize_named():
    steps = APPROVE["jobs"]["verify"]["steps"]
    authorize = next(s for s in steps if s.get("id") == "authorize")
    assert authorize["env"]["INCIDENT"] == "${{ inputs.incident }}"
    assert '--incident "$INCIDENT"' in authorize["run"]
    download = next(s for s in steps if "download-artifact" in s.get("uses", ""))
    assert download["with"]["name"] == "${{ steps.authorize.outputs.artifact }}"
    outputs = APPROVE["jobs"]["verify"]["outputs"]
    for key in ("kind", "artifact", "plan-started-at", "requester", "env-breakglass"):
        assert outputs[key] == f"${{{{ steps.authorize.outputs.{key} }}}}"


@pytest.mark.parametrize(
    "kind, incident, body, rc",
    [
        ("prod", "", "exit 0\n", 0),
        ("prod", "INC1", "exit 0\n", 1),  # a prod plan is never approved as a break-glass one
        ("breakglass", "INC1", "exit 0\n", 0),
        ("breakglass", "INC1", 'if [ "$2" = request ]; then exit 1; fi\n', 1),
        ("", "", "exit 0\n", 1),
    ],
)
def test_verify_checks_the_request_of_a_breakglass_plan(tmp_path, kind, incident, body, rc):
    step = next(
        s for s in APPROVE["jobs"]["verify"]["steps"] if "approve digest" in s.get("run", "")
    )
    # the shim gets "approve <action> ...": $1 is approve, $2 the action
    proc = _run(
        tmp_path,
        step["run"],
        body,
        RUNNER_TEMP=str(tmp_path),
        GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"),
        GITHUB_ACTOR="bruno",
        SHA="a" * 40,
        DIGEST="d" * 64,
        INCIDENT=incident,
        KIND=kind,
    )
    assert proc.returncode == rc, proc.stdout + proc.stderr


@pytest.mark.parametrize(
    "out, rc",
    [("allow-rollback=true", 0), ("allow-rollback=false", 0), ("allow-rollback=maybe", 1)],
)
def test_the_breakglass_request_step_fails_closed(tmp_path, out, rc):
    step = next(s for s in APPROVE["jobs"]["breakglass"]["steps"] if s.get("id") == "request")
    gh_out = tmp_path / "out.txt"
    gh_out.write_text("")
    body = f'if [ "$2" = request ]; then echo "{out}"; fi\nexit 0\n'
    proc = _run(
        tmp_path,
        step["run"],
        body,
        RUNNER_TEMP=str(tmp_path),
        GITHUB_OUTPUT=str(gh_out),
        SHA="a" * 40,
        DIGEST="d" * 64,
        INCIDENT="INC1",
    )
    assert proc.returncode == rc
    if rc == 0:
        assert _outputs(gh_out)["allow-rollback"] == out.split("=")[1]


@pytest.mark.parametrize(
    "approver, actor, triggering, rc",
    [
        ("rita", "rita", "rita", 0),
        ("Rita", "rita", "RITA", 0),
        ("rita", "rita", "mallory", 1),  # "re-run failed jobs" by another person
        ("rita", "mallory", "mallory", 1),
        ("", "rita", "rita", 1),
    ],
)
def test_apply_refuses_a_rerun_by_another_person(tmp_path, approver, actor, triggering, rc):
    steps = APPROVE["jobs"]["apply"]["steps"]
    assert steps[0]["name"] == "Refuse a re-run by another person"
    assert steps[0]["env"]["TRIGGERING_ACTOR"] == "${{ github.triggering_actor }}"
    proc = _run(
        tmp_path, steps[0]["run"], APPROVER=approver, ACTOR=actor, TRIGGERING_ACTOR=triggering
    )
    assert proc.returncode == rc, proc.stdout


# ---- a change of the gate settings is never promoted in sync (D15) ----------------------------


def test_a_gate_change_routes_to_an_approval_not_to_in_sync():
    changed = "needs.prod-plan.outputs.gate-changed == 'true'"
    assert STAGED["jobs"]["prod-plan"]["outputs"]["gate-changed"] == (
        "${{ steps.gate.outputs.gate-changed }}"
    )
    for job in ("prod", "prod-approval", "prod-result"):
        assert changed in STAGED["jobs"][job]["if"], job
    assert "needs.prod-plan.outputs.gate-changed == 'false'" in STAGED["jobs"]["prod-in-sync"]["if"]
    steps = _steps(STAGED, "prod-plan")
    guard = _index(steps, lambda s: "/actions/guard@" in s.get("uses", ""))
    gate = _index(steps, lambda s: s.get("id") == "gate")
    plan = _index(steps, lambda s: "/actions/plan@" in s.get("uses", ""))
    assert guard < gate < plan
    assert not re.search(
        r"secrets\.(AYASEE_PLAN|CLOUDFLARE|AWS_ACCESS|AYASEE_KMS)", str(steps[gate])
    )


@pytest.mark.parametrize(
    "body, rc, output",
    [
        ('echo "gate-changed=true"\n', 0, "gate-changed=true"),
        ('echo "gate-changed=false"\n', 0, "gate-changed=false"),
        ('echo "gate-changed=maybe"\n', 1, ""),
        ('echo "approve: cannot read the prod ledger" >&2; exit 1\n', 1, ""),
    ],
)
def test_the_gate_step_fails_closed(tmp_path, body, rc, output):
    step = next(s for s in _steps(STAGED, "prod-plan") if s.get("id") == "gate")
    out = tmp_path / "out.txt"
    out.write_text("")
    proc = _run(
        tmp_path,
        step["run"],
        body,
        AYASEE_LEDGER_URL="file:///x",
        REPO="o/r",
        CONFIG_PATH=".ayasee.yaml",
        RUNNER_TEMP=str(tmp_path),
        GITHUB_OUTPUT=str(out),
    )
    assert proc.returncode == rc
    assert out.read_text().strip() == output
