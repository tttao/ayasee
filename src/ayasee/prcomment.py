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
# one sticky comment per kind: the dev plan, and the plan of the shared prod units (#5), which may
# wait for the prod-pr-plan approval and must not hold back the dev plan's comment
MARKERS = {"pr-plan": MARKER, "pr-plan-shared": "<!-- ayasee:pr-plan-shared -->"}
MAX_BODY = 65536  # GitHub's comment size limit
_CUT = "\n\n_(summary truncated)_\n"


def _compose(text: str, marker: str = MARKER) -> str:
    body = f"{marker}\n{text}"
    if len(body) > MAX_BODY:
        body = body[: MAX_BODY - len(_CUT)] + _CUT
    return body


def _run(args: argparse.Namespace) -> int:
    gh: GitHub | None = None
    try:
        marker = MARKERS[args.marker]
        body = _compose(Path(args.body_file).read_text(), marker)
        gh = GitHub.from_env()
        existing = next(
            (
                c
                for c in gh.list_issue_comments(args.repo, args.pr)
                if str(c.get("body", "")).startswith(marker + "\n")
                and (c.get("user") or {}).get("type") == "Bot"
                and (c.get("user") or {}).get("login") == args.author
                and isinstance(c.get("id"), int)
            ),
            None,
        )
        if existing is None:
            gh.create_issue_comment(args.repo, args.pr, body)
            print(f"pr-comment: created on #{args.pr}")
        else:
            gh.update_issue_comment(args.repo, existing["id"], body)
            print(f"pr-comment: updated on #{args.pr}")
    except (GitHubError, OSError, UnicodeDecodeError) as exc:
        print(f"pr-comment: error: {exc}", file=sys.stderr)
        return 1
    finally:
        if gh is not None:
            gh.close()
    return 0


def _setup(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--body-file", required=True)
    parser.add_argument(
        "--marker",
        choices=sorted(MARKERS),
        default="pr-plan",
        help="which sticky comment to create or update (default: %(default)s)",
    )
    parser.add_argument(
        "--author",
        default="github-actions[bot]",
        help="login of the account whose marker comment is updated (default: %(default)s)",
    )
    parser.set_defaults(handler=_run)


cli.register("pr-comment", _setup)
