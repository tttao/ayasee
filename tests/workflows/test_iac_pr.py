"""Static checks on the iac-pr reusable workflow."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml
from conftest import AYASEE_USES, ROOT, SHA_REF, WORKFLOWS
from conftest import index as _index
from conftest import steps as _steps

from ayasee import cli
from ayasee.sarif import scrub  # noqa: F401  (registers sarif-scrub)

PATH = WORKFLOWS / "iac-pr.yml"
FORK_SKIP = "github.event.pull_request.head.repo.full_name == github.repository"


@pytest.fixture(scope="module")
def wf() -> dict:
    return yaml.safe_load(PATH.read_text())


def _uses(step):
    return step.get("uses", "")


def test_pr_never_runs_apply(wf):
    text = PATH.read_text()
    for name, job in wf["jobs"].items():
        for step in job["steps"]:
            assert "/actions/apply@" not in _uses(step), name
            assert "/actions/ledger@" not in _uses(step), name
            assert "/actions/guard@" not in _uses(step), name
    assert "ayasee apply" not in text and "tofu apply" not in text
    assert "ledger" not in text.lower()
    assert "deployments:" not in text
    assert "--mirror-deployments" not in text and "SG_MIRROR" not in text


def test_pr_plan_uses_dev_plan_environment_only(wf):
    assert wf["jobs"]["plan-dev"]["environment"] == "dev-plan"
    assert "environment" not in wf["jobs"]["checks"]
    envs = [j.get("environment") for j in wf["jobs"].values() if j.get("environment")]
    # never an environment that can apply or holds the plan key; prod-pr-plan is the read-only
    # identity of the shared-units plan (#5)
    assert sorted(envs) == ["dev-plan", "prod-pr-plan"]
    assert wf["jobs"]["plan-shared"]["environment"] == "prod-pr-plan"
    plans = [s for s in _steps(wf, "plan-dev") if "/actions/plan@" in _uses(s)]
    assert len(plans) == 1
    with_ = plans[0]["with"]
    # the first configured stage (dev by default), issue #1
    assert with_["env"] == "${{ steps.changed.outputs.stage }}"
    assert with_["binding-mode"] == "same-job" and with_["encrypt"] == "false"
    assert "only-units" in with_
    # no plan file or JSON leaves the runner: the only artifacts are the comment and the SARIF
    for job in wf["jobs"].values():
        for step in job["steps"]:
            if "upload-artifact" in _uses(step):
                assert step["with"]["name"] in {
                    "ayasee-pr-comment",
                    "ayasee-pr-comment-shared",
                    "ayasee-sarif",
                }
                assert "plan" not in step["with"]["path"].replace("pr-comment", "")


def test_permissions(wf):
    assert wf["permissions"] == {}
    assert wf["jobs"]["checks"]["permissions"] == {"contents": "read"}
    assert wf["jobs"]["plan-dev"]["permissions"] == {"contents": "read", "id-token": "write"}
    assert wf["jobs"]["upload-sarif"]["permissions"] == {
        "security-events": "write",
        "actions": "read",
        "contents": "read",
    }
    assert wf["jobs"]["comment"]["permissions"] == {
        "contents": "read",  # M5: the base commit checkout
        "pull-requests": "write",
    }


def test_no_job_running_pr_code_holds_a_write_scope(wf):
    runs_pr_code = 0
    for name, job in wf["jobs"].items():
        text = json.dumps(job["steps"])
        if "/actions/plan@" in text or "hcl validate" in text:
            runs_pr_code += 1
            assert not set(job["permissions"]) & {"pull-requests", "security-events"}, name
    assert runs_pr_code == 3  # checks, plan-dev and plan-shared


def test_comment_job_checks_out_only_the_base_commit(wf):
    """M5: the base commit gives the --local form its ./actions; never the PR head (R39)."""
    steps = _steps(wf, "comment")
    checkouts = [s for s in steps if "actions/checkout@" in _uses(s)]
    assert len(checkouts) == 1
    assert checkouts[0]["with"] == {
        "ref": "${{ github.event.pull_request.base.sha }}",
        "persist-credentials": False,
    }
    assert steps.index(checkouts[0]) < _index(steps, lambda s: "/actions/setup@" in _uses(s))
    assert "pull_request.head" not in json.dumps(steps)


def test_plan_dev_refuses_unsupported_settings_before_login(wf):
    steps = _steps(wf, "plan-dev")
    i = _index(steps, lambda s: "config check-supported" in s.get("run", ""))
    assert steps[i]["env"]["CONFIG_PATH"] == "${{ inputs.config-path }}"
    assert "if" not in steps[i] and "exit 1" in steps[i]["run"]
    assert i < _index(steps, lambda s: "azure/login" in _uses(s))


def test_publishing_jobs_run_no_pr_code(wf):
    for name in ("upload-sarif", "comment"):
        for step in _steps(wf, name):
            uses = _uses(step)
            if name == "comment" and "actions/checkout@" in uses:
                assert step["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
                continue
            assert "actions/checkout@" not in uses, name
            assert "azure/login" not in uses and "/actions/plan@" not in uses, name
            assert "terragrunt" not in step.get("run", "") and "tofu" not in step.get("run", "")
        assert "id-token" not in wf["jobs"][name]["permissions"]
    steps = _steps(wf, "comment")
    download = steps[_index(steps, lambda s: "download-artifact" in _uses(s))]
    assert download["with"]["name"] == "ayasee-pr-comment"
    assert wf["jobs"]["comment"]["needs"] == "plan-dev"
    assert wf["jobs"]["comment"]["if"] == (
        "${{ !cancelled() && github.event_name == 'pull_request' "
        "&& needs.plan-dev.result != 'skipped' }}"
    )
    up = _steps(wf, "plan-dev")[-1]
    assert up["with"]["name"] == "ayasee-pr-comment" and up["with"]["retention-days"] == 1
    sarif = _steps(wf, "checks")
    keep = sarif[_index(sarif, lambda s: "upload-artifact" in _uses(s))]
    assert keep["with"]["name"] == "ayasee-sarif" and keep["with"]["retention-days"] == 1


def test_plan_runs_without_the_state_lock(wf):
    steps = _steps(wf, "plan-dev")
    plan = steps[_index(steps, lambda s: "/actions/plan@" in _uses(s))]
    assert plan["with"]["lock"] == "false"
    sh = (ROOT / "actions" / "plan" / "plan.sh").read_text()
    assert "lockflag=(-lock=false)" in sh and "${lockflag[@]}" in sh
    meta = yaml.safe_load((ROOT / "actions" / "plan" / "action.yml").read_text())
    assert meta["inputs"]["lock"]["default"] == "true"


def test_no_pull_request_target(wf):
    code = "\n".join(
        line for line in PATH.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    assert "pull_request_target" not in code
    assert set(wf[True]) == {"workflow_call"}


def test_fork_prs_skip_plan_dev(wf):
    cond = wf["jobs"]["plan-dev"]["if"]
    assert FORK_SKIP in cond
    assert "github.event_name == 'pull_request'" in cond
    # the SARIF upload needs a write token, which a fork does not get
    assert FORK_SKIP in wf["jobs"]["upload-sarif"]["if"]
    assert wf["jobs"]["upload-sarif"]["needs"] == "checks"


def test_plan_dev_checkout_is_the_pr_head_without_credentials(wf):
    steps = _steps(wf, "plan-dev")
    co = steps[0]
    assert "actions/checkout@" in _uses(co)
    assert co["with"]["persist-credentials"] is False
    assert co["with"]["ref"] == "${{ github.event.pull_request.head.sha }}"
    changed = steps[_index(steps, lambda s: s.get("id") == "changed")]
    assert "github.event.pull_request.base.sha" in json.dumps(changed["env"])
    assert "ayasee changed-units" in changed["run"]


def test_plan_skipped_without_changes_and_comment_says_so(wf):
    steps = _steps(wf, "plan-dev")
    for step in steps:
        if "/actions/plan@" in _uses(step) or "azure/login" in _uses(step):
            assert "steps.changed.outputs.any == 'true'" in step["if"]
    body = steps[_index(steps, lambda s: s.get("id") == "body")]
    assert "No IaC changes" in body["run"]


def test_login_then_plan_then_policy_then_comment_body(wf):
    steps = _steps(wf, "plan-dev")
    login = _index(steps, lambda s: "azure/login" in _uses(s))
    plan = _index(steps, lambda s: "/actions/plan@" in _uses(s))
    policy = _index(steps, lambda s: "/actions/policy@" in _uses(s))
    body = _index(steps, lambda s: s.get("id") == "body")
    assert login < plan < policy < body
    assert not any("ayasee pr-comment" in s.get("run", "") for s in steps)
    pol = steps[policy]["with"]
    assert pol["stage"] == "${{ steps.changed.outputs.stage }}"
    assert "steps.plan.outputs.json-dir" in pol["plan-json"]


def test_comment_body_is_only_the_rendered_summary(wf):
    steps = _steps(wf, "plan-dev")
    body = steps[_index(steps, lambda s: s.get("id") == "body")]
    assert "steps.plan.outputs.summary-path" in body["env"]["SUMMARY"]
    # nothing reads plan JSON or the plan directory into the comment
    assert "json" not in body["run"].lower()


def test_checks_job_runs_fmt_validate_and_scans(wf):
    runs = "\n".join(s.get("run", "") for s in _steps(wf, "checks"))
    assert "tofu fmt -check -recursive" in runs
    assert "terragrunt hcl format --check" in runs
    assert "terragrunt hcl validate" in runs
    steps = _steps(wf, "checks")
    trivy = steps[_index(steps, lambda s: "trivy-action" in _uses(s))]["with"]
    assert trivy["scan-type"] == "config" and trivy["format"] == "sarif"
    assert trivy["cache"] == "false"
    checkov = steps[_index(steps, lambda s: s.get("id") == "checkov")]
    assert re.search(r"uvx \"checkov@\$CHECKOV_VERSION\"", checkov["run"])
    assert "--baseline .ayasee/checkov.baseline" in checkov["run"]
    assert "-f .ayasee/checkov.baseline" in checkov["run"]
    assert "--output sarif" in checkov["run"]
    default = wf[True]["workflow_call"]["inputs"]["checkov-version"]["default"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", default)


def test_sarif_is_scrubbed_before_it_leaves_checks(wf):
    steps = _steps(wf, "checks")
    keep_i = _index(steps, lambda s: "upload-artifact" in _uses(s))
    paths = steps[keep_i]["with"]["path"].splitlines()
    assert len(paths) == 2
    for path in paths:
        scrub_i = _index(
            steps,
            lambda s, path=path: (
                "ayasee sarif-scrub" in s.get("run", "") and s["env"]["SARIF_OUT"] == path
            ),
        )
        assert scrub_i < keep_i, path
        assert "raw" not in path and "checkov/" not in path
    # the upload job reads those files back and nothing else
    up_steps = _steps(wf, "upload-sarif")
    uploads = [s for s in up_steps if "upload-sarif" in _uses(s)]
    assert {u["with"]["category"] for u in uploads} == {"ayasee-trivy", "ayasee-checkov"}
    for u in uploads:
        assert u["with"]["sarif_file"].rsplit("/", 1)[1] in {"trivy.sarif", "checkov.sarif"}
        assert "raw" not in u["with"]["sarif_file"]
    dl = up_steps[0]
    assert "download-artifact" in _uses(dl) and dl["with"]["name"] == "ayasee-sarif"


def test_every_scanner_runs_even_after_an_earlier_failure(wf):
    steps = _steps(wf, "checks")
    for id_ in ("trivy", "checkov"):
        step = steps[_index(steps, lambda s, id_=id_: s.get("id") == id_)]
        assert step["if"] == "${{ !cancelled() }}"
    keep = steps[_index(steps, lambda s: "upload-artifact" in _uses(s))]
    assert keep["if"] == "${{ !cancelled() }}"


def test_failed_change_detection_never_reads_as_no_changes(wf):
    steps = _steps(wf, "plan-dev")
    body = steps[_index(steps, lambda s: s.get("id") == "body")]
    assert body["env"]["CHANGED_OUTCOME"] == "${{ steps.changed.outcome }}"
    run = body["run"]
    assert run.index('"$CHANGED_OUTCOME" != success') < run.index("No IaC changes")
    out = tempfile.mkdtemp()
    for changed, any_ in (("failure", ""), ("success", "false")):
        env = {
            **os.environ,
            "RUNNER_TEMP": out,
            "GITHUB_OUTPUT": f"{out}/o",
            "RUN_URL": "https://x/run/1",
            "CHANGED_OUTCOME": changed,
            "ANY": any_,
            "PLAN_OUTCOME": "skipped",
            "POLICY_OUTCOME": "skipped",
            "SUMMARY": "",
        }
        subprocess.run(["bash", "-c", run], env=env, check=True)
        text = Path(out, "pr-comment.md").read_text()
        assert ("No IaC changes" in text) == (changed == "success")
        assert ("failed before it started" in text) == (changed == "failure")


def test_every_uses_is_sha_pinned_or_owner_ref(wf):
    refs = set()
    for name, job in wf["jobs"].items():
        for step in job["steps"]:
            uses = _uses(step)
            if not uses:
                continue
            assert SHA_REF.search(uses), f"{name}: {uses}"
            m = AYASEE_USES.match(uses)
            if m:
                refs.add(m.group(1))
    assert len(refs) == 1
    assert "AYASEE_REF" in PATH.read_text()
    for line in PATH.read_text().splitlines():
        if "uses:" in line and "/ayasee/actions/" not in line:
            assert re.search(r"@[0-9a-f]{40} # v\d", line), line


def test_no_expression_in_run_blocks(wf):
    for name, job in wf["jobs"].items():
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), f"{name}: {step.get('name')}"


def test_workflow_call_only_and_no_secrets_needed(wf):
    # only the optional provider tokens of identity: secrets (issue #1)
    secrets = wf[True]["workflow_call"]["secrets"]
    assert set(secrets) == {"CLOUDFLARE_API_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"}
    assert all(v["required"] is False for v in secrets.values())
    assert "inherit" not in PATH.read_text()


def test_set_ayasee_ref_handles_this_workflow(tmp_path):
    local = tmp_path / "pr.yml"
    shutil.copy(PATH, local)
    subprocess.run([ROOT / "scripts" / "set-ayasee-ref.sh", "--local", local], check=True)
    text = local.read_text()
    assert "uses: ./actions/plan # AYASEE_REF" in text
    assert "OWNER/ayasee" not in text


def test_sarif_has_no_values(tmp_path):
    secret = "s3cr3t-after-value"
    sarif = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "t", "rules": [{"id": "X1"}]}},
                "results": [
                    {
                        "ruleId": "X1",
                        "message": {"text": f'attribute "after" = "{secret}" is public'},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "stages/dev/a/main.tf"},
                                    "region": {"startLine": 1, "snippet": {"text": secret}},
                                    "contextRegion": {"snippet": {"text": secret}},
                                }
                            }
                        ],
                    }
                ],
            }
        ],
    }
    src, dst = tmp_path / "in.sarif", tmp_path / "out.sarif"
    src.write_text(json.dumps(sarif))
    assert cli.main(["sarif-scrub", "--in", str(src), "--out", str(dst)]) == 0
    out = dst.read_text()
    assert secret not in out and "after" not in out
    assert json.loads(out)["runs"][0]["results"][0]["message"]["text"] == "X1"


def test_missing_artifact_is_a_warning_not_a_red_publishing_job(wf):
    for job, name, skipped in (
        ("upload-sarif", "ayasee-sarif", "found"),
        ("comment", "ayasee-pr-comment", None),
    ):
        steps = _steps(wf, job)
        i = _index(steps, lambda s: s.get("id") == "download")
        assert steps[i]["continue-on-error"] is True
        assert steps[i]["with"]["name"] == name
        warn = steps[i + 1]
        assert warn["if"] == "${{ steps.download.outcome != 'success' }}"
        assert "::warning::no " in warn["run"]
        later = steps[i + 2 :]
        if skipped:
            found = next(s for s in later if s.get("id") == skipped)
            assert found["if"] == "${{ steps.download.outcome == 'success' }}"
        else:
            post = next(s for s in later if "ayasee pr-comment" in s.get("run", ""))
            assert post["if"] == "${{ steps.download.outcome == 'success' }}"


def test_no_uv_cache_anywhere():
    setup = yaml.safe_load((ROOT / "actions" / "setup" / "action.yml").read_text())
    steps = setup["runs"]["steps"]
    uv = steps[_index(steps, lambda s: "setup-uv" in _uses(s))]
    assert uv["with"]["enable-cache"] is False
    install = steps[_index(steps, lambda s: s.get("name", "").startswith("Install tools"))]
    assert install["env"]["UV_NO_CACHE"] == "1"
    for path in WORKFLOWS.glob("*.yml"):
        for job in yaml.safe_load(path.read_text())["jobs"].values():
            for step in job.get("steps", []):
                if "setup-uv" in _uses(step):
                    assert step["with"]["enable-cache"] is False, path.name
    pr = yaml.safe_load(PATH.read_text())
    assert pr["jobs"]["checks"]["env"]["UV_NO_CACHE"] == "1"  # uvx checkov


def test_checks_are_scoped_to_the_iac_root(wf):
    """Issue #4: in a monorepo, application code beside the estate is not judged (iac_root)."""
    steps = _steps(wf, "checks")
    cfg = steps[_index(steps, lambda s: s.get("id") == "config")]
    assert "iac-root=" in cfg["run"] and ".iac_root" in cfg["run"]
    root = "${{ steps.config.outputs.iac-root }}"
    fmt = [
        s for s in steps if "format --check" in s.get("run", "") or "fmt -check" in s.get("run", "")
    ]
    assert len(fmt) == 2
    for s in fmt:
        assert s["env"]["IAC_ROOT"] == root and '"$IAC_ROOT"' in s["run"], s["name"]
    trivy = steps[_index(steps, lambda s: "trivy-action" in _uses(s))]["with"]
    assert trivy["scan-ref"] == root
    checkov = steps[_index(steps, lambda s: s.get("id") == "checkov")]
    assert checkov["env"]["IAC_ROOT"] == root
    assert '--directory "$IAC_ROOT"' in checkov["run"] and "--directory ." not in checkov["run"]
    # the workflows framework runs only when the repository is the estate
    assert 'if [ "$IAC_ROOT" = . ]; then frameworks+=(github_actions); fi' in checkov["run"]


def test_changed_units_ignores_code_outside_the_iac_root(wf):
    steps = _steps(wf, "plan-dev")
    changed = steps[_index(steps, lambda s: s.get("id") == "changed")]
    assert '--iac-root "$iac_root"' in changed["run"]
    assert '--config-path "$CONFIG_PATH"' in changed["run"]


def test_sarif_paths_are_made_repository_relative(wf):
    scrubs = [s for s in _steps(wf, "checks") if "ayasee sarif-scrub" in s.get("run", "")]
    assert len(scrubs) == 2
    for s in scrubs:
        assert s["env"]["IAC_ROOT"] == "${{ steps.config.outputs.iac-root }}"
        assert '--uri-prefix "$IAC_ROOT"' in s["run"]


def test_shared_units_are_found_without_an_identity(wf):
    """#5: the job that decides whether to plan shared units holds no identity or environment, so
    a PR that touches none of them never waits for the prod-pr-plan approval."""
    job = wf["jobs"]["shared-changes"]
    assert job["permissions"] == {"contents": "read"} and "environment" not in job
    assert FORK_SKIP in job["if"]
    step = job["steps"][_index(job["steps"], lambda s: s.get("id") == "shared")]
    assert "--env prod --shared" in step["run"] and "--iac-root" in step["run"]
    assert not any("azure/login" in _uses(s) or "/actions/plan@" in _uses(s) for s in job["steps"])


def test_plan_shared_plans_read_only_against_prod_and_never_applies(wf):
    job = wf["jobs"]["plan-shared"]
    assert job["needs"] == "shared-changes"
    assert job["if"] == "${{ needs.shared-changes.outputs.units != '' }}"
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    steps = job["steps"]
    supported = _index(steps, lambda s: "config check-supported" in s.get("run", ""))
    login = _index(steps, lambda s: "azure/login" in _uses(s))
    plan_i = _index(steps, lambda s: "/actions/plan@" in _uses(s))
    assert supported < login < plan_i
    plan = steps[plan_i]["with"]
    assert plan["env"] == "prod" and plan["binding-mode"] == "replan"
    assert plan["encrypt"] == "false" and plan["lock"] == "false"
    assert plan["only-units"] == "${{ needs.shared-changes.outputs.units }}"
    assert plan["shared-units"] == "${{ needs.shared-changes.outputs.shared-units }}"
    assert "key-vault-key-id" not in plan
    pol = steps[_index(steps, lambda s: "/actions/policy@" in _uses(s))]["with"]
    assert pol["stage"] == "prod"


def test_shared_plan_has_its_own_sticky_comment(wf):
    job = wf["jobs"]["comment-shared"]
    assert job["needs"] == "plan-shared"
    assert job["permissions"] == {"contents": "read", "pull-requests": "write"}
    steps = job["steps"]
    co = [s for s in steps if "actions/checkout@" in _uses(s)]
    assert len(co) == 1 and co[0]["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
    post = steps[-1]
    assert "--marker pr-plan-shared" in post["run"]
    dl = steps[_index(steps, lambda s: "download-artifact" in _uses(s))]
    assert dl["with"]["name"] == "ayasee-pr-comment-shared"
    # the dev plan's comment does not wait for the shared plan
    assert wf["jobs"]["comment"]["needs"] == "plan-dev"


def test_checks_refuse_a_shared_unit_outside_prod(wf):
    steps = _steps(wf, "checks")
    i = _index(steps, lambda s: "config check-shared" in s.get("run", ""))
    assert "exit 1" in steps[i]["run"]
