"""Run the end-to-end scenarios against a sandbox repository.

    python -m e2e.run --all
    python -m e2e.run --scenario supersession

Environment: E2E_REPO (owner/name; the name must end in ``-sandbox``), E2E_TOKEN (maintainer),
E2E_APPROVER_TOKEN (second identity), E2E_WEBHOOK_URL (webhook echo, optional for scenarios
that do not read cards). The suffix check is a safety guard: scenarios push to main and merge.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import time
import traceback
from collections.abc import Callable, Mapping, Sequence

from ayasee.github import DEFAULT_API_URL, GitHub, GitHubError
from e2e.sandbox import NotASandbox, Sandbox
from e2e.sandbox import check_repo as sandbox_check_repo
from e2e.scenarios import SCENARIOS


class RunnerError(Exception):
    """Bad configuration: nothing was run."""


def check_repo(repo: str) -> str:
    try:
        return sandbox_check_repo(repo)
    except NotASandbox as exc:
        raise RunnerError(str(exc)) from None


def _login(gh: GitHub) -> str | None:
    """The user behind a token; None for a GitHub App installation token (no /user for bots)."""
    try:
        return gh.current_login()
    except GitHubError as exc:
        if exc.status == 401:
            raise RunnerError(
                "a token was rejected by GitHub (HTTP 401): expired or revoked"
            ) from None
        if exc.status == 403:
            return None
        raise RunnerError(f"cannot identify a token ({exc})") from None


def build_sandbox(env: Mapping[str, str]) -> Sandbox:
    repo = check_repo(env.get("E2E_REPO", ""))
    token, approver = env.get("E2E_TOKEN"), env.get("E2E_APPROVER_TOKEN")
    if not token or not approver:
        raise RunnerError("E2E_TOKEN and E2E_APPROVER_TOKEN must be set")
    if token == approver:
        raise RunnerError("E2E_TOKEN and E2E_APPROVER_TOKEN must be different identities")
    webhook = env.get("E2E_WEBHOOK_URL") or None
    if webhook is not None and not webhook.startswith("https://"):
        raise RunnerError("E2E_WEBHOOK_URL must be an https URL")
    api = env.get("GITHUB_API_URL") or DEFAULT_API_URL
    gh, approver_gh = GitHub(api, token), GitHub(api, approver)
    mine, theirs = _login(gh), _login(approver_gh)
    if mine is not None and mine == theirs:
        raise RunnerError(
            f"both tokens belong to {mine}; approvals must come from a second identity"
        )
    return Sandbox(gh, approver_gh, repo, webhook)


def load(name: str) -> Callable[[Sandbox], None]:
    if name not in SCENARIOS:
        raise RunnerError(f"unknown scenario {name!r}; choose from {', '.join(SCENARIOS)}")
    return importlib.import_module(f"e2e.scenarios.{name}").run


def run_scenarios(sandbox: Sandbox, names: Sequence[str]) -> dict[str, str | None]:
    """Run each scenario; the value is None for a pass, else the failure text. Issues left by
    one scenario are closed first so they cannot stop the next."""
    results: dict[str, str | None] = {}
    for name in names:
        scenario = load(name)
        started = time.monotonic()
        try:
            sandbox.cleanup()
            scenario(sandbox)
            results[name] = None
            print(f"PASS {name} ({time.monotonic() - started:.0f}s)")
        except Exception as exc:  # a scenario failing must not hide the others
            results[name] = f"{type(exc).__name__}: {exc}"
            print(f"FAIL {name}: {results[name]}")
            if not isinstance(exc, AssertionError | GitHubError):
                traceback.print_exc()
    return results


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m e2e.run")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="run every scenario in order")
    group.add_argument("--scenario", metavar="NAME", help="run one scenario")
    args = parser.parse_args(argv)
    names = list(SCENARIOS) if args.all else [args.scenario]
    try:
        for name in names:
            load(name)
        sandbox = build_sandbox(os.environ if env is None else env)
    except RunnerError as exc:
        print(f"e2e: {exc}", file=sys.stderr)
        return 2
    results = run_scenarios(sandbox, names)
    failed = [n for n, r in results.items() if r is not None]
    print(f"{len(results) - len(failed)}/{len(results)} scenarios passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
