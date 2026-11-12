"""Static checks on the iac-breakglass reusable workflow (R44, DORA RTS 2024/1774 Art. 17(1)(g))."""

import re
import subprocess

import pytest
import yaml
from conftest import AYASEE_USES, SHA_REF, WORKFLOWS
from conftest import index as _index
from conftest import steps as _steps

PATH = WORKFLOWS / "iac-breakglass.yml"
TEXT = PATH.read_text()
JOB = "breakglass"
INCIDENT_RE = r"^[A-Z]+-[0-9]+$|^INC[0-9]+$"


@pytest.fixture(scope="module")
def wf() -> dict:
    return yaml.safe_load(TEXT)


def _uses(step):
    return step.get("uses", "")


def _run(step):
    return step.get("run", "")


def _idx(wf, pred, job=JOB):
    return _index(_steps(wf, job), pred)


def _ayasee(action):
    return lambda s: f"/actions/{action}@" in _uses(s)


def _ancestor(s):
    return "merge-base --is-ancestor" in _run(s)


def test_triggers_and_inputs(wf):
    assert set(wf[True]) == {"workflow_call", "workflow_dispatch"}
    assert wf["permissions"] == {}
    for trigger in ("workflow_call", "workflow_dispatch"):
        inputs = wf[True][trigger]["inputs"]
        assert inputs["incident"]["required"] is True
        assert inputs["sha"]["required"] is True
        assert "units" not in inputs  # R71: break-glass always plans the whole prod tree
        assert inputs["allow-rollback"]["default"] == "false"


def test_incident_required(wf):
    steps = _steps(wf, "validate")
    step = steps[_idx(wf, lambda s: "INCIDENT" in s.get("env", {}), "validate")]
    assert step["env"]["INCIDENT"] == "${{ inputs.incident }}"
    assert "inputs." not in _run(step)
    assert "^[A-Z]+-[0-9]+$" in _run(step) and "^INC[0-9]+$" in _run(step)
    assert "exit 1" in _run(step)
    # the regex is the framework one (same as sg_check_mode and the issue command)
    lib = (WORKFLOWS.parents[1] / "actions" / "lib" / "ayasee-lib.sh").read_text()
    assert "^[A-Z]+-[0-9]+$" in lib and "^INC[0-9]+$" in lib
    assert re.fullmatch(INCIDENT_RE, "INC123") and not re.fullmatch(INCIDENT_RE, "inc123")


def test_validate_checks_sha(wf):
    step = _steps(wf, "validate")[_idx(wf, lambda s: "SHA" in s.get("env", {}), "validate")]
    assert "[0-9a-f]{40}" in _run(step)
    assert "UNITS" not in step["env"] and "UNITS" not in _run(step)
    assert "units" not in wf["jobs"]["validate"]["outputs"]
    assert wf["jobs"]["validate"]["permissions"] == {"contents": "read"}
    assert "environment" not in wf["jobs"]["validate"]


def test_uses_breakglass_environment(wf):
    job = wf["jobs"][JOB]
    assert job["environment"] == "prod-breakglass"
    for name, j in wf["jobs"].items():
        env = j.get("environment")
        assert env in (None, "prod-breakglass"), name
    assert "environment: prod\n" not in TEXT
    assert job["concurrency"] == {
        "group": "ayasee-${{ github.repository }}-prod",
        "cancel-in-progress": False,
    }
    assert job["permissions"] == {
        "id-token": "write",
        "contents": "read",
        "deployments": "write",
        "issues": "write",
        "actions": "read",
    }
    assert "validate" in job["needs"]
    # a dispatch from another ref fails loudly instead of skipping
    assert "if" not in wf["jobs"]["validate"]
    first = _steps(wf, "validate")[0]
    assert first["env"]["REF"] == "${{ github.ref }}" and "exit 1" in _run(first)
    assert "refs/heads/main" in _run(first)


def test_prod_vars_come_from_the_breakglass_environment(wf):
    for var in ("AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID"):
        assert f"vars.{var}" in TEXT
    plan = _steps(wf, JOB)[_idx(wf, _ayasee("plan"))]
    oidc = "steps.cfg.outputs.identity == 'oidc'"
    assert plan["env"]["ARM_CLIENT_ID"] == f"${{{{ {oidc} && vars.AZURE_CLIENT_ID || '' }}}}"
    assert plan["with"]["key-vault-key-id"] == "${{ vars.AYASEE_KEY_VAULT_KEY_ID }}"


def test_opens_post_approval_issue_step(wf):
    steps = _steps(wf, JOB)
    step = steps[_idx(wf, lambda s: "ayasee issue post-approval" in _run(s))]
    assert step["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    for flag in ("--repo", "--incident", "--sha", "--run-url", "--approvers"):
        assert flag in _run(step)
    assert "inputs." not in _run(step) and "${{" not in _run(step)


def test_post_approval_runs_always_after_validation_and_the_apply_started(wf):
    steps = _steps(wf, JOB)
    i = _idx(wf, lambda s: "ayasee issue post-approval" in _run(s))
    cond = steps[i]["if"]
    assert "always()" in cond
    assert "steps.apply.outcome != 'skipped'" in cond  # never when nothing was applied
    assert i > _idx(wf, _ancestor)
    assert i > _idx(wf, lambda s: s.get("id") == "apply")
    assert wf["jobs"][JOB]["needs"] == ["validate"] or "validate" in wf["jobs"][JOB]["needs"]
    # the issue is opened before the ledger/notify steps can fail the job
    assert i < _idx(wf, _ayasee("notify"))


def test_no_guard_step_by_design(wf):
    for job in wf["jobs"].values():
        for s in job["steps"]:
            assert "/actions/guard@" not in _uses(s)
            assert "ayasee guard" not in _run(s)
    assert "stop-the-line is intentionally not enforced" in TEXT


def test_failed_apply_opens_the_stop_the_line_issue_after_the_ledger(wf):
    steps = _steps(wf, JOB)
    i = _idx(wf, lambda s: "ayasee issue stop-the-line" in _run(s))
    cond = steps[i]["if"]
    assert "failure()" in cond and "steps.apply.outcome == 'failure'" in cond
    assert "steps.units.outputs.units-json != ''" in cond
    assert "--env prod" in _run(steps[i])
    assert steps[i]["env"]["SHA"] == "${{ needs.validate.outputs.sha }}"
    assert _idx(wf, _ayasee("ledger")) < i


def test_prod_failed_notification_on_failure(wf):
    failed = [
        s for s in _steps(wf, JOB) if _ayasee("notify")(s) and s["with"]["event"] == "prod_failed"
    ]
    assert len(failed) == 1 and "failure()" in failed[0]["if"]
    assert failed[0]["with"]["env"] == "prod"


def test_policy_pack_runs_between_plan_and_apply(wf):
    steps = _steps(wf, JOB)
    i = _idx(wf, _ayasee("policy"))
    assert _idx(wf, _ayasee("plan")) < i < _idx(wf, _ayasee("apply"))
    assert steps[i]["with"]["stage"] == "prod"
    assert steps[i]["with"]["plan-json"] == "${{ steps.plan.outputs.json-dir }}"
    assert "if" not in steps[i] and "continue-on-error" not in steps[i]
    assert "trivy" not in TEXT.lower().replace("trivy is not run", "")


def test_post_approval_step_passes_counts_and_rollback(wf):
    step = _steps(wf, JOB)[_idx(wf, lambda s: "ayasee issue post-approval" in _run(s))]
    assert step["env"]["CHANGESET"] == "${{ steps.plan.outputs.changeset-path }}"
    assert step["env"]["ROLLBACK_FROM"] == "${{ steps.live.outputs.rollback-from }}"
    assert "--changeset" in _run(step) and "--rollback-from" in _run(step)


def test_rollback_needs_explicit_opt_in(wf):
    steps = _steps(wf, JOB)
    i = _idx(wf, lambda s: s.get("id") == "live")
    run = _run(steps[i])
    assert "ayasee ledger show" in run and "--env prod" in run
    assert 'merge-base --is-ancestor "$live" "$SHA"' in run
    assert '"$ALLOW_ROLLBACK" = true' in run
    assert "::warning::rollback" in run
    assert "first deploy" in run  # no record: no check
    assert steps[i]["env"]["ALLOW_ROLLBACK"] == "${{ needs.validate.outputs.allow-rollback }}"
    assert _idx(wf, _ancestor) < i < _idx(wf, lambda s: s.get("id") == "approver")
    assert i > _idx(wf, lambda s: "azure/login@" in _uses(s))  # the ledger store may need it
    # allow-rollback is validated in the first job
    val = _steps(wf, "validate")[_idx(wf, lambda s: s.get("id") == "validate", "validate")]
    assert "ALLOW_ROLLBACK" in _run(val) and "true | false" in _run(val)


def test_ancestor_check_tells_missing_main_from_not_ancestor(wf):
    run = _run(_steps(wf, JOB)[_idx(wf, _ancestor)])
    assert "rev-parse --verify" in run and "origin/main was not fetched" in run
    assert "is not an ancestor" in run and "cannot check" in run


def test_ledger_url_variable_is_required_on_the_environment(wf):
    first = _steps(wf, JOB)[0]
    assert "AYASEE_LEDGER_URL" in _run(first) and "exit 1" in _run(first)
    assert wf["jobs"][JOB]["env"]["AYASEE_LEDGER_URL"] == "${{ vars.AYASEE_LEDGER_URL }}"


def test_ledger_and_units_steps_run_even_when_cancelled(wf):
    steps = _steps(wf, JOB)
    assert steps[_idx(wf, lambda s: s.get("id") == "units")]["if"] == "${{ always() }}"
    cond = steps[_idx(wf, _ayasee("ledger"))]["if"]
    assert "always()" in cond and "units-json != ''" in cond


def test_ancestor_check_precedes_the_plan(wf):
    steps = _steps(wf, JOB)
    check = steps[_idx(wf, _ancestor)]
    assert "origin/main" in _run(check)
    assert "SHA" in check["env"] and "inputs." not in _run(check)
    checkout = steps[_idx(wf, lambda s: "actions/checkout@" in _uses(s))]
    assert checkout["with"]["ref"] == "${{ needs.validate.outputs.sha }}"
    assert checkout["with"]["fetch-depth"] == 0
    assert checkout["with"]["persist-credentials"] is False
    assert _idx(wf, lambda s: "actions/checkout@" in _uses(s)) < _idx(wf, _ancestor)
    assert _idx(wf, _ancestor) < _idx(wf, _ayasee("plan"))
    assert _idx(wf, _ancestor) < _idx(wf, lambda s: "azure/login@" in _uses(s))


def test_approvers_precede_the_plan_and_apply(wf):
    steps = _steps(wf, JOB)
    i = _idx(wf, lambda s: "ayasee approvals list" in _run(s))
    assert "--env prod-breakglass" in _run(steps[i])
    assert steps[i]["id"] == "approver"
    assert i < _idx(wf, _ayasee("plan")) < _idx(wf, _ayasee("apply"))
    assert "if" not in steps[i]  # fail closed: no condition can skip it


def test_plan_and_apply_use_breakglass_mode_with_lock_timeout(wf):
    steps = _steps(wf, JOB)
    plan = steps[_idx(wf, _ayasee("plan"))]
    apply = steps[_idx(wf, _ayasee("apply"))]
    for s in (plan, apply):
        w = s["with"]
        assert w["env"] == "prod" and w["binding-mode"] == "breakglass"
        assert w["breakglass-incident"] == "${{ needs.validate.outputs.incident }}"
        assert w["lock-timeout"] == "10m"
        assert w["encrypt"] == "true"
        assert "only-units" not in w  # R71
    assert "plan-artifact" not in apply["with"]


def test_ledger_records_incident_approvers_and_mirror(wf):
    steps = _steps(wf, JOB)
    ledger = steps[_idx(wf, _ayasee("ledger"))]
    w = ledger["with"]
    assert w["env"] == "prod"
    assert w["breakglass-incident"] == "${{ needs.validate.outputs.incident }}"
    assert w["approver"] == "${{ steps.approver.outputs.login }}"
    assert w["mirror-deployments"] == "true"
    assert w["sha"] == "${{ needs.validate.outputs.sha }}"
    assert _idx(wf, _ayasee("apply")) < _idx(wf, _ayasee("ledger"))


def test_ledger_credentials_are_step_scoped(wf):
    job = wf["jobs"][JOB]
    assert "AYASEE_LEDGER_TOKEN" not in yaml.safe_dump(job["env"])
    holders = [s for s in job["steps"] if "AYASEE_LEDGER_TOKEN" in s.get("env", {})]
    assert [h.get("id") or "ledger" for h in holders] == ["live", "ledger"]
    assert "/actions/ledger@" in _uses(holders[1])


def test_plan_runs_at_the_checked_out_sha(wf):
    plan = _steps(wf, JOB)[_idx(wf, _ayasee("plan"))]
    assert plan["with"]["expect-sha"] == "true"
    assert plan["env"]["SG_SHA"] == "${{ needs.validate.outputs.sha }}"


def test_notify_breakglass_used(wf):
    notify = [s for s in _steps(wf, JOB) if _ayasee("notify")(s)]
    used = next(s for s in notify if s["with"]["event"] == "breakglass_used")
    assert "always()" in used["if"]
    assert used["with"]["env"] == "prod"
    assert used["with"]["approver"] == "${{ steps.approver.outputs.login }}"


def test_every_uses_is_sha_or_owner_pinned(wf):
    seen = set()
    for job in wf["jobs"].values():
        for s in job["steps"]:
            uses = _uses(s)
            if not uses:
                continue
            seen.add(uses.split("@")[0])
            if "/ayasee/actions/" in uses:
                assert AYASEE_USES.match(uses) and uses.split("@")[1] == "0" * 40, uses
            else:
                assert SHA_REF.search(uses), uses
    assert seen == {
        "actions/checkout",
        "azure/login",
        *(
            f"OWNER/ayasee/actions/{a}"
            for a in ("setup", "plan", "policy", "apply", "ledger", "notify")
        ),
    }
    for line in TEXT.splitlines():
        if "/ayasee/actions/" in line:
            assert line.rstrip().endswith("# AYASEE_REF"), line


def test_no_uv_cache_and_no_input_interpolation_in_run(wf):
    assert "enable-cache: true" not in TEXT
    assert "setup-uv" not in TEXT and "cache: true" not in TEXT
    assert "UV_CACHE" not in TEXT
    for job in wf["jobs"].values():
        for s in job["steps"]:
            assert "${{" not in _run(s), _run(s)


def test_no_cancel_in_progress(wf):
    assert "cancel-in-progress: true" not in TEXT


def test_set_ayasee_ref_script_rewrites_the_workflow(tmp_path):
    import shutil

    from conftest import ROOT

    script = ROOT / "scripts" / "set-ayasee-ref.sh"
    rel = tmp_path / "rel.yml"
    shutil.copy(PATH, rel)
    head = subprocess.run(
        ["git", "-C", ROOT, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run([script, "acme/ayasee", head, rel], check=True)
    text = rel.read_text()
    assert f"uses: acme/ayasee/actions/plan@{head} # AYASEE_REF" in text
    assert "OWNER/ayasee" not in text


def test_no_unit_scope_anywhere(wf):
    """R71: nothing in break-glass narrows the plan or apply to some units."""
    text = PATH.read_text()
    assert "only-units" not in text and "inputs.units" not in text


def test_refuses_unsupported_settings_before_login(wf):
    steps = _steps(wf, JOB)
    i = _idx(wf, lambda s: "config check-supported" in s.get("run", ""))
    assert steps[i]["env"]["CONFIG_PATH"] == "${{ inputs.config-path }}"
    assert "if" not in steps[i]
    login = _idx(wf, lambda s: "azure/login" in s.get("uses", ""))
    assert _idx(wf, _ayasee("setup")) < i < login


def test_breakglass_has_no_main_tip_check(wf):
    """R70 excludes break-glass: it deploys an explicit, older-or-equal sha on main."""
    assert "main-tip" not in PATH.read_text()
