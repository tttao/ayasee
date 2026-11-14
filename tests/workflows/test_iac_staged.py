"""Static checks on the iac-staged reusable workflow."""

import re

import pytest
import yaml
from conftest import AYASEE_USES, ROOT, SHA_REF, WORKFLOWS
from conftest import index as _index
from conftest import steps as _steps

from ayasee.config import PRE_PROD_SLOTS

PATH = WORKFLOWS / "iac-staged.yml"
# the pre-prod job slots, in order; they run the stages named in `stages` (issues #1, #10)
SLOTS = [f"stage-{i}" for i in range(1, PRE_PROD_SLOTS + 1)]
STAGE_JOBS = [*SLOTS, "prod-plan", "prod", "prod-in-sync"]
# prod-plan and prod-in-sync never wait for prod reviewers (R31)
EXPECTED_ENV = {
    **{slot: f"${{{{ needs.resolve.outputs.{slot} }}}}" for slot in SLOTS},
    "prod-plan": "prod-plan",
    "prod": "prod",
    "prod-in-sync": "prod-plan",
}


@pytest.fixture(scope="module")
def wf() -> dict:
    return yaml.safe_load(PATH.read_text())


def test_no_cancel_in_progress_on_any_job(wf):
    assert wf["jobs"]
    for name, job in wf["jobs"].items():
        conc = job.get("concurrency")
        if conc is not None:
            assert conc["cancel-in-progress"] is False, name
    assert "cancel-in-progress: true" not in PATH.read_text()


def test_prod_needs_prod_plan_and_is_conditional(wf):
    prod = wf["jobs"]["prod"]
    assert "prod-plan" in prod["needs"]
    assert "needs.prod-plan.outputs.has-changes == 'true'" in prod["if"]


def test_every_job_has_environment_and_main_ref_guard(wf):
    for name in STAGE_JOBS:
        job = wf["jobs"][name]
        assert job["environment"] == EXPECTED_ENV[name], name
        assert "github.ref == 'refs/heads/main'" in job["if"], name
        assert job["concurrency"]["group"].startswith("ayasee-${{ github.repository }}-")
        if name == "prod-plan":
            # M3: prod-plan writes no ledger, deployment or issue; the guard only reads issues
            assert job["permissions"] == {
                "id-token": "write",
                "contents": "read",
                "issues": "read",
            }, name
            continue
        assert job["permissions"] == {
            "id-token": "write",
            "contents": "read",
            "deployments": "write",
            "issues": "write",
            "actions": "read",
        }, name
    assert "github.ref == 'refs/heads/main'" in wf["jobs"]["resolve"]["if"]


def test_concurrency_groups(wf):
    def group(job):
        return wf["jobs"][job]["concurrency"]["group"]

    assert group("prod-plan") == "ayasee-${{ github.repository }}-prod-plan"
    # prod and the in-sync ledger write both touch the prod record: one queue
    assert group("prod") == group("prod-in-sync") == "ayasee-${{ github.repository }}-prod"


def test_no_secrets_inherit(wf):
    assert "inherit" not in PATH.read_text()
    secrets = wf[True]["workflow_call"]["secrets"]
    assert set(secrets) == {
        "teams-webhook",
        "ledger-token",
        "extra-env-secret",
        "AYASEE_PLAN_PASSPHRASE",
        "AYASEE_KMS_ACCESS_KEY_ID",
        "AYASEE_KMS_SECRET_ACCESS_KEY",
        "AYASEE_GCP_KMS_CREDENTIALS",
        "AYASEE_OPENBAO_TOKEN",
        "CLOUDFLARE_API_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AYASEE_PROVIDER_ENV",
        "AYASEE_LEDGER_AWS_ACCESS_KEY_ID",
        "AYASEE_LEDGER_AWS_SECRET_ACCESS_KEY",
    }
    assert all(v["required"] is False for v in secrets.values())


def test_empty_plan_path_writes_ledger(wf):
    job = wf["jobs"]["prod-in-sync"]
    assert "prod-plan" in job["needs"]
    assert "needs.prod-plan.outputs.has-changes == 'false'" in job["if"]
    text = "\n".join(str(s) for s in job["steps"])
    assert "ledger" in text and "no_changes" in text
    assert "needs.prod-plan.outputs.units" in text


def test_every_uses_is_sha_pinned_or_local(wf):
    refs = set()
    for name, job in wf["jobs"].items():
        for step in job["steps"]:
            uses = step.get("uses")
            if uses is None or uses.startswith("./"):
                continue
            assert SHA_REF.search(uses), f"{name}: {uses}"
            m = AYASEE_USES.match(uses)
            if m:
                refs.add(m.group(1))
    # all ayasee actions come from one ref, replaced at release
    assert len(refs) == 1
    assert "AYASEE_REF" in PATH.read_text()


def test_third_party_uses_have_version_comment():
    for line in PATH.read_text().splitlines():
        if "uses:" in line and "/ayasee/actions/" not in line:
            assert re.search(r"@[0-9a-f]{40} # v\d", line), line


def test_no_expression_in_run_blocks(wf):
    for name, job in wf["jobs"].items():
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), f"{name}: {step.get('name')}"


def test_prod_guard_runs_before_download_and_apply(wf):
    steps = _steps(wf, "prod")
    guard = _index(steps, lambda s: s.get("uses", "").find("/actions/guard@") > 0)
    download = _index(steps, lambda s: "download-artifact" in s.get("uses", ""))
    apply = _index(steps, lambda s: "/actions/apply@" in s.get("uses", ""))
    login = _index(steps, lambda s: "azure/login" in s.get("uses", ""))
    assert login < guard < download < apply
    assert steps[apply]["with"]["binding-mode"] == "encrypted_plan"


def test_azure_login_precedes_plan_in_every_stage_job(wf):
    for name in (*SLOTS, "prod-plan", "prod"):
        steps = _steps(wf, name)
        login = _index(steps, lambda s: "azure/login" in s.get("uses", ""))
        plan = _index(steps, lambda s: re.search(r"/actions/(plan|apply)@", s.get("uses", "")))
        assert login < plan, name


def test_prod_plan_upload_has_no_plan_json_and_short_retention(wf):
    steps = _steps(wf, "prod-plan")
    up = steps[_index(steps, lambda s: "upload-artifact" in s.get("uses", ""))]
    assert up["with"]["name"] == "ayasee-prod-plan"
    assert up["with"]["retention-days"] == 7
    assert up["with"]["if-no-files-found"] == "error"
    assert "json" not in up["with"]["path"]
    assert "outputs.plan-dir" in up["with"]["path"]
    assert "outputs.manifest-path" in up["with"]["path"]


def test_prod_plan_scans_before_upload(wf):
    steps = _steps(wf, "prod-plan")
    policy = _index(steps, lambda s: "/actions/policy@" in s.get("uses", ""))
    trivy = _index(steps, lambda s: "trivy-action" in s.get("uses", ""))
    upload = _index(steps, lambda s: "upload-artifact" in s.get("uses", ""))
    assert policy < upload and trivy < upload
    with_ = steps[trivy]["with"]
    assert with_["scan-type"] == "config"
    assert with_["severity"] == "HIGH,CRITICAL"
    assert str(with_["exit-code"]) == "1"
    assert steps[policy]["with"]["stage"] == "prod"


def test_prod_plan_outputs(wf):
    assert set(wf["jobs"]["prod-plan"]["outputs"]) == {"has-changes", "critical", "high", "units"}


def test_start_stage_validated_in_resolve(wf):
    step = next(s for s in wf["jobs"]["resolve"]["steps"] if s.get("id") == "resolve")
    run = step["run"]
    assert step["env"]["START_STAGE"] == "${{ inputs.start-stage }}"
    assert 'ayasee config staged --path "$CONFIG_PATH" --start-stage "$START_STAGE"' in run
    assert "::error::$line" in run and "exit 1" in run
    assert "if" not in step and "continue-on-error" not in step
    # empty means the first configured stage
    assert wf[True]["workflow_call"]["inputs"]["start-stage"]["default"] == ""


def test_pre_prod_slots_use_same_job_and_write_ledger_with_mirror(wf):
    for name in SLOTS:
        steps = _steps(wf, name)
        plan = steps[_index(steps, lambda s: "/actions/plan@" in s.get("uses", ""))]
        apply = steps[_index(steps, lambda s: "/actions/apply@" in s.get("uses", ""))]
        ledger = steps[_index(steps, lambda s: "/actions/ledger@" in s.get("uses", ""))]
        assert plan["with"]["binding-mode"] == apply["with"]["binding-mode"] == "same-job"
        assert ledger["with"]["mirror-deployments"] == "true"
        assert "steps.units.outputs.units-json" in ledger["if"]


def test_oidc_environment(wf):
    """ARM_* and the Databricks auth type are set for identity: oidc only (issue #1)."""
    oidc = "needs.resolve.outputs.identity == 'oidc'"
    for name in (*SLOTS, "prod-plan", "prod", "prod-in-sync"):
        env = wf["jobs"][name]["env"]
        assert env["ARM_USE_OIDC"] == f"${{{{ {oidc} && 'true' || '' }}}}"
        assert env["DATABRICKS_AUTH_TYPE"] == f"${{{{ {oidc} && 'github-oidc-azure' || '' }}}}"
        for var in ("CLIENT_ID", "TENANT_ID", "SUBSCRIPTION_ID"):
            assert env[f"ARM_{var}"] == f"${{{{ {oidc} && vars.AZURE_{var} || '' }}}}"
        assert "resolve" in wf["jobs"][name]["needs"]


def test_azure_login_only_for_the_oidc_identity(wf):
    for name in (*SLOTS, "prod-plan", "prod", "prod-in-sync"):
        logins = [s for s in _steps(wf, name) if "azure/login" in s.get("uses", "")]
        assert len(logins) == 1, name
        assert logins[0]["if"] == "${{ needs.resolve.outputs.identity == 'oidc' }}", name
    out = wf["jobs"]["resolve"]["outputs"]
    assert out["identity"] == "${{ steps.resolve.outputs.identity }}"


def test_pre_prod_slots_run_the_configured_stage(wf):
    for job in SLOTS:
        j = wf["jobs"][job]
        expr = f"${{{{ needs.resolve.outputs.{job} }}}}"
        assert j["env"]["STAGE"] == expr
        assert j["name"] == f"${{{{ needs.resolve.outputs.{job} || '{job}' }}}}"
        # an unused (skipped) slot gets a group of its own run, never the shared empty-stage one
        assert j["concurrency"]["group"] == (
            f"ayasee-${{{{ github.repository }}}}-${{{{ needs.resolve.outputs.{job} || "
            f"format('unused-{{0}}-{job}', github.run_id) }}}}"
        )
        steps = _steps(wf, job)
        for kind in ("plan", "apply", "ledger", "notify"):
            step = next(s for s in steps if f"/actions/{kind}@" in s.get("uses", ""))
            assert step["with"]["env"] == "${{ env.STAGE }}", (job, kind)
        guard = next(s for s in steps if "/actions/guard@" in s.get("uses", ""))
        assert guard["with"]["stage"] == "${{ env.STAGE }}"
        assert guard["with"]["order"] == "${{ needs.resolve.outputs.order }}"
        text = PATH.read_text()
        assert f"stage: {job}\n" not in text and f"env: {job}\n" not in text
    out = wf["jobs"]["resolve"]["outputs"]
    for job in SLOTS:
        assert out[job] == f"${{{{ steps.resolve.outputs.{job} }}}}"
        assert out[f"run-{job}"] == f"${{{{ steps.resolve.outputs.run-{job} }}}}"
    # every slot and its run flag the CLI prints is wired; no other slot exists
    jobs = [j for j in wf["jobs"] if re.fullmatch(r"stage-\d+", j)]
    assert jobs == SLOTS


def test_pre_prod_slots_are_identical_but_for_their_name(wf):
    """Every slot is a copy of stage-1 except `needs` and `if` (#10), so the per-step checks on
    one slot hold for all, and a new slot only has to be a copy to be covered."""

    def shape(job):
        j = {k: v for k, v in wf["jobs"][job].items() if k not in ("needs", "if")}
        return yaml.safe_dump(j, sort_keys=True).replace(job, "<slot>")

    assert "<slot>" in shape(SLOTS[0])
    for job in SLOTS[1:]:
        assert shape(job) == shape(SLOTS[0]), job


def test_pre_prod_slots_and_prod_plan_form_one_chain(wf):
    """Slot i needs slot i-1 and runs only after it succeeded or was skipped on purpose (#10).

    Together with the right-aligned layout (tests/test_config.py), this is what keeps the order:
    no slot starts before the one before it has finished, and a failure anywhere stops the rest.
    """
    jobs = wf["jobs"]
    assert jobs[SLOTS[0]]["needs"] == "resolve"
    assert "'skipped'" not in jobs[SLOTS[0]]["if"]
    for prev, job in zip(SLOTS, SLOTS[1:], strict=False):
        assert jobs[job]["needs"] == ["resolve", prev], job
        cond = jobs[job]["if"]
        assert cond.startswith("${{ !cancelled() && github.ref == 'refs/heads/main' && ")
        assert f"needs.resolve.outputs.run-{job} == 'true'" in cond
        assert (
            f"(needs.{prev}.result == 'success' || (needs.resolve.outputs.run-{prev} == 'false'"
            f" && needs.{prev}.result == 'skipped'))" in cond
        ), job
    assert jobs["prod-plan"]["needs"] == ["resolve", *SLOTS]
    for slot in SLOTS:
        assert (
            f"(needs.{slot}.result == 'success' || (needs.resolve.outputs.run-{slot} == 'false'"
            f" && needs.{slot}.result == 'skipped'))" in jobs["prod-plan"]["if"]
        ), slot


def test_prod_in_sync_mirrors_and_notifies_failure(wf):
    steps = _steps(wf, "prod-in-sync")
    ledger = steps[_index(steps, lambda s: "/actions/ledger@" in s.get("uses", ""))]
    assert ledger["with"]["mirror-deployments"] == "true"
    notify = steps[_index(steps, lambda s: "/actions/notify@" in s.get("uses", ""))]
    assert notify["with"]["event"] == "prod_failed"
    assert "failure()" in notify["if"]


def test_skipped_needs_only_count_when_the_skip_was_intended(wf):
    pattern = re.compile(r"needs\.(\w+)\.result == 'skipped'")
    for name in (*SLOTS[1:], "prod-plan"):
        cond = wf["jobs"][name]["if"]
        for m in pattern.finditer(cond):
            prev = m.group(1)
            assert f"needs.resolve.outputs.run-{prev} == 'false' && {m.group(0)}" in cond
            assert f"needs.{prev}.result == 'success'" in cond
    for slot in SLOTS[1:]:
        assert wf["jobs"][slot]["if"].count("'skipped'") == 1, slot
    assert wf["jobs"]["prod-plan"]["if"].count("'skipped'") == len(SLOTS)
    assert wf["jobs"]["prod-plan"]["needs"] == ["resolve", *SLOTS]
    assert "'skipped'" not in wf["jobs"][SLOTS[0]]["if"]


def test_prod_reads_the_approver_before_download_and_apply(wf):
    steps = _steps(wf, "prod")
    guard = _index(steps, lambda s: "/actions/guard@" in s.get("uses", ""))
    approver = _index(steps, lambda s: s.get("id") == "approver")
    download = _index(steps, lambda s: "download-artifact" in s.get("uses", ""))
    apply = _index(steps, lambda s: "/actions/apply@" in s.get("uses", ""))
    ledger = _index(steps, lambda s: "/actions/ledger@" in s.get("uses", ""))
    assert guard < approver < download < apply < ledger
    assert "if" not in steps[approver]  # default success(): no approval, no apply
    assert "approvals list" in steps[approver]["run"]
    assert steps[ledger]["with"]["approver"] == "${{ steps.approver.outputs.login }}"


def test_binding_failure_does_not_open_stop_the_line(wf):
    for name in (*SLOTS, "prod"):
        steps = _steps(wf, name)
        issue = steps[_index(steps, lambda s: "stop-the-line" in s.get("run", ""))]
        assert "steps.apply.outcome == 'failure'" in issue["if"]
        assert "steps.units.outputs.units-json != ''" in issue["if"]
    steps = _steps(wf, "prod")
    notify = [s for s in steps if s.get("with", {}).get("event") == "prod_failed"]
    assert notify, "prod_failed is still notified"


def test_trivy_does_not_use_the_actions_cache(wf):
    steps = _steps(wf, "prod-plan")
    trivy = steps[_index(steps, lambda s: "trivy-action" in s.get("uses", ""))]
    assert trivy["with"]["cache"] == "false"


def test_policy_and_trivy_read_the_plan_json_dir_output(wf):
    steps = _steps(wf, "prod-plan")
    policy = steps[_index(steps, lambda s: "/actions/policy@" in s.get("uses", ""))]
    trivy = steps[_index(steps, lambda s: "trivy-action" in s.get("uses", ""))]
    assert policy["with"]["plan-json"] == "${{ steps.plan.outputs.json-dir }}"
    assert trivy["with"]["scan-ref"] == "${{ steps.plan.outputs.json-dir }}"


def test_resolve_rejects_unsupported_settings(wf):
    """R35 + R73: gate: risky, engine: terraform, terragrunt: false, extra_rules, notify.teams."""
    steps = _steps(wf, "resolve")
    i = _index(steps, lambda s: "config check-supported" in s.get("run", ""))
    step = steps[i]
    assert step["env"]["CONFIG_PATH"] == "${{ inputs.config-path }}"
    assert "if" not in step and "continue-on-error" not in step
    assert "::error::$line" in step["run"] and "exit 1" in step["run"]
    assert i < _index(steps, lambda s: s.get("id") == "resolve")


def test_prod_plan_writes_nothing_to_github(wf):
    """M3: no ledger write, no deployment mirror and no issue in prod-plan."""
    for step in _steps(wf, "prod-plan"):
        assert "/actions/ledger@" not in step.get("uses", "")
        assert "ayasee issue" not in step.get("run", "")


def test_resolve_refuses_a_superseded_commit(wf):
    """R70: the run of a commit that is no longer the tip of main stops in resolve."""
    steps = _steps(wf, "resolve")
    i = _index(steps, lambda s: "main-tip" in s.get("run", ""))
    step = steps[i]
    assert step["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    assert step["env"]["REPO"] == "${{ github.repository }}"
    assert step["env"]["SHA"] == "${{ github.sha }}"
    assert step["env"]["CONFIG_PATH"] == "${{ inputs.config-path }}"
    run = step["run"]
    assert 'ayasee main-tip --repo "$REPO" --check "$SHA" --config-path "$CONFIG_PATH"' in run
    assert "::error::" in run and "exit 1" in run
    assert "if" not in step and "continue-on-error" not in step
    setup = _index(steps, lambda s: "/actions/setup@" in s.get("uses", ""))
    assert setup < i < _index(steps, lambda s: s.get("id") == "resolve")
    assert wf["jobs"]["resolve"]["permissions"] == {"contents": "read"}


def test_ledger_credentials_are_step_scoped_env_only(wf):
    text = PATH.read_text()
    assert "credential.helper" not in text and "git config" not in text
    assert "ledger-git-credentials" not in text
    for name in (*SLOTS, "prod-plan", "prod", "prod-in-sync"):
        for step in _steps(wf, name):
            uses = step.get("uses", "")
            env = step.get("env", {})
            if "/actions/guard@" in uses or "/actions/ledger@" in uses:
                assert env["GIT_CONFIG_COUNT"] == "${{ secrets.ledger-token && '1' || '0' }}", name
                assert env["GIT_CONFIG_KEY_0"] == "credential.${{ github.server_url }}.helper"
                assert "$AYASEE_LEDGER_TOKEN" in env["GIT_CONFIG_VALUE_0"]
            else:
                assert "GIT_CONFIG_COUNT" not in env and "AYASEE_LEDGER_TOKEN" not in env
        assert "ledger-token" not in str(wf["jobs"][name].get("env", {}))


def test_prod_plan_summary_assigns_before_echoing(wf):
    steps = _steps(wf, "prod-plan")
    run = steps[_index(steps, lambda s: s.get("id") == "summary")]["run"]
    for line in run.splitlines():
        if line.strip().startswith("echo"):
            assert "$(" not in line, line  # a failing jq inside echo would be swallowed
    for var in ("critical", "high", "units"):
        assert f'{var}="$(jq -e' in run


def test_prod_in_sync_checks_out_the_repository(wf):
    steps = _steps(wf, "prod-in-sync")
    co = steps[_index(steps, lambda s: "actions/checkout" in s.get("uses", ""))]
    assert co["with"]["persist-credentials"] is False


def test_prod_failed_notification_carries_replan_detail(wf):
    steps = _steps(wf, "prod")
    n = [s for s in steps if s.get("with", {}).get("event") == "prod_failed"][0]
    assert "replan_needed" in n["with"]["detail"]
    assert "steps.units.outputs.units-json == ''" in n["with"]["detail"]


def test_set_ayasee_ref_script(tmp_path):
    import shutil
    import subprocess

    script = ROOT / "scripts" / "set-ayasee-ref.sh"
    local = tmp_path / "local.yml"
    shutil.copy(PATH, local)
    subprocess.run([script, "--local", local], check=True)
    text = local.read_text()
    assert "uses: ./actions/plan # AYASEE_REF" in text
    assert "OWNER/ayasee" not in text

    head = subprocess.run(
        ["git", "-C", ROOT, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    rel = tmp_path / "rel.yml"
    shutil.copy(PATH, rel)
    subprocess.run([script, "acme/ayasee", head, rel], check=True)
    assert f"uses: acme/ayasee/actions/plan@{head} # AYASEE_REF" in rel.read_text()

    bad = subprocess.run([script, "acme/ayasee", "z" * 40, rel], capture_output=True)
    assert bad.returncode != 0


def test_set_ayasee_ref_refuses_when_actions_differ_from_source(tmp_path):
    import shutil
    import subprocess

    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "actions").mkdir()
    (repo / ".github" / "workflows").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "set-ayasee-ref.sh", repo / "scripts")
    shutil.copy(PATH, repo / ".github" / "workflows" / "iac-staged.yml")
    (repo / "actions" / "a.txt").write_text("one")

    def git(*a):
        return subprocess.run(
            ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", *a],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "one")
    source = git("rev-parse", "HEAD")
    (repo / "actions" / "a.txt").write_text("two")
    git("commit", "-qam", "two")
    res = subprocess.run(
        [repo / "scripts" / "set-ayasee-ref.sh", "acme/ayasee", source],
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0 and "differ" in res.stderr
    assert "OWNER/ayasee" in (repo / ".github" / "workflows" / "iac-staged.yml").read_text()


def test_prod_records_the_ledger_even_when_cancelled(wf):
    steps = _steps(wf, "prod")
    assert steps[_index(steps, lambda s: s.get("id") == "units")]["if"] == "${{ always() }}"
    ledger = steps[_index(steps, lambda s: "/actions/ledger@" in s.get("uses", ""))]
    assert ledger["if"] == "${{ always() && steps.units.outputs.units-json != '' }}"


# ---- R50: a rejected prod gate is announced ----


def test_prod_result_runs_only_after_a_failed_prod_job_with_changes(wf):
    job = wf["jobs"]["prod-result"]
    assert job["needs"] == ["resolve", "prod-plan", "prod"]
    cond = job["if"]
    assert cond.startswith("${{ always()")
    assert "needs.prod-plan.outputs.has-changes == 'true'" in cond
    assert "needs.prod.result == 'failure'" in cond
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    assert "environment" not in job


def test_prod_result_notifies_prod_rejected_only_when_the_gate_was_rejected(wf):
    s = _steps(wf, "prod-result")
    check = next(x for x in s if x.get("id") == "rejected")
    assert "ayasee approvals rejected" in check["run"]
    assert "--env prod --job prod" in check["run"]
    notify = next(x for x in s if x.get("uses", "").startswith("OWNER/ayasee/actions/notify@"))
    assert notify["with"]["event"] == "prod_rejected"
    assert notify["if"] == "${{ steps.rejected.outputs.rejected == 'true' }}"
    assert [x for x in s if "notify@" in x.get("uses", "")] == [notify]


# ---- R52 / R51 plumbing ----


def test_prod_plan_passes_allow_cascade_from_the_configuration(wf):
    plan = next(x for x in _steps(wf, "prod-plan") if x.get("id") == "plan")
    assert plan["with"]["allow-cascade"] == "${{ needs.resolve.outputs.allow-cascade }}"
    assert "allow_cascade" in PATH.read_text()
    for stage in SLOTS:
        step = next(x for x in _steps(wf, stage) if x.get("id") == "plan")
        assert "allow-cascade" not in step["with"]


def test_extra_secret_reaches_only_plan_and_apply_steps(wf):
    text = PATH.read_text()
    assert text.count("secrets.extra-env-secret") == 2 * len(SLOTS) + 2
    for name, job in wf["jobs"].items():
        for step in job["steps"]:
            has = "AYASEE_EXTRA_SECRET" in (step.get("env") or {})
            uses = step.get("uses", "")
            assert has == bool(re.search(r"/actions/(plan|apply)@", uses)), (name, step)


def test_features_step_never_blocks_the_run(wf):
    step = next(s for s in wf["jobs"]["resolve"]["steps"] if s.get("id") == "features")
    assert step["continue-on-error"] is True
    assert "::warning::" in step["run"] and 'echo "features={}"' in step["run"]


def _run_step(tmp_path, step, ayasee_body, **env):
    import os
    import subprocess

    shim = tmp_path / "shim"
    shim.mkdir(exist_ok=True)
    fake = shim / "ayasee"
    fake.write_text("#!/bin/bash\n" + ayasee_body)
    fake.chmod(0o755)
    return subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{shim}:{os.environ['PATH']}", **env},
    )


@pytest.mark.parametrize(
    ("body", "rc", "out"),
    [
        ("exit 0\n", 0, None),
        ('echo "a is not the tip, but no later commit changes the IaC"\n', 0, "::notice::a is"),
        (
            'echo "main-tip: superseded: later commits change the IaC" >&2; exit 1\n',
            1,
            "::error::superseded: later commits",
        ),
        (
            'echo "NON-COMPLIANT: x" >&2; echo "main-tip: error: bad" >&2; exit 1\n',
            1,
            "::error::error: bad",
        ),
    ],
    ids=["tip", "unaffected", "superseded", "error"],
)
def test_superseded_step_behaviour(wf, tmp_path, body, rc, out):
    steps = _steps(wf, "resolve")
    step = steps[_index(steps, lambda s: "main-tip" in s.get("run", ""))]
    proc = _run_step(
        tmp_path,
        step,
        body,
        REPO="o/r",
        SHA="a" * 40,
        CONFIG_PATH=".ayasee.yaml",
        RUNNER_TEMP=str(tmp_path),
    )
    assert proc.returncode == rc
    if out is None:
        assert "::" not in proc.stdout
    else:
        assert out in proc.stdout
    assert "::error::NON-COMPLIANT" not in proc.stdout


def test_check_supported_step_prints_one_error_per_line(wf, tmp_path):
    steps = _steps(wf, "resolve")
    step = steps[_index(steps, lambda s: "config check-supported" in s.get("run", ""))]
    body = (
        'echo "engine: not supported yet" >&2; echo "extra_rules: not supported yet" >&2; exit 1\n'
    )
    proc = _run_step(tmp_path, step, body, CONFIG_PATH=".ayasee.yaml")
    assert proc.returncode == 1
    assert proc.stdout.splitlines() == [
        "::error::engine: not supported yet",
        "::error::extra_rules: not supported yet",
    ]
    ok = _run_step(tmp_path, step, "exit 0\n", CONFIG_PATH=".ayasee.yaml")
    assert ok.returncode == 0 and ok.stdout == ""


# ---- issue #1: configurable stages and identity profile, with the real CLI ----

_REAL_AYASEE = (
    f'PYTHONPATH="{ROOT / "src"}" exec "{__import__("sys").executable}" '
    '-c "from ayasee.cli import entrypoint; entrypoint()" "$@"\n'
)


def _resolve(wf, tmp_path, config_text, start=""):
    step = next(s for s in wf["jobs"]["resolve"]["steps"] if s.get("id") == "resolve")
    cfg = tmp_path / "ayasee.yaml"
    cfg.write_text(config_text)
    out = tmp_path / "out.txt"
    out.write_text("")
    proc = _run_step(
        tmp_path,
        step,
        _REAL_AYASEE,
        CONFIG_PATH=str(cfg),
        START_STAGE=start,
        RUNNER_TEMP=str(tmp_path),
        GITHUB_OUTPUT=str(out),
    )
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines())
    return proc, outputs


def test_resolve_step_defaults_keep_dev_uat_prod(wf, tmp_path):
    proc, out = _resolve(wf, tmp_path, "")
    assert proc.returncode == 0, proc.stderr
    assert [out[f"run-{s}"] for s in SLOTS] == ["false", "false", "true", "true"]
    assert [out[s] for s in SLOTS] == ["", "", "dev", "uat"]
    assert out["run-prod-plan"] == "true"
    assert out["order"] == "dev,uat,prod" and out["identity"] == "oidc"


def test_resolve_step_two_stages_and_secrets_identity(wf, tmp_path):
    proc, out = _resolve(wf, tmp_path, "stages: [staging, prod]\nidentity: secrets\n")
    assert proc.returncode == 0, proc.stderr
    assert [out[f"run-{s}"] for s in SLOTS] == ["false", "false", "false", "true"]
    assert [out[s] for s in SLOTS] == ["", "", "", "staging"]
    assert out["order"] == "staging,prod" and out["identity"] == "secrets"


def test_resolve_step_five_stages_from_a_middle_stage(wf, tmp_path):
    """Issue #10: four pre-prod stages fill every slot; start-stage skips the ones before it."""
    proc, out = _resolve(wf, tmp_path, "stages: [dev, sit, uat, preprod, prod]\n", "uat")
    assert proc.returncode == 0, proc.stderr
    assert [out[s] for s in SLOTS] == ["dev", "sit", "uat", "preprod"]
    assert [out[f"run-{s}"] for s in SLOTS] == ["false", "false", "true", "true"]
    assert out["run-prod-plan"] == "true" and out["order"] == "dev,sit,uat,preprod,prod"


@pytest.mark.parametrize(
    ("text", "start"),
    [("stages: [a, b, c, d, e, prod]\n", ""), ("stages: [prod-plan, prod]\n", ""), ("", "qa")],
)
def test_resolve_step_refuses_an_unsupported_layout(wf, tmp_path, text, start):
    proc, out = _resolve(wf, tmp_path, text, start)
    assert proc.returncode == 1
    assert proc.stdout.startswith("::error::ayasee: config error:")
    assert out == {}


# ---- issue #1: plan_encryption.key_provider ----


def test_key_provider_reaches_the_prod_plan_and_apply_only(wf):
    assert wf["jobs"]["resolve"]["outputs"]["key-provider"] == (
        "${{ steps.resolve.outputs.key-provider }}"
    )
    run = next(s for s in _steps(wf, "resolve") if s.get("id") == "resolve")["run"]
    assert "'.plan_encryption.key_provider'" in run
    for job, kind in (("prod-plan", "plan"), ("prod", "apply")):
        step = next(s for s in _steps(wf, job) if f"/actions/{kind}@" in s.get("uses", ""))
        assert step["with"]["key-provider"] == "${{ needs.resolve.outputs.key-provider }}"
        assert step["with"]["encrypt"] == "true"
        # issue #10: the aws_kms key, next to the Key Vault key; the action refuses both set
        assert step["with"]["kms-key-id"] == "${{ vars.AYASEE_KMS_KEY_ID }}"
        assert step["with"]["gcp-kms-key-name"] == "${{ vars.AYASEE_GCP_KMS_KEY_NAME }}"
        assert step["with"]["openbao-key-url"] == "${{ vars.AYASEE_OPENBAO_KEY_URL }}"


def test_the_plan_passphrase_reaches_only_the_prod_plan_and_apply_steps(wf):
    seen = []
    for name, job in wf["jobs"].items():
        assert "AYASEE_PLAN_PASSPHRASE" not in str(job.get("env", {})), name
        for step in job["steps"]:
            if "AYASEE_PLAN_PASSPHRASE" in str(step):
                assert step["env"]["AYASEE_PLAN_PASSPHRASE"] == (
                    "${{ secrets.AYASEE_PLAN_PASSPHRASE }}"
                )
                seen.append((name, step["uses"].split("/actions/")[1].split("@")[0]))
    assert seen == [("prod-plan", "plan"), ("prod", "apply")]
    secret = wf[True]["workflow_call"]["secrets"]["AYASEE_PLAN_PASSPHRASE"]
    assert secret["required"] is False and "environment secret" in secret["description"]


@pytest.mark.parametrize(
    ("secret", "provider"),
    [
        ("AYASEE_KMS_ACCESS_KEY_ID", "aws_kms"),
        ("AYASEE_KMS_SECRET_ACCESS_KEY", "aws_kms"),
        ("AYASEE_GCP_KMS_CREDENTIALS", "gcp_kms"),
        ("AYASEE_OPENBAO_TOKEN", "openbao"),
    ],
)
def test_the_kms_credentials_reach_only_the_prod_plan_and_apply_steps(wf, secret, provider):
    """issue #10: each key-service credential is prod-only, like the pbkdf2 passphrase."""
    seen = []
    for name, job in wf["jobs"].items():
        assert secret not in str(job.get("env", {})), name
        for step in job["steps"]:
            if secret in str(step):
                assert step["env"][secret] == f"${{{{ secrets.{secret} }}}}"
                seen.append((name, step["uses"].split("/actions/")[1].split("@")[0]))
    assert seen == [("prod-plan", "plan"), ("prod", "apply")]
    declared = wf[True]["workflow_call"]["secrets"][secret]
    assert declared["required"] is False
    if secret != "AYASEE_KMS_SECRET_ACCESS_KEY":  # its description points at the access key id
        text = " ".join(declared["description"].split())
        assert "environment secret of prod-plan, prod and prod-breakglass only" in text
        assert provider in text


def test_prod_plan_flags_shared_units_and_resolve_checks_their_layout():
    wf = yaml.safe_load((WORKFLOWS / "iac-staged.yml").read_text())
    resolve = wf["jobs"]["resolve"]
    assert "shared-units" in resolve["outputs"]
    run = next(s for s in resolve["steps"] if s.get("id") == "resolve")["run"]
    assert ".shared_units" in run and "ayasee config check-shared" in run
    plans = [
        s
        for job in wf["jobs"].values()
        for s in job["steps"]
        if "/actions/plan@" in s.get("uses", "") and s["with"]["env"] == "prod"
    ]
    assert plans and all(
        p["with"]["shared-units"] == "${{ needs.resolve.outputs.shared-units }}" for p in plans
    )
