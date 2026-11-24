"""``ayasee pr-comment``: keep one plan-summary comment on a pull request.

The comment is found by a hidden marker and updated in place; it is created on the first run.
Only a bot comment by the expected login (``--author``) counts, so a user pasting the
marker cannot capture the upsert.
The body is whatever file the caller passes: the workflow passes the rendered change set
(no resource values), nothing else.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ayasee import cli
from ayasee.github import GitHub, GitHubError

MARKER = "<!-- ayasee:pr-plan -->"
# one sticky comment per kind: the first stage's plan, and the plan of the shared prod units (#5),
# which may wait for the prod-pr-plan approval (or, with the workflow gate, for a prod reviewer
# to start iac-pr-shared) and must not hold back the other
MARKERS = {
    "pr-plan": MARKER,
    "pr-plan-shared": "<!-- ayasee:pr-plan-shared -->",
    # the result of an iac-import run on the pull request's branch (issue #58)
    "import-plan": "<!-- ayasee:import-plan -->",
}
DEFAULT_AUTHOR = "github-actions[bot]"
MAX_BODY = 65536  # GitHub's comment size limit
_CUT = "\n\n_(summary truncated)_\n"


def _compose(text: str, marker: str = MARKER) -> str:
    body = f"{marker}\n{text}"
    if len(body) > MAX_BODY:
        body = body[: MAX_BODY - len(_CUT)] + _CUT
    return body


def upsert(
    gh: GitHub, repo: str, pr: int, text: str, marker_key: str, author: str = DEFAULT_AUTHOR
) -> str:
    """Create or update the sticky comment ``marker_key`` on ``pr``; returns what was done."""
    marker = MARKERS[marker_key]
    body = _compose(text, marker)
    existing = next(
        (
            c
            for c in gh.list_issue_comments(repo, pr)
            if str(c.get("body", "")).startswith(marker + "\n")
            and (c.get("user") or {}).get("type") == "Bot"
            and (c.get("user") or {}).get("login") == author
            and isinstance(c.get("id"), int)
        ),
        None,
    )
    if existing is None:
        gh.create_issue_comment(repo, pr, body)
        return "created"
    gh.update_issue_comment(repo, existing["id"], body)
    return "updated"


def _run(args: argparse.Namespace) -> int:
    gh: GitHub | None = None
    try:
        text = Path(args.body_file).read_text()
        gh = GitHub.from_env()
        if args.pr is not None:
            numbers = [args.pr]
        else:
            # the open pull requests whose head is this branch of this repository, never a fork's
            numbers = gh.open_pulls_from_branch(args.repo, args.head_branch)
            if not numbers:
                print(f"pr-comment: no open pull request from {args.head_branch}; nothing posted")
        for number in numbers:
            done = upsert(gh, args.repo, number, text, args.marker, args.author)
            print(f"pr-comment: {done} on #{number}")
    except (GitHubError, OSError, UnicodeDecodeError) as exc:
        print(f"pr-comment: error: {exc}", file=sys.stderr)
        return 1
    finally:
        if gh is not None:
            gh.close()
    return 0


def _setup(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", required=True, help="owner/name")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", type=int)
    target.add_argument(
        "--head-branch",
        help="instead of --pr: every open pull request whose head is this branch of --repo",
    )
    parser.add_argument("--body-file", required=True)
    parser.add_argument(
        "--marker",
        choices=sorted(MARKERS),
        default="pr-plan",
        help="which sticky comment to create or update (default: %(default)s)",
    )
    parser.add_argument(
        "--author",
        default=DEFAULT_AUTHOR,
        help="login of the account whose marker comment is updated (default: %(default)s)",
    )
    parser.set_defaults(handler=_run)


cli.register("pr-comment", _setup)
