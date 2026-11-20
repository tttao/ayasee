"""scripts/check-archive.sh: a commit's tarball holds no submodule and no symlink a runner
can't stage as an action (#49)."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-archive.sh"


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "actions" / "run").mkdir(parents=True)
    (r / "actions" / "run" / "action.yml").write_text("name: run\n")
    return r


def _commit(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c")


def _run(repo: Path, rev: str = "HEAD"):
    return subprocess.run([str(SCRIPT), rev, str(repo)], capture_output=True, text=True)


def test_repository_passes():
    r = subprocess.run([str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_symlink_inside_the_archive_passes(repo):
    (repo / "link").symlink_to("actions/run/action.yml")
    (repo / "dirlink").symlink_to("actions")
    _commit(repo)
    r = _run(repo)
    assert r.returncode == 0, r.stderr


def test_dangling_symlink_fails(repo):
    (repo / ".claude" / "skills").mkdir(parents=True)
    (repo / ".claude" / "skills" / "x").symlink_to("../vendor/mengto-skills/x")
    _commit(repo)
    r = _run(repo)
    assert r.returncode == 1
    assert "dangling symlink: .claude/skills/x -> ../vendor/mengto-skills/x" in r.stderr


def test_symlink_leaving_the_archive_fails(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("x\n")
    (repo / "escape").symlink_to(outside)
    _commit(repo)
    r = _run(repo)
    assert r.returncode == 1
    assert "symlink leaves the archive: escape" in r.stderr


def test_submodule_fails(repo):
    gitlink = "0" * 39 + "1"  # a submodule entry needs no real commit behind it
    _git(repo, "add", "-A")
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{gitlink},vendor/skills")
    _git(repo, "commit", "-q", "-m", "c")  # no add -A: the gitlink has no directory behind it
    r = _run(repo)
    assert r.returncode == 1
    assert "submodule in the tree (missing from the action download): vendor/skills" in r.stderr


def test_unknown_revision_fails(repo):
    _commit(repo)
    r = _run(repo, "no-such-rev")
    assert r.returncode == 1
    assert "not a commit: no-such-rev" in r.stderr
