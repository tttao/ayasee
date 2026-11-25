"""iac-import (issue #58): the import blocks of a unit's first adoption, from a plan made in CI.

Dispatched by hand on a branch. resolve checks the inputs with no environment; import plans the
first stage read-only in <first stage>-plan (the environment iac-pr already hands a pull
request's code), runs import-plan, deletes every plan file and JSON, and uploads only the
imports.tf files and a values-free summary; comment posts it without running branch code."""

import os
import re
import subprocess

import yaml
from conftest import AYASEE_USES, SHA_REF, WORKFLOWS
from conftest import index as _index
from conftest import steps as _steps

TEXT = (WORKFLOWS / "iac-import.yml").read_text()
WF = yaml.safe_load(TEXT)
PR = yaml.safe_load((WORKFLOWS / "iac-pr.yml").read_text())
SECRET_REF = re.compile(r"secrets\.")
TOKENS = (
    "CLOUDFLARE_API_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AYASEE_PROVIDER_ENV",
)
ON_A_BRANCH = "github.event_name == 'workflow_dispatch' && startsWith(github.ref, 'refs/heads/')"


def _uses(step):
    return step.get("uses", "")


def test_it_is_only_callable_and_holds_no_token_by_default():
    assert list(WF[True]) == ["workflow_call"]  # `on:` parses as True
    assert WF["permissions"] == {}
    call = WF[True]["workflow_call"]
    assert set(call["inputs"]) == {"stage", "unit", "comment", "tool-mirror", "config-path"}
    for name in ("stage", "unit", "tool-mirror"):
        assert call["inputs"][name]["required"] is False and call["inputs"][name]["default"] == ""
    assert call["inputs"]["comment"]["type"] == "boolean"
    assert call["inputs"]["comment"]["default"] is True
    # the same provider secrets as iac-pr, optional, described as environment secrets
    assert set(call["secrets"]) == set(TOKENS)
    for token in TOKENS:
        assert call["secrets"][token]["required"] is False
        assert "environment secret" in " ".join(call["secrets"][token]["description"].split())
    assert "pull_request_target" not in TEXT and "inherit" not in TEXT


def test_every_job_runs_for_a_dispatch_on_a_branch_only():
    assert WF["jobs"]["resolve"]["if"] == "${{ " + ON_A_BRANCH + " }}"
    for name in ("import", "comment"):
        assert ON_A_BRANCH in WF["jobs"][name]["if"], name


def test_resolve_checks_the_inputs_with_no_environment_and_no_secret():
    job = WF["jobs"]["resolve"]
    assert "environment" not in job
    assert job["permissions"] == {"contents": "read"}
    assert not SECRET_REF.search(str(job))
    co = next(s for s in job["steps"] if "actions/checkout@" in _uses(s))
    assert co["with"] == {"ref": "${{ github.sha }}", "persist-credentials": False}
    target = next(s for s in job["steps"] if s.get("id") == "target")
    assert "ayasee import-target" in target["run"]
    assert target["env"]["STAGE"] == "${{ inputs.stage }}"
    assert target["env"]["UNIT"] == "${{ inputs.unit }}"
    for key in (
        "environment",
        "stage",
        "units-root",
        "engine",
        "identity",
        "provider-env-names",
        "only-units",
    ):
        assert job["outputs"][key] == f"${{{{ steps.target.outputs.{key} }}}}"


def test_resolve_writes_outputs_or_errors(tmp_path):
    step = next(s for s in _steps(WF, "resolve") if s.get("id") == "target")
    shim = tmp_path / "bin"
    shim.mkdir()
    fake = shim / "ayasee"
    out = tmp_path / "out"
    env = {
        **os.environ,
        "PATH": f"{shim}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(out),
        "CONFIG_PATH": "x",
        "STAGE": "",
        "UNIT": "",
    }
    fake.write_text("#!/bin/bash\necho environment=test-plan\necho stage=test\n")
    fake.chmod(0o755)
    run = ["bash", "-e", "-o", "pipefail", "-c", step["run"]]
    proc = subprocess.run(run, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert out.read_text() == "environment=test-plan\nstage=test\n"
    out.unlink()
    fake.write_text("#!/bin/bash\necho environment=x\necho 'import-target: refused' >&2\nexit 1\n")
    proc = subprocess.run(run, env=env, capture_output=True, text=True)
    assert proc.returncode != 0
    assert "::error::import-target: refused" in proc.stdout
    assert not out.exists() or "environment" not in out.read_text()


def test_the_import_job_runs_in_the_environment_resolve_named():
    job = WF["jobs"]["import"]
    assert job["needs"] == "resolve"
    assert "needs.resolve.outputs.environment != ''" in job["if"]
    assert job["environment"] == "${{ needs.resolve.outputs.environment }}"
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    steps = job["steps"]
    co = _index(steps, lambda s: "actions/checkout@" in _uses(s))
    supported = _index(steps, lambda s: "config check-supported" in s.get("run", ""))
    login = _index(steps, lambda s: "azure/login" in _uses(s))
    plan = _index(steps, lambda s: "/actions/plan@" in _uses(s))
    imports = _index(steps, lambda s: s.get("id") == "imports")
    delete = _index(steps, lambda s: s.get("id") == "delete")
    upload = _index(steps, lambda s: "upload-artifact" in _uses(s))
    assert co < supported < login < plan < imports < delete < upload
    assert steps[co]["with"] == {"ref": "${{ github.sha }}", "persist-credentials": False}
    assert "needs.resolve.outputs.identity == 'oidc'" in steps[login]["if"]
    for s in steps[:plan]:
        assert not SECRET_REF.search(str(s)), s.get("name")


def test_the_plan_is_the_read_only_plan_of_iac_pr():
    steps = _steps(WF, "import")
    plan = steps[_index(steps, lambda s: "/actions/plan@" in _uses(s))]
    w = plan["with"]
    assert w["env"] == "${{ needs.resolve.outputs.stage }}"
    assert w["only-units"] == "${{ needs.resolve.outputs.only-units }}"
    assert w["binding-mode"] == "same-job"
    assert w["encrypt"] == "false" and w["lock"] == "false"
    ident = "needs.resolve.outputs.identity"
    for token in TOKENS:
        assert plan["env"][token] == f"${{{{ {ident} == 'secrets' && secrets.{token} || '' }}}}"
    assert plan["env"]["AYASEE_PROVIDER_ENV_NAMES"] == (
        "${{ needs.resolve.outputs.provider-env-names }}"
    )
    # same environment variables as the first-stage plan of iac-pr, with its identity source
    pr_plan = next(s for s in _steps(PR, "plan-first-stage") if s.get("id") == "plan")
    assert set(plan["env"]) == set(pr_plan["env"])
    assert not any("/actions/apply@" in _uses(s) or "/actions/ledger@" in _uses(s) for s in steps)
    assert "AYASEE_PLAN_PASSPHRASE" not in TEXT and "ledger-token" not in TEXT


def test_secrets_reach_the_plan_step_and_the_cloudflare_token_the_lookups_only():
    steps = _steps(WF, "import")
    holders = {s.get("id"): s for s in steps if SECRET_REF.search(str(s))}
    assert set(holders) == {"plan", "imports"}
    imports = holders["imports"]
    assert SECRET_REF.findall(str(imports)) == ["secrets."]
    assert imports["env"]["CLOUDFLARE_API_TOKEN"] == (
        "${{ needs.resolve.outputs.identity == 'secrets' && secrets.CLOUDFLARE_API_TOKEN || '' }}"
    )
    for job in ("resolve", "comment"):
        assert not SECRET_REF.search(str(WF["jobs"][job])), job


def test_import_plan_reads_the_plan_directory_of_the_stage():
    step = next(s for s in _steps(WF, "import") if s.get("id") == "imports")
    assert step["if"] == "${{ steps.plan.outcome == 'success' }}"
    assert step["env"]["JSON_DIR"] == "${{ steps.plan.outputs.json-dir }}"
    for flag in ("--plan-dir", "--out-dir", "--unit-prefix", "--summary"):
        assert flag in step["run"]
    # whatever the plan's code wrote into the artifact directory is gone first
    assert step["run"].index('rm -rf "$RUNNER_TEMP/ayasee-import"') < step["run"].index(
        "ayasee import-plan"
    )


def test_plan_files_and_json_are_deleted_whatever_happened():
    step = next(s for s in _steps(WF, "import") if s.get("id") == "delete")
    assert step["if"] == "${{ always() }}"
    assert 'rm -rf "$RUNNER_TEMP/ayasee"' in step["run"]
    assert "continue-on-error" not in step


def test_only_the_imports_and_the_summary_leave_the_job(tmp_path):
    steps = _steps(WF, "import")
    uploads = [s for s in steps if "upload-artifact" in _uses(s)]
    assert len(uploads) == 1
    w = uploads[0]["with"]
    assert w["name"] == "ayasee-imports"
    assert w["path"] == "${{ runner.temp }}/ayasee-import"
    assert "ayasee/" not in w["path"]  # never the plan action's working directory
    assert uploads[0]["if"] == "${{ !cancelled() && steps.delete.outcome == 'success' }}"
    # no plan output is ever a job output or a cache
    assert "outputs" not in WF["jobs"]["import"]
    assert "actions/cache" not in TEXT


def test_the_body_says_why_when_there_is_no_summary(tmp_path):
    step = next(s for s in _steps(WF, "import") if s.get("id") == "body")
    assert step["if"] == "${{ !cancelled() }}"
    env = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path),
        "RUN_URL": "https://example.invalid/run",
        "STAGE": "test",
        "SHA": "a" * 40,
        "PLAN_OUTCOME": "failure",
        "IMPORTS_OUTCOME": "skipped",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "step-summary.md"),
    }
    run = ["bash", "-e", "-o", "pipefail", "-c", step["run"]]
    assert subprocess.run(run, env=env).returncode == 0
    body = (tmp_path / "ayasee-import" / "import-summary.md").read_text()
    assert "plan of test failed" in body and "https://example.invalid/run" in body
    (tmp_path / "ayasee-import" / "import-summary.md").unlink()
    env.update(PLAN_OUTCOME="success", IMPORTS_OUTCOME="failure")
    assert subprocess.run(run, env=env).returncode == 0
    body = (tmp_path / "ayasee-import" / "import-summary.md").read_text()
    assert "import-plan failed" in body and "nothing was written" in body
    # a summary import-plan wrote is kept, under a heading
    (tmp_path / "ayasee-import" / "import-summary.md").write_text("#### `stages/test/dns`\n")
    env.update(IMPORTS_OUTCOME="failure")
    assert subprocess.run(run, env=env).returncode == 0
    body = (tmp_path / "ayasee-import" / "import-summary.md").read_text()
    assert body.startswith("### Import plan of test at `" + "a" * 40 + "`")
    assert "#### `stages/test/dns`" in body and "ambiguous" in body
    assert "never commits" in body


def test_the_comment_runs_no_branch_code():
    job = WF["jobs"]["comment"]
    assert job["needs"] == ["resolve", "import"]
    assert "inputs.comment" in job["if"] and "needs.import.result != 'skipped'" in job["if"]
    assert job["permissions"] == {"contents": "read", "pull-requests": "write"}
    co = [s for s in job["steps"] if "actions/checkout@" in _uses(s)]
    assert len(co) == 1
    assert co[0]["with"] == {
        "ref": "${{ github.event.repository.default_branch }}",
        "persist-credentials": False,
    }
    dl = next(s for s in job["steps"] if "download-artifact" in _uses(s))
    assert dl["with"]["name"] == "ayasee-imports"
    post = job["steps"][-1]
    assert "ayasee pr-comment" in post["run"]
    assert "--head-branch" in post["run"] and "--marker import-plan" in post["run"]
    assert post["env"]["BRANCH"] == "${{ github.ref_name }}"


def test_least_privilege_per_job():
    # R39: the job that evaluates the branch's HCL holds no write token but id-token
    for name, job in WF["jobs"].items():
        writes = {k for k, v in job.get("permissions", {}).items() if v == "write"}
        expected = {"import": {"id-token"}, "comment": {"pull-requests"}}.get(name, set())
        assert writes == expected, name


def test_pins_and_no_expression_in_a_run_script():
    for job in WF["jobs"].values():
        for step in job["steps"]:
            uses = _uses(step)
            if "/ayasee/actions/" in uses:
                assert AYASEE_USES.match(uses), uses
                assert step.get("with", {}).get("tool-mirror", "${{ inputs.tool-mirror }}") == (
                    "${{ inputs.tool-mirror }}"
                )
            elif uses:
                assert SHA_REF.search(uses), uses
            # inputs and outputs reach scripts through env only (no template injection)
            assert "${{" not in step.get("run", ""), step.get("name")
    assert "AYASEE_REF" in TEXT


def test_never_commits_or_pushes():
    for word in ("git push", "git commit", "contents: write", "create_tree", "git add"):
        assert word not in TEXT
