"""Static checks on the release workflow (R68, R8: it is validated, never run from tests)."""

import re

import pytest
import yaml
from conftest import SHA_REF, WORKFLOWS

TEXT = (WORKFLOWS / "release.yml").read_text()
WRITE_JOBS = ["attest", "github-release", "pypi"]


@pytest.fixture(scope="module")
def wf() -> dict:
    return yaml.safe_load(TEXT)


def _needs(wf, job) -> set[str]:
    n = wf["jobs"][job].get("needs", [])
    return {n} if isinstance(n, str) else set(n)


def _ancestors(wf, job) -> set[str]:
    seen: set[str] = set()
    todo = list(_needs(wf, job))
    while todo:
        j = todo.pop()
        if j not in seen:
            seen.add(j)
            todo.extend(_needs(wf, j))
    return seen


def test_triggers(wf):
    on = wf[True]
    assert on["push"] == {"tags": ["v*"]}
    dry = on["workflow_dispatch"]["inputs"]["dry-run"]
    assert dry["type"] == "boolean" and dry["default"] is True


def test_provenance_gate_is_first_and_everything_waits_for_it(wf):
    jobs = list(wf["jobs"])
    assert jobs[0] == "provenance"
    assert any(
        "scripts/check-provenance.sh" in s.get("run", "") for s in wf["jobs"]["provenance"]["steps"]
    )
    assert "needs" not in wf["jobs"]["provenance"]
    for job in jobs[1:]:
        assert "provenance" in _ancestors(wf, job), job


@pytest.mark.parametrize("job", WRITE_JOBS)
def test_write_jobs_skip_on_dry_run(wf, job):
    cond = wf["jobs"][job]["if"]
    assert "inputs.dry-run == false" in cond
    assert "github.event_name == 'push'" in cond


def test_pin_pushes_only_when_not_dry_run(wf):
    push = next(
        s for s in wf["jobs"]["pin"]["steps"] if "git" in s.get("run", "") and "push" in s["run"]
    )
    assert "inputs.dry-run == false" in push["if"]
    others = [s for s in wf["jobs"]["pin"]["steps"] if s is not push]
    assert not any(re.search(r"git (-c \S+ )?push", s.get("run", "")) for s in others)


def test_pypi_uses_environment_and_trusted_publishing_without_token(wf):
    job = wf["jobs"]["pypi"]
    assert job["environment"]["name"] == "pypi"
    assert job["permissions"] == {"id-token": "write"}
    pub = next(
        s for s in job["steps"] if s.get("uses", "").startswith("pypa/gh-action-pypi-publish@")
    )
    assert not {"password", "user", "repository-url"} & set(pub.get("with", {}))
    assert "secrets." not in TEXT


def test_pypi_is_last_and_waits_for_the_release(wf):
    assert {"build", "pin", "attest", "github-release"} <= _ancestors(wf, "pypi")


def test_actions_are_sha_pinned(wf):
    uses = [s["uses"] for j in wf["jobs"].values() for s in j["steps"] if "uses" in s]
    assert uses
    for u in uses:
        assert not u.startswith("./"), u
        assert SHA_REF.search(u), u


def test_permissions_are_per_job(wf):
    assert wf["permissions"] == {}
    perms = {j: wf["jobs"][j]["permissions"] for j in wf["jobs"]}
    assert perms["provenance"] == {"contents": "read"}
    assert perms["build"] == {"contents": "read"}
    assert perms["pin"] == {"contents": "write"}
    assert perms["attest"] == {"id-token": "write", "attestations": "write", "contents": "read"}
    assert perms["github-release"] == {"contents": "write"}
    assert perms["pypi"] == {"id-token": "write"}


def test_attest_covers_wheel_sdist_and_site_tarball(wf):
    step = next(
        s for s in wf["jobs"]["attest"]["steps"] if "attest-build-provenance" in s.get("uses", "")
    )
    subjects = step["with"]["subject-path"].split()
    assert subjects == ["dist/*.whl", "dist/*.tar.gz", "site/*.tar.gz"]


def test_no_expression_in_run_blocks(wf):
    for name, job in wf["jobs"].items():
        for s in job["steps"]:
            assert "${{" not in s.get("run", ""), (name, s)


def test_no_uv_cache(wf):
    assert wf["env"]["UV_NO_CACHE"] == "1"
    for job in wf["jobs"].values():
        for s in job["steps"]:
            if s.get("uses", "").startswith("astral-sh/setup-uv@"):
                assert s["with"]["enable-cache"] is False
            if s.get("uses", "").startswith("actions/setup-node@"):
                assert s["with"]["package-manager-cache"] is False


def test_build_checks_version_builds_both_and_uploads(wf):
    runs = "\n".join(s.get("run", "") for s in wf["jobs"]["build"]["steps"])
    assert "__version__" in runs and "uv build" in runs
    assert "npm ci" in runs and "npm run build" in runs
    assert "databricks-azure.yaml" in runs
    up = [
        s["with"]["name"]
        for s in wf["jobs"]["build"]["steps"]
        if "upload-artifact" in s.get("uses", "")
    ]
    assert sorted(up) == ["cockpit-site", "python-dist"]


def test_pin_runs_the_release_form_with_full_history_and_exposes_both_shas(wf):
    job = wf["jobs"]["pin"]
    checkout = next(s for s in job["steps"] if "actions/checkout" in s.get("uses", ""))
    assert checkout["with"]["fetch-depth"] == 0
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert 'git checkout -b "release/$TAG"' in runs
    assert 'scripts/set-ayasee-ref.sh "$REPO" "$src" "$f"' in runs
    assert set(job["outputs"]) == {"source-sha", "release-sha"}


def test_release_notes_name_both_shas_and_changelog(wf):
    step = next(
        s for s in wf["jobs"]["github-release"]["steps"] if s.get("name") == "Create the release"
    )
    run = step["run"]
    assert "$SOURCE_SHA" in run and "$RELEASE_SHA" in run and "CHANGELOG.md" in run
    assert "gh attestation verify" in run


def test_release_workflow_does_not_select_itself_for_rewriting():
    # the pin job selects workflow files by their "# AYASEE_*" markers; this file must not match
    assert not re.search(r"# AYASEE_(REF|REPO|SHA)", TEXT)


def _all_steps(wf, job):
    return wf["jobs"][job]["steps"]


@pytest.mark.parametrize("job", WRITE_JOBS)
def test_write_jobs_require_a_tag_ref(wf, job):
    assert "github.ref_type == 'tag'" in wf["jobs"][job]["if"]


def test_push_step_requires_a_tag_ref_and_disables_hooks(wf):
    push = next(s for s in _all_steps(wf, "pin") if "push origin" in s.get("run", ""))
    assert "github.ref_type == 'tag'" in push["if"]
    assert "core.hooksPath=/dev/null" in push["run"]


def test_first_job_rejects_a_real_dispatch_and_checks_main_ancestry(wf):
    steps_ = _all_steps(wf, "provenance")
    assert next(s for s in steps_ if "uses" in s)["with"]["fetch-depth"] == 0
    guard = next(s for s in steps_ if "DRY_RUN" in s.get("env", {}))
    assert guard["env"]["REF_TYPE"] == "${{ github.ref_type }}"
    assert guard["env"]["DRY_RUN"] == "${{ inputs.dry-run }}"
    assert '"$REF_TYPE" != tag' in guard["run"] and '"$DRY_RUN" != true' in guard["run"]
    ancestry = "\n".join(s.get("run", "") for s in steps_)
    # T25: origin/main of the fetch-depth 0 checkout, no second fetch
    assert "git fetch" not in ancestry
    assert 'git rev-parse --verify --quiet "refs/remotes/origin/main^{commit}"' in ancestry
    assert "merge-base --is-ancestor HEAD origin/main" in ancestry
    # the ancestry check runs before the provenance script and no longer lives in the pin job
    assert ancestry.index("merge-base") < ancestry.index("check-provenance.sh")
    assert "merge-base" not in "\n".join(s.get("run", "") for s in _all_steps(wf, "pin"))


def test_leftover_check_covers_the_repo_marker_and_selects_files_by_any_marker(wf):
    run = next(s["run"] for s in _all_steps(wf, "pin") if s.get("id") == "pin")
    assert "AYASEE_(REF|REPO|SHA)" in run
    assert "OWNER/ayasee|" in run
