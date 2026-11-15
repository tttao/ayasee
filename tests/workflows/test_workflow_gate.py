"""gate_mechanism: workflow (issue #40, D15) in the workflows: iac-staged stops after prod-plan
and opens an approval issue, iac-approve checks the reviewer before any secret and applies the
bound plan, iac-pr switches the shared-unit plan off, iac-breakglass refuses."""

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
        "tool-mirror",
        "config-path",
    }
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


# ---- iac-pr: no shared-unit plan with the workflow gate ----------------------------------------


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
    assert ("::warning::gate_mechanism: workflow" in proc.stdout) is not planned


# ---- iac-breakglass: refused with the workflow gate --------------------------------------------


def test_breakglass_refuses_the_workflow_gate(tmp_path):
    step = next(s for s in _steps(BREAKGLASS, "validate") if s.get("id") == "prefix")
    cfg = tmp_path / "ayasee.yaml"
    out = tmp_path / "out.txt"
    for text, rc in (("", 0), (WORKFLOW, 1)):
        cfg.write_text(text)
        out.write_text("")
        proc = _run(
            tmp_path,
            step["run"],
            CONFIG_PATH=str(cfg),
            RUNNER_TEMP=str(tmp_path),
            GITHUB_OUTPUT=str(out),
        )
        assert proc.returncode == rc, proc.stderr
    assert "::error::break-glass is not available with gate_mechanism: workflow" in proc.stdout


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
