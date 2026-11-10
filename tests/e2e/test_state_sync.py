"""The fixture's state-sync.sh against a local bare repository (no network)."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "e2e" / "fixture" / "scripts" / "state-sync.sh"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def git(cwd, *args):
    subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        env={**os.environ, **GIT_ENV},
    )


def clone(tmp_path, origin, name):
    d = tmp_path / name
    subprocess.run(["git", "clone", "-q", str(origin), str(d)], check=True, capture_output=True)
    return d


def run(cwd, *args, **env):
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("GITHUB_", "AYASEE_"))}
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=cwd,
        env={**clean, **GIT_ENV, **env},
        capture_output=True,
        text=True,
    )


def origin_with_commit(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = clone(tmp_path, origin, "seed")
    (seed / "README").write_text("x")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "x")
    git(seed, "push", "-q", "origin", "HEAD:main")
    return origin


def test_state_round_trips_through_the_branch(tmp_path):
    origin = origin_with_commit(tmp_path)
    a, b = clone(tmp_path, origin, "a"), clone(tmp_path, origin, "b")
    state = a / ".ayasee-e2e-state"
    (state / "prod").mkdir(parents=True)
    (state / "prod" / "base.tfstate").write_text('{"serial": 1}')
    proc = run(a, "push", str(state), "prod/base.tfstate", AYASEE_EXTRA_SECRET="tok")
    assert proc.returncode == 0, proc.stderr
    (state / "prod" / "grants.tfstate").write_text('{"serial": 7}')
    assert (
        run(a, "push", str(state), "prod/grants.tfstate", AYASEE_EXTRA_SECRET="tok").returncode == 0
    )
    restored = b / ".ayasee-e2e-state"
    assert run(b, "pull", str(restored), AYASEE_EXTRA_SECRET="tok").returncode == 0
    assert (restored / "prod" / "base.tfstate").read_text() == '{"serial": 1}'
    assert (restored / "prod" / "grants.tfstate").read_text() == '{"serial": 7}'


def test_a_relative_state_dir_is_relative_to_the_hook_directory(tmp_path):
    # Terragrunt runs the hooks in a subdirectory; the script then moves to the repository top
    origin = origin_with_commit(tmp_path)
    a, b = clone(tmp_path, origin, "a"), clone(tmp_path, origin, "b")
    for repo in (a, b):
        (repo / "hook").mkdir()
    (a / "hook" / "state" / "prod").mkdir(parents=True)
    (a / "hook" / "state" / "prod" / "base.tfstate").write_text('{"serial": 3}')
    proc = run(a / "hook", "push", "state", "prod/base.tfstate", AYASEE_EXTRA_SECRET="tok")
    assert proc.returncode == 0, proc.stderr
    proc = run(b / "hook", "pull", "state", AYASEE_EXTRA_SECRET="tok")
    assert proc.returncode == 0, proc.stderr
    assert (b / "hook" / "state" / "prod" / "base.tfstate").read_text() == '{"serial": 3}'
    assert not (b / "state").exists()


def test_fails_closed_on_actions_without_a_token(tmp_path):
    origin = origin_with_commit(tmp_path)
    a = clone(tmp_path, origin, "a")
    for cmd in (("pull", str(a / "s")), ("push", str(a / "s"), "x.tfstate")):
        proc = run(a, *cmd, GITHUB_ACTIONS="true", GITHUB_EVENT_NAME="push")
        assert proc.returncode == 1 and "AYASEE_EXTRA_SECRET" in proc.stderr


def test_pull_request_runs_never_sync_even_with_a_token(tmp_path):
    origin = origin_with_commit(tmp_path)
    a = clone(tmp_path, origin, "a")
    (a / "s").mkdir()
    (a / "s" / "x.tfstate").write_text("{}")
    proc = run(
        a,
        "push",
        str(a / "s"),
        "x.tfstate",
        GITHUB_ACTIONS="true",
        GITHUB_EVENT_NAME="pull_request",
        AYASEE_EXTRA_SECRET="tok",
    )
    assert proc.returncode == 0 and "no state sync" in proc.stderr
    heads = subprocess.run(
        ["git", "ls-remote", "--heads", str(origin)], capture_output=True, text=True
    ).stdout
    assert "ayasee-state" not in heads


def test_local_run_without_a_token_just_skips(tmp_path):
    origin = origin_with_commit(tmp_path)
    a = clone(tmp_path, origin, "a")
    proc = run(a, "pull", str(a / "s"))
    assert proc.returncode == 0 and "skipped" in proc.stderr


def test_the_credential_is_not_put_on_a_command_line():
    text = SCRIPT.read_text()
    assert "git -c" not in text and "GIT_CONFIG_COUNT" in text


def _pushed(tmp_path):
    origin = origin_with_commit(tmp_path)
    a, b = clone(tmp_path, origin, "a"), clone(tmp_path, origin, "b")
    state = a / ".ayasee-e2e-state"
    state.mkdir()
    (state / "base.tfstate").write_text('{"serial": 1}')
    assert run(a, "push", str(state), "base.tfstate", AYASEE_EXTRA_SECRET="tok").returncode == 0
    return b


def test_pull_with_nothing_saved_for_this_path_starts_empty(tmp_path):
    b = _pushed(tmp_path)
    other = b / "elsewhere"
    proc = run(b, "pull", str(other), AYASEE_EXTRA_SECRET="tok")
    assert proc.returncode == 0, proc.stderr


def test_a_failed_restore_fails(tmp_path):
    """M6: a saved state that cannot be extracted must fail the pull, not start empty."""
    b = _pushed(tmp_path)
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "tar").write_text("#!/bin/sh\nexit 2\n")
    (shim / "tar").chmod(0o755)
    proc = run(
        b,
        "pull",
        str(b / ".ayasee-e2e-state"),
        AYASEE_EXTRA_SECRET="tok",
        PATH=f"{shim}:{os.environ['PATH']}",
    )
    assert proc.returncode == 1 and "restoring" in proc.stderr


def test_pull_and_push_work_from_a_subdirectory_like_terragrunt_hooks(tmp_path):
    """Terragrunt runs hooks inside stages/<env>/<unit>/.terragrunt-cache/...; git paths must not
    be resolved against that directory."""
    b = _pushed(tmp_path)
    hook_cwd = b / "stages" / "dev" / "base" / ".terragrunt-cache" / "x"
    hook_cwd.mkdir(parents=True)
    state = b / ".ayasee-e2e-state"
    proc = run(hook_cwd, "pull", str(state), AYASEE_EXTRA_SECRET="tok")
    assert proc.returncode == 0, proc.stderr
    assert (state / "base.tfstate").read_text() == '{"serial": 1}'
    (state / "base.tfstate").write_text('{"serial": 2}')
    proc = run(hook_cwd, "push", str(state), "base.tfstate", AYASEE_EXTRA_SECRET="tok")
    assert proc.returncode == 0, proc.stderr
    (state / "base.tfstate").unlink()
    assert run(hook_cwd, "pull", str(state), AYASEE_EXTRA_SECRET="tok").returncode == 0
    assert (state / "base.tfstate").read_text() == '{"serial": 2}'
