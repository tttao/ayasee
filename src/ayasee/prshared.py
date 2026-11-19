"""``ayasee pr-shared``: the pull-request plan of the shared prod units with
``gate_mechanism: workflow`` (D15, issue #40).

With the environment gate, ``iac-pr`` plans the shared prod units of a pull request in the
``prod-pr-plan`` environment, and GitHub holds the job until a prod reviewer approves it: the
reviewer stands between the pull request's code and the read-only prod identity. Without
environment reviewers, a job of a pull-request run cannot be made to wait: the pull request
controls the workflow file of its own run, so any check ayasee put in it could be removed, and an
environment that accepts pull-request branches hands its credentials to whichever job names it.

So in this mode the plan never runs in the pull request's run. A prod reviewer starts the
``iac-pr-shared`` workflow **on main** with the pull request number and its head SHA. The
workflow file is main's, ``prod-pr-plan`` deploys from ``main`` only (GitHub enforces the branch
policy), and ayasee checks, before any secret is used:

``authorize`` (the ``verify`` job: no environment, no secret, ``github.token`` only)
    the run is a ``workflow_dispatch`` on ``refs/heads/main``, started and not re-run by one
    person; the configuration of ``main`` (never of the pull request) has
    ``gate_mechanism: workflow``, sets ``shared_units`` and lists that person in
    ``reviewers.prod``; GitHub gives them write access; the pull request is open, comes from this
    repository (never a fork), targets ``main`` and its head is exactly the SHA given (a push
    after the authorisation needs a new one); the person did not open it nor write or commit any
    of its commits (unless ``solo_mode``); and ``prod-pr-plan`` deploys from ``main`` only.
``comment`` (the ``comment`` job: no pull-request code, ``pull-requests: write``)
    posts the plan text with a header naming the SHA planned and the approver, and says so when
    the pull request has moved on since.

The plan job then checks out exactly that SHA and plans the changed shared units read-only, as
``plan-shared`` of ``iac-pr`` does. On GitHub Free a private repository has no deployment branch
policies and no environment secrets or variables: ``prod-pr-plan`` would accept every branch and
hand out the repository's own credentials, the prod apply's. ``authorize`` refuses that.

Every check fails closed. Nothing here reads or prints a plan value: SHAs, logins, numbers and
URLs only.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from ayasee import cli
from ayasee.approve import (
    MAIN_REF,
    SHA_RE,
    Refused,
    _fold,
    _load_quiet,
    _login,
    _report,
    check_independent,
    check_permission,
)
from ayasee.config import GITHUB_LOGIN, Config
from ayasee.github import GitHub, GitHubError
from ayasee.maintip import _repo
from ayasee.plansupport import reviewer_count
from ayasee.prcomment import upsert

PR_RE = re.compile(r"^[1-9][0-9]{0,8}$")
MAX_PR_COMMITS = 250  # the pull request commits API lists no more than this
_URL_RE = re.compile(r"^https://[^\s`<>()\[\]]+$")
ENVIRONMENT = "prod-pr-plan"


# ---- authorize ---------------------------------------------------------------------------------


def check_request(
    *, pr: str, sha: str, ref: str, event: str, actor: str, triggering_actor: str
) -> int:
    """The dispatch itself: on main, by one person, with well-formed inputs. Returns the PR."""
    if event != "workflow_dispatch":
        raise Refused(f"iac-pr-shared runs on workflow_dispatch only, not {event or 'no event'}")
    if ref != MAIN_REF:
        raise Refused(f"iac-pr-shared runs on {MAIN_REF} only, not {ref or 'no ref'}")
    if not PR_RE.fullmatch(pr):
        raise Refused("pr must be a pull request number")
    if not SHA_RE.fullmatch(sha):
        raise Refused("sha must be the full 40-character lowercase head SHA of the pull request")
    if not actor or not triggering_actor:
        raise Refused("the run has no actor")
    if actor.casefold() != triggering_actor.casefold():
        # a re-run keeps github.actor but is started by triggering_actor: an authorisation is
        # one person's act, so it is never replayed by someone else
        raise Refused(
            f"this run was started by {actor} and re-run by {triggering_actor}: an authorisation "
            "is not re-run by another person; start iac-pr-shared again"
        )
    return int(pr)


def check_settings(cfg: Config, actor: str) -> None:
    """The configuration of main holds the workflow gate, shared units and ``actor``."""
    if cfg.gate_mechanism != "workflow":
        raise Refused(
            "the configuration of main has gate_mechanism: environment: iac-pr plans the shared "
            "units in the prod-pr-plan environment, behind its GitHub reviewers"
        )
    if not cfg.shared_units:
        raise Refused("the configuration of main has no shared_units: nothing to plan")
    if actor.casefold() not in _fold(cfg.reviewers.prod):
        raise Refused(f"{actor} is not in reviewers.prod of the configuration on main")


def check_pull_request(gh: GitHub, repo: str, pr: int, sha: str) -> dict[str, Any]:
    """Open, from this repository, into main, and its head is ``sha``."""
    try:
        data = gh.pull_request(repo, pr)
    except GitHubError as exc:
        raise Refused(f"cannot read pull request #{pr}: {exc}") from None
    base = data.get("base") if isinstance(data.get("base"), dict) else {}
    head = data.get("head") if isinstance(data.get("head"), dict) else {}

    def repo_of(part: dict[str, Any]) -> str | None:
        r = part.get("repo")
        name = r.get("full_name") if isinstance(r, dict) else None
        return name if isinstance(name, str) else None

    if data.get("state") != "open":
        raise Refused(f"pull request #{pr} is {data.get('state') or 'not open'}")
    if repo_of(base) != repo:
        raise Refused(f"#{pr} is not a pull request of {repo}")
    if base.get("ref") != "main":
        raise Refused(f"pull request #{pr} targets {base.get('ref')}, not main")
    if repo_of(head) != repo:
        # forks get no plan with a cloud identity, as in iac-pr
        raise Refused(f"pull request #{pr} comes from a fork: its code is not planned with prod")
    current = head.get("sha")
    if current != sha:
        shown = current[:12] if isinstance(current, str) else "unknown"
        raise Refused(
            f"the head of pull request #{pr} is now {shown}, not {sha[:12]}: read the new "
            "changes and authorise the new head"
        )
    return data


def pr_people(gh: GitHub, repo: str, pr: int, data: dict[str, Any]) -> tuple[set[str], int]:
    """Who made the pull request: its author and the author and committer of each of its
    commits. Returns them and the number of commits with neither login."""
    people = {x for x in (_login(data.get("user")),) if x}
    try:
        commits = gh.pull_request_commits(repo, pr)
    except GitHubError as exc:
        raise Refused(f"cannot read the commits of pull request #{pr}: {exc}") from None
    if len(commits) >= MAX_PR_COMMITS:
        raise Refused(
            f"pull request #{pr} has {MAX_PR_COMMITS} commits or more: GitHub does not list them "
            "all, so who wrote them cannot be checked"
        )
    unknown = 0
    for c in commits:
        logins = [x for x in (_login(c.get("author")), _login(c.get("committer"))) if x]
        if not logins:
            unknown += 1
        people.update(logins)
    return people, unknown


def check_environment(gh: GitHub, repo: str, env: str) -> list[str]:
    """``env`` deploys from main only. Unlike the prod gate, an environment open to every branch
    is refused on every plan, GitHub Free included: there its credentials would be the
    repository's own. Returns notes."""
    try:
        data = gh.environment(repo, env)
    except GitHubError as exc:
        if exc.status == 404:
            raise Refused(
                f"the environment {env} does not exist; create it with shared_units_pr_plan in "
                "the bootstrap"
            ) from None
        raise Refused(f"cannot read the environment {env}: {exc}") from None
    notes: list[str] = []
    if reviewer_count(data):
        notes.append(f"{env} also has required reviewers: the plan waits for them as well")
    policy = data.get("deployment_branch_policy")
    if isinstance(policy, dict) and policy.get("custom_branch_policies") is True:
        try:
            names = sorted(str(p.get("name")) for p in gh.environment_branch_policies(repo, env))
        except GitHubError as exc:
            raise Refused(f"cannot read the branch policies of {env}: {exc}") from None
        if names != ["main"]:
            raise Refused(f"{env} deploys from {names or 'no branch'}, expected [main]")
        return notes
    if policy:
        raise Refused(f"{env} is restricted to protected branches, not to main; run the bootstrap")
    raise Refused(
        f"{env} accepts every branch: a workflow of any branch could use its credentials without "
        "this check. Restrict it to main (the bootstrap does, on GitHub Pro, Team or Enterprise). "
        "A private repository on GitHub Free has no deployment branch policies and no environment "
        "secrets, so the pull-request plan of shared units is not available there (D15)"
    )


def authorize(
    gh: GitHub,
    *,
    repo: str,
    pr: str,
    sha: str,
    ref: str,
    event: str,
    actor: str,
    triggering_actor: str,
    cfg: Config,
    environment: str,
) -> list[str]:
    """Every check of the ``verify`` job, in order; raises :class:`Refused`. Returns notes."""
    number = check_request(
        pr=pr, sha=sha, ref=ref, event=event, actor=actor, triggering_actor=triggering_actor
    )
    check_settings(cfg, actor)  # before any API call
    check_permission(gh, repo, actor)
    data = check_pull_request(gh, repo, number, sha)
    notes: list[str] = []
    if cfg.solo_mode:
        notes.append(
            "NON-COMPLIANT: solo_mode lets the author authorise the plan of their own pull "
            "request (D2)"
        )
    else:
        people, unknown = pr_people(gh, repo, number, data)
        check_independent(actor, people, "opened or wrote it")
        if unknown:
            notes.append(
                f"{unknown} commit(s) of pull request #{number} have no GitHub login: their "
                "author is not checked for independence"
            )
    notes.extend(check_environment(gh, repo, environment))
    return notes


# ---- comment -----------------------------------------------------------------------------------


def comment_text(
    body: str, *, pr: int, sha: str, approver: str, run_url: str, head: str | None
) -> str:
    """The sticky comment: a header of identifiers written here, then the plan job's text."""
    lines = [
        f"Planned at `{sha[:12]}`, authorised by {approver} ([iac-pr-shared run]({run_url})).",
    ]
    if head is None:
        lines.append(
            f"**ayasee could not confirm the head of #{pr}: check that it is still `{sha[:12]}`.**"
        )
    elif head != sha:
        shown = head[:12] if SHA_RE.fullmatch(head) else "unknown"
        lines.append(
            f"**The pull request has moved on since: its head is now `{shown}`. This plan is of "
            f"`{sha[:12]}`; a prod reviewer authorises the new head with a new iac-pr-shared run.**"
        )
    return "\n\n".join(lines) + "\n\n" + body


def _head(gh: GitHub, repo: str, pr: int) -> str | None:
    try:
        head = gh.pull_request(repo, pr).get("head")
    except GitHubError:
        return None
    sha = head.get("sha") if isinstance(head, dict) else None
    return sha if isinstance(sha, str) else None


# ---- CLI ---------------------------------------------------------------------------------------


def _authorize(args: argparse.Namespace) -> int:
    gh: GitHub | None = None
    try:
        cfg = _load_quiet(args.config_path)
        environment = cfg.environment_prefix + ENVIRONMENT
        gh = GitHub.from_env()
        notes = authorize(
            gh,
            repo=args.repo,
            pr=args.pr,
            sha=args.sha,
            ref=args.ref,
            event=args.event,
            actor=args.actor,
            triggering_actor=args.triggering_actor,
            cfg=cfg,
            environment=environment,
        )
    except (Refused, GitHubError) as exc:
        print(f"pr-shared: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # unexpected data: fail closed, never a traceback
        print(f"pr-shared: unexpected {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        if gh is not None:
            gh.close()
    _report(notes)
    print(f"approver={args.actor}")
    print(f"environment={environment}")
    return 0


def _comment(args: argparse.Namespace) -> int:
    gh: GitHub | None = None
    try:
        if not PR_RE.fullmatch(args.pr) or not SHA_RE.fullmatch(args.sha):
            raise Refused("pr or sha is malformed")
        if not GITHUB_LOGIN.fullmatch(args.approver):
            raise Refused("approver must be a GitHub login")
        if not _URL_RE.fullmatch(args.run_url):
            raise Refused("run-url must be an https URL")
        pr = int(args.pr)
        body = Path(args.body_file).read_text()
        gh = GitHub.from_env()
        text = comment_text(
            body,
            pr=pr,
            sha=args.sha,
            approver=args.approver,
            run_url=args.run_url,
            head=_head(gh, args.repo, pr),
        )
        done = upsert(gh, args.repo, pr, text, "pr-plan-shared")
    except (Refused, GitHubError, OSError, UnicodeDecodeError) as exc:
        print(f"pr-shared: {exc}", file=sys.stderr)
        return 1
    finally:
        if gh is not None:
            gh.close()
    print(f"pr-shared: comment {done} on #{pr}")
    return 0


def _setup(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="pr_shared_command", metavar="<action>", required=True)
    p = sub.add_parser("authorize", help="the checks of the verify job (no secret needed)")
    p.add_argument("--repo", required=True, type=_repo)
    p.add_argument("--pr", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--ref", required=True)
    p.add_argument("--event", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--triggering-actor", required=True)
    p.add_argument("--config-path", required=True, help="the configuration of main")
    p.set_defaults(handler=_authorize)
    p = sub.add_parser("comment", help="post the plan of the authorised head as a sticky comment")
    p.add_argument("--repo", required=True, type=_repo)
    p.add_argument("--pr", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--approver", required=True)
    p.add_argument("--run-url", required=True)
    p.add_argument("--body-file", required=True)
    p.set_defaults(handler=_comment)


cli.register("pr-shared", _setup)
