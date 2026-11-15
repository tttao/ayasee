"""``ayasee require-reviewers``: refuse to run behind an environment that would not wait (#39).

``prod`` and ``prod-breakglass`` are gated by the required reviewers of their GitHub environment. An
environment without them, one that does not exist yet (GitHub creates it, unprotected, on first
use), or one whose reviewers the repository's plan ignores, would let the job run without an
approval. The ``prod-gate`` job of ``iac-staged`` runs this before ``prod-plan`` produces a plan
for ``prod``, and the ``validate`` job of ``iac-breakglass`` before the break-glass job, both with
the workflow's ``github.token`` holding ``actions: read`` (the environment and its protection
rules) and the Metadata read every token has (the repository and its rulesets).

``solo_mode`` does not change this: it lets the one maintainer approve their own change (D2), but
the reviewers are still required (``modules/github-environments``), so zero reviewers is never a
solo setup. Whether self-review is prevented is ``ayasee doctor``'s check, not a refusal here.

Exit 0 when the environment has at least one required reviewer and the plan is not known to lack
them; exit 1 otherwise, with the reasons on stderr. An API error is a refusal. When the plan cannot
be confirmed (the run's token cannot read an organization's plan), the reviewers GitHub reports
stand and a note is printed on stdout."""

from __future__ import annotations

import argparse
import sys

from ayasee import cli
from ayasee.github import GitHub, GitHubError
from ayasee.maintip import _repo
from ayasee.plansupport import REVIEWERS, UNKNOWN, UNSUPPORTED, detect, reviewer_count


def check(gh: GitHub, repo: str, env: str) -> tuple[list[str], list[str]]:
    """``(problems, notes)`` for the gated environment ``env`` (its GitHub name, prefix
    included). No problem means the job behind ``env`` waits for an approval."""
    try:
        data = gh.environment(repo, env)
    except GitHubError as exc:
        if exc.status == 404:
            return [
                f"the environment {env} does not exist: GitHub would create it on first use "
                "with no required reviewers; run the bootstrap"
            ], []
        return [f"cannot read the environment {env}: {exc}"], []
    if reviewer_count(data) == 0:
        return [
            f"{env} has no required reviewers: its job would run without an approval. Set them "
            "with the bootstrap (modules/github-environments); a private repository has them on "
            "GitHub Enterprise only. Check with `ayasee doctor`"
        ], []
    support = detect(gh, repo)
    if support.verdict == UNSUPPORTED and REVIEWERS in support.missing:
        return [f"{env} lists reviewers, but " + " ".join(support.explain())], []
    if support.verdict == UNKNOWN:
        return [], [f"{env} has required reviewers; " + support.explain()[0]]
    return [], []


def _run(args: argparse.Namespace) -> int:
    gh: GitHub | None = None
    notes: list[str] = []
    try:
        gh = GitHub.from_env()
        problems, notes = check(gh, args.repo, args.environment)
    except GitHubError as exc:
        problems = [f"cannot check the reviewers of {args.environment}: {exc}"]
    finally:
        if gh is not None:
            gh.close()
    for line in notes:
        print(line)
    for line in problems:
        print(f"require-reviewers: {line}", file=sys.stderr)
    return 1 if problems else 0


def _setup(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", required=True, type=_repo, help="owner/name")
    parser.add_argument(
        "--environment", required=True, help="GitHub name of the gated environment (prefixed)"
    )
    parser.set_defaults(handler=_run)


cli.register("require-reviewers", _setup)
