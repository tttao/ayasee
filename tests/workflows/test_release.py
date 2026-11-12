"""Static checks on the release workflow (R68, R8: it is validated, never run from tests)."""

import os
import re
import shutil
import subprocess

import pytest
import yaml
from conftest import ROOT, SHA_REF, WORKFLOWS

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
    assert perms["pin"] == {"contents": "read"}  # it never pushes
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


def test_pin_never_pushes_and_verifies_the_maintainer_pushed_branch(wf):
    """The release commit changes .github/workflows/, which no Actions token may push; the
    maintainer pushes release/<tag> with scripts/pin-release.sh and the job only verifies it."""
    job = wf["jobs"]["pin"]
    checkout = next(s for s in job["steps"] if "actions/checkout" in s.get("uses", ""))
    assert checkout["with"]["fetch-depth"] == 0
    assert not any(re.search(r"git (-c \S+ )*push", s.get("run", "")) for s in job["steps"])
    pin = next(s for s in job["steps"] if s.get("id") == "pin")
    assert 'scripts/pin-release.sh "$REPO" "$TAG"' in pin["run"]
    verify = next(s for s in job["steps"] if s.get("id") == "verify")
    assert "github.ref_type == 'tag'" in verify["if"] and "inputs.dry-run == false" in verify["if"]
    run = verify["run"]
    assert "core.hooksPath=/dev/null" in run
    assert '"$pushed^"' in run and "$SOURCE_SHA" in run  # one commit on top of the tag
    assert '"$pushed^{tree}"' in run and '"HEAD^{tree}"' in run  # the same content
    assert set(job["outputs"]) == {"source-sha", "release-sha"}
    assert "steps.verify.outputs.release-sha" in job["outputs"]["release-sha"]


def _clone(tmp_path):
    def git(*a, cwd=None):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    repo = tmp_path / "ayasee"
    git("clone", "-q", "--no-hardlinks", str(ROOT), str(repo))
    for rel in ("scripts/pin-release.sh", "scripts/set-ayasee-ref.sh"):
        shutil.copy2(ROOT / rel, repo / rel)
    init = repo / "src/ayasee/__init__.py"
    init.write_text(re.sub(r'__version__ = ".*"', '__version__ = "9.9.9"', init.read_text()))
    git("add", "-A", cwd=repo)
    git("commit", "-q", "--allow-empty", "-m", "test", cwd=repo)
    return repo, git


def _pin(repo, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(
        ["scripts/pin-release.sh", *args], cwd=repo, env=env, capture_output=True, text=True
    )


def test_pin_release_script_makes_one_commit_that_pins_every_workflow(tmp_path):
    repo, git = _clone(tmp_path)
    src = git("rev-parse", "HEAD", cwd=repo)
    proc = _pin(repo, "o/ayasee", "v9.9.9")
    assert proc.returncode == 0, proc.stderr
    out = dict(line.split("=", 1) for line in proc.stdout.splitlines())
    assert out["source-sha"] == src
    assert git("rev-parse", "HEAD^", cwd=repo) == src
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo) == "release/v9.9.9"
    changed = git("diff", "--name-only", src, "HEAD", cwd=repo).splitlines()
    assert changed and all(f.startswith(".github/workflows/") for f in changed)
    for f in changed:
        text = (repo / f).read_text()
        assert "OWNER/ayasee" not in text and "o/ayasee/actions/" in text and src in text
    # a second run makes the same tree: that is what the workflow compares
    tree = git("rev-parse", "HEAD^{tree}", cwd=repo)
    git("switch", "-q", "--detach", src, cwd=repo)
    git("branch", "-q", "-D", "release/v9.9.9", cwd=repo)
    assert _pin(repo, "o/ayasee", "v9.9.9").returncode == 0
    assert git("rev-parse", "HEAD^{tree}", cwd=repo) == tree


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("o/ayasee", "v1.0.0"), "does not match"),
        (("o/ayasee",), "usage"),
        (("bad repo", "v9.9.9"), "invalid repository"),
    ],
)
def test_pin_release_script_refuses(tmp_path, args, message):
    repo, _ = _clone(tmp_path)
    proc = _pin(repo, *args)
    assert proc.returncode != 0 and message in proc.stderr


def test_pin_release_script_refuses_a_dirty_tree_and_an_existing_branch(tmp_path):
    repo, git = _clone(tmp_path)
    (repo / "README.md").write_text("dirty")
    assert "working tree has changes" in _pin(repo, "o/ayasee", "v9.9.9").stderr
    git("checkout", "-q", "README.md", cwd=repo)
    git("branch", "release/v9.9.9", cwd=repo)
    assert "already exists" in _pin(repo, "o/ayasee", "v9.9.9").stderr
