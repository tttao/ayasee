"""iac-pr-shared (issue #40, D15): the pull-request plan of the shared prod units with
gate_mechanism: workflow. A prod reviewer dispatches it on main for one head SHA; verify checks
them before any secret, plan runs that SHA in the main-only prod-pr-plan environment, comment
posts the result without running pull-request code."""

import os
import re
import subprocess
import sys

import pytest
import yaml
from conftest import AYASEE_USES, ROOT, SHA_REF, WORKFLOWS
from conftest import index as _index
from conftest import steps as _steps

TEXT = (WORKFLOWS / "iac-pr-shared.yml").read_text()
WF = yaml.safe_load(TEXT)
PR = yaml.safe_load((WORKFLOWS / "iac-pr.yml").read_text())
ON_MAIN = "github.ref == 'refs/heads/main'"
SECRET_REF = re.compile(r"secrets\.")
SENTINEL = "ayasee-fixture-secret-marker"

REAL_AYASEE = (
    f'PYTHONPATH="{ROOT / "src"}" exec "{sys.executable}" '
    '-c "from ayasee.cli import entrypoint; entrypoint()" "$@"\n'
)


def _uses(step):
    return step.get("uses", "")


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


def test_it_is_only_callable_and_holds_no_token_by_default():
    assert list(WF[True]) == ["workflow_call"]  # `on:` parses as True
    assert WF["permissions"] == {}
    inputs = WF[True]["workflow_call"]["inputs"]
    assert inputs["pr"]["required"] and inputs["sha"]["required"]
    assert "pull_request_target" not in TEXT


def test_every_job_runs_on_main_from_a_dispatch_only():
    verify = WF["jobs"]["verify"]
    assert verify["if"] == (
        "${{ github.ref == 'refs/heads/main' && github.event_name == 'workflow_dispatch' }}"
    )
    for name in ("plan", "comment"):
        assert ON_MAIN in WF["jobs"][name]["if"], name
        assert "needs.verify.result == 'success'" in WF["jobs"][name]["if"], name


def test_verify_holds_no_environment_and_no_secret():
    job = WF["jobs"]["verify"]
    assert "environment" not in job
    assert job["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "actions": "read",
    }
    assert not SECRET_REF.search(str(job))
    co = [s for s in job["steps"] if "actions/checkout@" in _uses(s)]
    # the configuration of main decides: the default ref of a dispatch on main, never the PR
    assert len(co) == 1 and "ref" not in co[0]["with"]
    assert co[0]["with"]["persist-credentials"] is False
    auth = next(s for s in job["steps"] if s.get("id") == "authorize")
    for flag in ("--pr", "--sha", "--ref", "--event", "--actor", "--triggering-actor"):
        assert flag in auth["run"]
    assert auth["env"]["TRIGGERING_ACTOR"] == "${{ github.triggering_actor }}"
    assert auth["env"]["ACTOR"] == "${{ github.actor }}"


def test_the_plan_runs_in_the_environment_verify_named_after_every_check():
    job = WF["jobs"]["plan"]
    assert job["needs"] == "verify"
    assert "needs.verify.outputs.environment != ''" in job["if"]  # never without an environment
    assert job["environment"] == "${{ needs.verify.outputs.environment }}"
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    steps = job["steps"]
    assert steps[0]["name"] == "Refuse a re-run by another person"
    co = _index(steps, lambda s: "actions/checkout@" in _uses(s))
    pinned = _index(
        steps, lambda s: s.get("name") == "Refuse a checkout that is not the authorised commit"
    )
    supported = _index(steps, lambda s: "config check-supported" in s.get("run", ""))
    login = _index(steps, lambda s: "azure/login" in _uses(s))
    plan = _index(steps, lambda s: "/actions/plan@" in _uses(s))
    assert 0 < co < pinned < supported < login < plan
    assert steps[co]["with"]["ref"] == "${{ inputs.sha }}"
    assert steps[co]["with"]["persist-credentials"] is False
    # no secret before the plan step: the login uses variables, the rest nothing
    for s in steps[:plan]:
        assert not SECRET_REF.search(str(s)), s.get("name")


def test_the_plan_is_read_only_and_never_applied():
    steps = _steps(WF, "plan")
    plan = steps[_index(steps, lambda s: "/actions/plan@" in _uses(s))]
    w = plan["with"]
    assert w["env"] == "prod" and w["binding-mode"] == "replan"
    assert w["encrypt"] == "false" and w["lock"] == "false"
    assert "key-vault-key-id" not in w and "plan-artifact" not in w
    assert not any("/actions/apply@" in _uses(s) or "/actions/ledger@" in _uses(s) for s in steps)
    assert "AYASEE_PLAN_PASSPHRASE" not in TEXT and "ledger-token" not in TEXT
    pol = steps[_index(steps, lambda s: "/actions/policy@" in _uses(s))]["with"]
    assert pol["stage"] == "prod"


def test_identity_secrets_reach_the_plan_step_only():
    steps = _steps(WF, "plan")
    with_secret = [s.get("name") or _uses(s) for s in steps if SECRET_REF.search(str(s))]
    assert len(with_secret) == 1 and "/actions/plan@" in with_secret[0]
    for job in ("verify", "comment"):
        assert not SECRET_REF.search(str(WF["jobs"][job])), job


def test_the_comment_runs_no_pull_request_code():
    job = WF["jobs"]["comment"]
    assert job["needs"] == ["verify", "plan"]
    assert job["permissions"] == {"contents": "read", "pull-requests": "write"}
    co = [s for s in job["steps"] if "actions/checkout@" in _uses(s)]
    assert len(co) == 1 and "ref" not in co[0]["with"]  # main, the dispatch's own commit
    dl = next(s for s in job["steps"] if "download-artifact" in _uses(s))
    assert dl["with"]["name"] == "ayasee-pr-comment-shared"
    post = job["steps"][-1]
    assert "ayasee pr-shared comment" in post["run"]
    assert post["env"]["APPROVER"] == "${{ needs.verify.outputs.approver }}"


def test_the_jobs_that_run_pull_request_code_hold_no_write_token():
    # R39: the plan job evaluates the PR's HCL; only the comment job may write
    for name, job in WF["jobs"].items():
        perms = job.get("permissions", {})
        writes = {k for k, v in perms.items() if v == "write"}
        if name == "plan":
            assert writes == {"id-token"}
        elif name == "comment":
            assert writes == {"pull-requests"}
        else:
            assert writes == set(), name


def test_pins_and_no_expression_in_a_run_script():
    for job in WF["jobs"].values():
        for step in job["steps"]:
            uses = _uses(step)
            if "/ayasee/actions/" in uses:
                assert AYASEE_USES.match(uses), uses
            elif uses:
                assert SHA_REF.search(uses), uses
            # inputs and outputs reach scripts through env only (no template injection)
            assert "${{" not in step.get("run", ""), step.get("name")


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
def test_the_plan_refuses_a_rerun_by_another_person(tmp_path, approver, actor, triggering, rc):
    step = _steps(WF, "plan")[0]
    assert step["env"]["TRIGGERING_ACTOR"] == "${{ github.triggering_actor }}"
    proc = _run(tmp_path, step["run"], APPROVER=approver, ACTOR=actor, TRIGGERING_ACTOR=triggering)
    assert proc.returncode == rc, proc.stdout


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_the_checkout_must_be_the_authorised_commit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@example.invalid", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", "c", cwd=repo)
    head = _git("rev-parse", "HEAD", cwd=repo)
    step = next(
        s
        for s in _steps(WF, "plan")
        if s.get("name") == "Refuse a checkout that is not the authorised commit"
    )
    assert _run(tmp_path, step["run"], cwd=repo, SHA=head).returncode == 0
    assert _run(tmp_path, step["run"], cwd=repo, SHA="b" * 40).returncode == 1


def test_the_authorize_step_maps_refusals_and_notes(tmp_path):
    step = next(s for s in _steps(WF, "verify") if s.get("id") == "authorize")
    out = tmp_path / "out.txt"
    out.write_text("")
    summary = tmp_path / "summary.md"
    env = dict(RUNNER_TEMP=str(tmp_path), GITHUB_OUTPUT=str(out), GITHUB_STEP_SUMMARY=str(summary))
    body = (
        'echo "warning: NON-COMPLIANT: solo_mode" >&2\n'
        'echo "notice: 1 commit(s) have no login" >&2\n'
        'echo "approver=rita"; echo "environment=prod-pr-plan"\n'
    )
    proc = _run(tmp_path, step["run"], body, **env)
    assert proc.returncode == 0, proc.stderr
    assert "::warning::NON-COMPLIANT: solo_mode" in proc.stdout
    assert "::notice::1 commit(s) have no login" in proc.stdout
    assert _outputs(out) == {"approver": "rita", "environment": "prod-pr-plan"}
    out.write_text("")
    proc = _run(
        tmp_path, step["run"], 'echo "approver=x"; echo "pr-shared: no" >&2; exit 1\n', **env
    )
    assert proc.returncode == 1 and "::error::no" in proc.stdout
    assert out.read_text() == ""  # a refusal hands nothing to the plan job


def _shared_repo(tmp_path, head_text, changed=True):
    """main has the configuration; the PR head changes a shared unit (or a README only)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@example.invalid", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / ".ayasee.yaml").write_text(head_text)
    _git("add", ".", cwd=repo)
    _git("commit", "-q", "-m", "base", cwd=repo)
    _git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=repo)
    _git("checkout", "-q", "-b", "pr", cwd=repo)
    (repo / "README.md").write_text("x")
    _git("add", ".", cwd=repo)
    _git("commit", "-q", "-m", "head", cwd=repo)
    return repo


@pytest.mark.parametrize("changed, any_", [(True, "true"), (False, "false")])
def test_the_changed_shared_units_are_found_against_the_merge_base(tmp_path, changed, any_):
    repo = _shared_repo(tmp_path, "stages: [dev, prod]\nshared_units: [edge]\n")
    step = next(s for s in _steps(WF, "plan") if s.get("id") == "changed")
    out = tmp_path / "out.txt"
    out.write_text("")
    log = tmp_path / "calls.txt"
    body = (
        f'if [ "$1" = changed-units ]; then echo "$@" >"{log}"; '
        + ("echo edge" if changed else "echo")
        + "; exit 0; fi\n"
        + REAL_AYASEE
    )
    proc = _run(
        tmp_path,
        step["run"],
        body,
        cwd=repo,
        CONFIG_PATH=".ayasee.yaml",
        SHA=_git("rev-parse", "HEAD", cwd=repo),
        RUNNER_TEMP=str(tmp_path),
        GITHUB_OUTPUT=str(out),
    )
    assert proc.returncode == 0, proc.stderr
    got = _outputs(out)
    assert got["any"] == any_ and got["units"] == ("edge" if changed else "")
    assert got["shared-units"] == "edge"
    base = _git("rev-parse", "main", cwd=repo)
    assert f"--base {base} --head" in log.read_text()
    assert "--env prod --shared edge" in log.read_text()


def test_nothing_is_planned_and_nothing_logs_in_without_a_changed_shared_unit():
    steps = _steps(WF, "plan")
    for s in steps:
        if "azure/login" in _uses(s) or "/actions/plan@" in _uses(s):
            assert "steps.changed.outputs.any == 'true'" in s["if"]


@pytest.mark.parametrize(
    "changed, plan, policy, expect",
    [
        ("success", "skipped", "skipped", "No shared prod unit changed"),
        ("failure", "skipped", "skipped", "failed before it started"),
        ("success", "failure", "skipped", "plan of the shared prod units failed"),
        ("success", "success", "success", "Policy pack: passed."),
        ("success", "success", "failure", "Policy pack: failed."),
    ],
)
def test_the_comment_body(tmp_path, changed, plan, policy, expect):
    step = next(s for s in _steps(WF, "plan") if s.get("id") == "body")
    summary = tmp_path / "summary.md"
    summary.write_text("1 to change\n")
    out = tmp_path / "out.txt"
    out.write_text("")
    proc = _run(
        tmp_path,
        step["run"],
        RUNNER_TEMP=str(tmp_path),
        GITHUB_OUTPUT=str(out),
        RUN_URL="https://x/run",
        CHANGED_OUTCOME=changed,
        ANY="true" if plan != "skipped" else "false",
        PLAN_OUTCOME=plan,
        POLICY_OUTCOME=policy,
        SUMMARY=str(summary),
    )
    assert proc.returncode == 0, proc.stderr
    text = (tmp_path / "pr-comment-shared.md").read_text()
    assert expect in text
    assert text.startswith("### Shared prod units plan")


def test_the_environment_name_carries_the_prefix_from_main():
    # verify prints environment=<environment_prefix of main>prod-pr-plan; the plan job uses it
    assert WF["jobs"]["verify"]["outputs"]["environment"] == (
        "${{ steps.authorize.outputs.environment }}"
    )


# ---- iac-pr in workflow mode: say what to do ---------------------------------------------------


def test_iac_pr_leaves_a_pending_comment_in_workflow_mode(tmp_path):
    job = PR["jobs"]["comment-shared"]
    assert job["needs"] == ["shared-changes", "plan-shared"]
    assert "needs.shared-changes.outputs.pending == 'true'" in job["if"]
    step = next(s for s in job["steps"] if s.get("id") == "pending")
    out = tmp_path / "pr-comment"
    proc = _run(
        tmp_path,
        step["run"],
        RUNNER_TEMP=str(tmp_path),
        PR_NUMBER="7",
        HEAD_SHA="a" * 40,
        CLOUDFLARE_API_TOKEN=SENTINEL,
    )
    assert proc.returncode == 0, proc.stderr
    text = (out / "pr-comment-shared.md").read_text()
    assert "iac-pr-shared" in text and "a" * 40 in text and "pr: 7" in text
    assert SENTINEL not in text
