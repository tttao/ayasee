"""``ayasee trunk-guard``: is ``main`` behind a pull-request rule the ledger token cannot bypass?

A ``git-branch://`` ledger store needs a token with contents write (``ledger-token``), and such a
token can push to any branch, ``main`` included, unless a ruleset stops it (issue #11). Run with
that token as ``GITHUB_TOKEN``: both endpoints need only Metadata read, which every fine-grained
token has, and ``current_user_can_bypass`` then describes the ledger token itself. The workflow's
own ``github.token`` could read the rules but not who may bypass them (``bypass_actors`` needs
write on the ruleset), so it cannot answer the question.

Exit 0 only when an active ruleset with a ``pull_request`` rule applies to ``main`` and the token
can ``never`` bypass it. Everything else, an API error and an unknown value included, exits 1 with
the reasons on stderr: not verified is never reported as protected. Classic branch protection is
not visible here (it needs Administration read), so it reads as "no pull_request rule"."""

from __future__ import annotations

import argparse
import sys

from ayasee import cli
from ayasee.github import GitHub, GitHubError
from ayasee.maintip import _repo

TRUNK = "main"
_BYPASS = ("always", "pull_requests_only", "exempt", "never")


def check(gh: GitHub, repo: str) -> list[str]:
    """The reasons ``main`` is not protected against the calling token; empty when it is.
    Raises :class:`GitHubError` when the API fails or a pull_request rule has no ruleset id."""
    ids: list[int] = []
    for rule in gh.branch_rules(repo, TRUNK):
        if rule.get("type") != "pull_request":
            continue
        rid = rule.get("ruleset_id")
        if not isinstance(rid, int) or isinstance(rid, bool):
            raise GitHubError(0, "unexpected branch rules response (ruleset_id)")
        if rid not in ids:
            ids.append(rid)
    if not ids:
        return [
            f"no active pull_request rule on {TRUNK}: the ledger token can push to it "
            "(classic branch protection is not visible to this check; use a ruleset)"
        ]
    problems: list[str] = []
    for rid in ids:
        ruleset = gh.ruleset(repo, rid)
        bypass = ruleset.get("current_user_can_bypass")
        if ruleset.get("enforcement") != "active":
            problems.append(f"ruleset {rid}: enforcement is not active")
        elif bypass not in _BYPASS:
            problems.append(f"ruleset {rid}: cannot tell whether the ledger token can bypass it")
        elif bypass != "never":
            problems.append(f"ruleset {rid}: the ledger token can bypass it ({bypass})")
        else:
            return []  # this ruleset alone already forces a pull request on the token
    return problems


def _run(args: argparse.Namespace) -> int:
    gh: GitHub | None = None
    try:
        gh = GitHub.from_env()
        problems = check(gh, args.repo)
    except GitHubError as exc:
        problems = [f"cannot tell whether {TRUNK} is protected: {exc}"]
    finally:
        if gh is not None:
            gh.close()
    for line in problems:
        print(f"trunk-guard: {line}", file=sys.stderr)
    return 1 if problems else 0


def _setup(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", required=True, type=_repo, help="owner/name")
    parser.set_defaults(handler=_run)


cli.register("trunk-guard", _setup)
