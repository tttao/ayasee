"""``ayasee approve``: the prod gate of ``gate_mechanism: workflow``, enforced by ayasee (D15).

With the environment gate (the default), GitHub pauses the ``prod`` job until a required
reviewer of the ``prod`` environment approves it. A private repository below GitHub Enterprise
has no such reviewers (issue #39), so with ``gate_mechanism: workflow`` the staged run stops after
``prod-plan`` and opens an approval issue (``approve issue``). A reviewer then starts the
``iac-approve`` workflow on ``main`` with the commit SHA and the digest of the plan manifest, and
ayasee checks, before any secret is used:

``authorize`` (the ``verify`` job: no environment, no secret, ``github.token`` only)
    the run is a ``workflow_dispatch`` on ``refs/heads/main``, started (and not re-run) by one
    person; the configuration of ``main`` (never of the commit to approve) has
    ``gate_mechanism: workflow`` and lists that person in ``reviewers.prod``; GitHub gives them
    write access today; the SHA is an ancestor of ``main``; the newest unexpired prod plan of that
    SHA comes from a successful ``iac-staged`` run on ``main``; the person did not push the
    change, author the commit, or open or merge its pull request (unless ``solo_mode``); and the
    ``prod`` environment either deploys from ``main`` only or sits on a plan that has no branch
    policies at all (GitHub Free), which is said as a deviation.
``digest``
    the downloaded manifest is byte for byte the one the reviewer named, and binds that SHA.
``baseline`` (the ``apply`` job, after the guard, before the apply step)
    against the ledger: the SHA is not already live in prod; the person was already a prod
    reviewer at the last *approved* prod apply (never at a commit that went live in sync, so a
    change cannot add its own approver; before the first approval, a bootstrap-trust DEVIATION);
    and no commit since that one is theirs (unless ``solo_mode`` at both commits).
``gate-change`` (``prod-plan`` of ``iac-staged``)
    whether the commit changes ``gate_mechanism``, ``reviewers.prod``, ``reviewers.breakglass``
    or ``solo_mode`` against the last approved apply, with the workflow gate on either side: such
    a commit needs an approval even with an empty plan, so the in-sync path never changes who may
    approve.

Then the apply step applies exactly the bound plan (D1) and the ledger records the approver.

**Break-glass** (D3 in this mode, issue #40) uses the same checks with its own role: the
``iac-breakglass`` run of a requester plans prod in ``prod-plan`` and keeps the encrypted plan
as ``ayasee-breakglass-plan-<sha>`` with a ``request.json`` (incident, SHA, allow-rollback,
counts), and ``breakglass-issue`` opens the approval issue. ``iac-approve`` started with an
``incident`` is then a break-glass approval: the approver is in ``reviewers.breakglass`` (at main
and at the last approved apply), is not the requester (the request run's actor or triggering
actor) and did not write or merge the change (unless ``solo_mode``); the plan is the newest of a
successful ``iac-breakglass`` dispatch on main, bound by its digest; ``request`` checks the
incident and the SHA against ``request.json``; and ``baseline --role breakglass`` refuses a plan
made before the prod ledger last changed. The guard is not run (stop-the-line is not enforced on
the emergency path), as with the environment gate.
Every check fails closed: an API error, an unexpected answer or a missing input is a refusal.
Nothing here reads or prints a plan value: SHAs, digests, logins, counts and URLs only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from ayasee import cli
from ayasee.binding import Manifest
from ayasee.config import DEVIATION_GATE, Config, ConfigError, parse_config_text
from ayasee.github import GitHub, GitHubError
from ayasee.issues import INCIDENT_RE
from ayasee.labels import BREAKGLASS_APPROVAL_LABEL, PROD_APPROVAL_LABEL
from ayasee.ledger import LedgerError, LedgerRecord, LedgerStore, open_store
from ayasee.maintip import _repo
from ayasee.plansupport import BRANCH_POLICIES, UNSUPPORTED, detect, reviewer_count

ARTIFACT = "ayasee-prod-plan"
# a break-glass plan is named after its commit: its run is a dispatch on main, whose head_sha is
# main's tip, not the commit planned
BREAKGLASS_ARTIFACT = "ayasee-breakglass-plan-"
REQUEST_SCHEMA = "ayasee.breakglass-request/v1"
MAIN_REF = "refs/heads/main"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_STAGED = re.compile(r"(?:^|/)\.github/workflows/iac-staged\.ya?ml@")
_BREAKGLASS = re.compile(r"(?:^|/)\.github/workflows/iac-breakglass\.ya?ml@")
_URL_RE = re.compile(r"^https://[^\s`<>()\[\]]+$")
_LOGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
WRITE = frozenset({"admin", "write"})
PLAN_EVENTS = frozenset({"push", "workflow_dispatch"})
COUNTS = ("critical", "high", "medium", "total")

# prod: a prod plan of iac-staged; breakglass: a break-glass request of iac-breakglass
Role = Literal["prod", "breakglass"]


class Refused(Exception):
    """A check refused the approval; the message is ours and holds no value."""


@dataclass(frozen=True)
class PlanRun:
    """The ``iac-staged`` run whose ``prod-plan`` produced the plan to approve."""

    run_id: int
    artifact_id: int
    pushers: tuple[str, ...]  # the run's actor and triggering actor: who pushed or re-ran it
    started_at: str = ""  # when the latest attempt of that run started (RFC 3339, GitHub's clock)


def _fold(logins: Iterable[str]) -> set[str]:
    return {x.casefold() for x in logins if x}


def _login(obj: Any) -> str | None:
    login = obj.get("login") if isinstance(obj, dict) else None
    return login if isinstance(login, str) and login else None


# ---- authorize ---------------------------------------------------------------------------------


def check_request(
    *,
    sha: str,
    digest: str,
    ref: str,
    event: str,
    actor: str,
    triggering_actor: str,
    incident: str = "",
) -> None:
    """The dispatch itself: on main, by one person, with well-formed inputs."""
    if event != "workflow_dispatch":
        raise Refused(f"iac-approve runs on workflow_dispatch only, not {event or 'no event'}")
    if ref != MAIN_REF:
        raise Refused(f"iac-approve runs on {MAIN_REF} only, not {ref or 'no ref'}")
    if not SHA_RE.fullmatch(sha):
        raise Refused("sha must be a full 40-character lowercase commit SHA")
    if not DIGEST_RE.fullmatch(digest):
        raise Refused("manifest-digest must be the 64-character SHA-256 of the approval issue")
    if incident and not INCIDENT_RE.fullmatch(incident):
        raise Refused("incident must look like ABC-123 or INC123")
    if not actor or not triggering_actor:
        raise Refused("the run has no actor")
    if actor.casefold() != triggering_actor.casefold():
        # a re-run keeps github.actor (who chose the inputs) but is started by triggering_actor:
        # an approval is one person's act, so it is never replayed by someone else
        raise Refused(
            f"this run was started by {actor} and re-run by {triggering_actor}: an approval is "
            "not re-run by another person; start iac-approve again"
        )


def reviewers_of(cfg: Config, role: Role) -> list[str]:
    return cfg.reviewers.breakglass if role == "breakglass" else cfg.reviewers.prod


def check_reviewer(cfg: Config, actor: str, role: Role = "prod") -> None:
    """The configuration of main holds the workflow gate and lists ``actor`` for ``role``."""
    if cfg.gate_mechanism != "workflow":
        where = (
            "prod-breakglass environment of iac-breakglass"
            if role == "breakglass"
            else "prod environment of the staged run"
        )
        raise Refused(
            f"the configuration of main has gate_mechanism: environment: {role} is approved on "
            f"the {where}, not with iac-approve"
        )
    names = reviewers_of(cfg, role)
    if not names:
        raise Refused(
            f"reviewers.{role} of the configuration on main is empty: nobody may approve a "
            "break-glass request; add it by a pull request (its first use is then approved)"
        )
    if actor.casefold() not in _fold(names):
        raise Refused(f"{actor} is not in reviewers.{role} of the configuration on main")


def check_ancestor(sha: str, head: str, root: Path) -> None:
    """``sha`` is a commit of main's history (``head`` is the checked-out tip of main)."""

    def git(*args: str) -> int:
        try:
            return subprocess.run(
                ["git", "-C", str(root), *args], capture_output=True, check=False
            ).returncode
        except OSError:
            return 128

    if git("cat-file", "-e", f"{sha}^{{commit}}") != 0:
        raise Refused(f"{sha[:12]} is not a commit of this repository")
    if git("merge-base", "--is-ancestor", sha, head) != 0:
        raise Refused(f"{sha[:12]} is not an ancestor of main")


def check_permission(gh: GitHub, repo: str, actor: str) -> None:
    try:
        perm = gh.collaborator_permission(repo, actor)
    except GitHubError as exc:
        raise Refused(f"cannot read the permission of {actor}: {exc}") from None
    if perm not in WRITE:
        raise Refused(f"{actor} has {perm} access to the repository; a reviewer needs write")


def check_environment(gh: GitHub, repo: str, env: str) -> list[str]:
    """The prod environment deploys from main only, or the plan has no branch policies at all.

    Returns warnings (the deviation of a plan without branch policies)."""
    try:
        data = gh.environment(repo, env)
    except GitHubError as exc:
        if exc.status == 404:
            raise Refused(f"the environment {env} does not exist; run the bootstrap") from None
        raise Refused(f"cannot read the environment {env}: {exc}") from None
    notes: list[str] = []
    if reviewer_count(data):
        notes.append(f"{env} also has required reviewers: the apply waits for them as well")
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
    support = detect(gh, repo)
    if support.verdict == UNSUPPORTED and BRANCH_POLICIES in support.missing:
        notes.append(
            f"DEVIATION: {env} has no deployment branch policy, because this repository's plan "
            "has none: the prod secrets are repository secrets that any collaborator with write "
            "access can read from a workflow of any branch. This gate stops mistakes and "
            "unreviewed applies, not a collaborator who means harm (D15)"
        )
        return notes
    raise Refused(
        f"{env} is not restricted to main: any branch could use its secrets. Run the bootstrap "
        "(a private repository has deployment branch policies on GitHub Pro, Team or Enterprise)"
    )


def artifact_name(role: Role, sha: str) -> str:
    return BREAKGLASS_ARTIFACT + sha if role == "breakglass" else ARTIFACT


def _parse_time(value: Any, what: str) -> datetime:
    """An RFC 3339 UTC time (GitHub's ``...Z`` or the ledger's microseconds); refused otherwise."""
    if isinstance(value, str) and value.endswith("Z"):
        try:
            return datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError:
            pass
    raise Refused(f"{what} has no readable time")


def find_plan(gh: GitHub, repo: str, sha: str, role: Role = "prod") -> PlanRun:
    """The newest unexpired plan of ``sha``: for prod, an ``ayasee-prod-plan`` of a successful
    ``iac-staged`` run of ``sha`` on main; for break-glass, an ``ayasee-breakglass-plan-<sha>``
    of a successful ``iac-breakglass`` dispatch on main. Only the newest counts: a re-planned
    SHA is approved by its new digest."""
    name = artifact_name(role, sha)
    what = "break-glass plan" if role == "breakglass" else "prod plan"
    again = "start iac-breakglass again" if role == "breakglass" else "re-run prod-plan"
    try:
        found = [
            a
            for a in gh.list_artifacts(repo, name)
            if a.get("name") == name
            and isinstance(a.get("workflow_run"), dict)
            and (role == "breakglass" or a["workflow_run"].get("head_sha") == sha)
        ]
    except GitHubError as exc:
        raise Refused(f"cannot list the {what}s: {exc}") from None
    if not found:
        raise Refused(f"no {what} of {sha[:12]} among the newest {name} artifacts: {again}")
    newest = max(found, key=lambda a: (str(a.get("created_at", "")), int(a.get("id") or 0)))
    if newest.get("expired") is not False:
        raise Refused(f"the {what} of {sha[:12]} has expired: {again}")
    art_id, run_id = newest.get("id"), newest["workflow_run"].get("id")
    if not isinstance(art_id, int) or not isinstance(run_id, int):
        raise Refused("unexpected artifact response")
    try:
        run = gh.get_run(repo, run_id)
    except GitHubError as exc:
        raise Refused(f"cannot read run {run_id}: {exc}") from None
    # the start of the attempt that planned: no later than the plan's view of prod's state
    started = run.get("run_started_at")
    _parse_time(started, f"run {run_id}")
    if role == "breakglass":
        # a dispatch runs on main's tip; the commit planned is in the artifact's name
        if run.get("head_branch") != "main":
            raise Refused(f"run {run_id} did not run on main")
        if run.get("event") != "workflow_dispatch":
            raise Refused(f"run {run_id} was started by {run.get('event')}, not by a dispatch")
        pattern, workflow = _BREAKGLASS, "iac-breakglass"
    else:
        if run.get("head_sha") != sha or run.get("head_branch") != "main":
            raise Refused(f"run {run_id} did not run {sha[:12]} on main")
        if run.get("event") not in PLAN_EVENTS:
            raise Refused(f"run {run_id} was started by {run.get('event')}, not by a push to main")
        pattern, workflow = _STAGED, "iac-staged"
    if (run.get("status"), run.get("conclusion")) != ("completed", "success"):
        raise Refused(f"run {run_id} has not completed successfully")
    called = run.get("referenced_workflows")
    paths = (
        [str(w.get("path", "")) for w in called if isinstance(w, dict)]
        if isinstance(called, list)
        else []
    )
    if not any(pattern.search(p) for p in paths):
        raise Refused(f"run {run_id} is not a run of the ayasee {workflow} workflow")
    pushers = tuple(x for x in (_login(run.get("actor")), _login(run.get("triggering_actor"))) if x)
    if not pushers:
        raise Refused(f"run {run_id} has no actor")
    return PlanRun(run_id, art_id, pushers, str(started))


def change_people(gh: GitHub, repo: str, sha: str, pushers: Iterable[str]) -> set[str]:
    """Who made the change at ``sha``: whoever pushed it (the staged run's actors), the author
    and committer of the commit, and the author and merger of its pull requests into main."""
    people = set(pushers)
    try:
        data = gh.commit(repo, sha)
        people.update(x for x in (_login(data.get("author")), _login(data.get("committer"))) if x)
        for pr in gh.commit_pulls(repo, sha):
            base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
            if base.get("ref") != "main" or not isinstance(pr.get("number"), int):
                continue
            people.update(x for x in (_login(pr.get("user")),) if x)
            merged_by = _login(gh.pull_request(repo, pr["number"]).get("merged_by"))
            if merged_by:
                people.add(merged_by)
    except GitHubError as exc:
        raise Refused(f"cannot read who made {sha[:12]}: {exc}") from None
    return people


def check_independent(actor: str, people: Iterable[str], what: str) -> None:
    if actor.casefold() in _fold(people):
        raise Refused(
            f"{actor} {what}: an approver must not approve their own change (D2); another "
            "reviewer approves, or solo_mode (NON-COMPLIANT) allows it"
        )


def authorize(
    gh: GitHub,
    *,
    repo: str,
    sha: str,
    digest: str,
    ref: str,
    event: str,
    actor: str,
    triggering_actor: str,
    head: str,
    cfg: Config,
    environment: str,
    root: Path,
    incident: str = "",
) -> tuple[PlanRun, list[str]]:
    """Every check of the ``verify`` job, in order; raises :class:`Refused`. With an
    ``incident`` it is a break-glass approval: ``reviewers.breakglass``, the plan of an
    ``iac-breakglass`` request, and the requester counts among the people of the change."""
    role: Role = "breakglass" if incident else "prod"
    check_request(
        sha=sha,
        digest=digest,
        ref=ref,
        event=event,
        actor=actor,
        triggering_actor=triggering_actor,
        incident=incident,
    )
    check_reviewer(cfg, actor, role)
    check_ancestor(sha, head, root)
    check_permission(gh, repo, actor)
    notes = check_environment(gh, repo, environment)
    plan = find_plan(gh, repo, sha, role)
    if cfg.solo_mode:
        notes.append("NON-COMPLIANT: solo_mode lets the author approve their own change (D2)")
    else:
        check_independent(actor, change_people(gh, repo, sha, plan.pushers), _made(role))
    return plan, notes


def _made(role: Role) -> str:
    return "requested, wrote or merged it" if role == "breakglass" else "pushed, wrote or merged it"


# ---- digest ------------------------------------------------------------------------------------


def check_digest(manifest: Path, digest: str, sha: str) -> None:
    """The downloaded manifest is the one the reviewer named, for prod at ``sha``."""
    try:
        data = manifest.read_bytes()
    except OSError as exc:
        raise Refused(f"cannot read the manifest: {type(exc).__name__}") from None
    if hashlib.sha256(data).hexdigest() != digest:
        raise Refused(
            "the prod plan does not match manifest-digest: it was re-planned or the digest is "
            "wrong; approve the digest of the newest approval issue"
        )
    try:
        m = Manifest.from_json(data.decode())
    except (ValueError, UnicodeDecodeError):
        raise Refused("the manifest is malformed") from None
    if m.sha != sha or m.env != "prod":
        raise Refused(f"the manifest binds {m.env} at {m.sha[:12]}, not prod at {sha[:12]}")


# ---- the break-glass request -------------------------------------------------------------------


@dataclass(frozen=True)
class Request:
    """``request.json`` of a break-glass plan: what the requester asked for."""

    allow_rollback: bool
    counts: dict[str, int]


def check_request_file(path: Path, *, incident: str, sha: str) -> Request:
    """The request kept with the break-glass plan is for this incident and this commit."""
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        raise Refused("cannot read the break-glass request (request.json)") from None
    if not isinstance(doc, dict) or doc.get("schema") != REQUEST_SCHEMA:
        raise Refused("the break-glass request is malformed")
    counts = doc.get("counts")
    if (
        not isinstance(doc.get("allow_rollback"), bool)
        or not isinstance(counts, dict)
        or set(counts) != set(COUNTS)
        or not all(type(counts[k]) is int and counts[k] >= 0 for k in COUNTS)
    ):
        raise Refused("the break-glass request is malformed")
    if doc.get("incident") != incident:
        raise Refused(f"the break-glass plan was requested for another incident, not {incident}")
    if doc.get("sha") != sha:
        raise Refused(f"the break-glass request is not for {sha[:12]}")
    return Request(doc["allow_rollback"], {k: counts[k] for k in COUNTS})


# ---- baseline ----------------------------------------------------------------------------------


def _git_show(root: Path, sha: str, path: str) -> str | None:
    """The file at ``sha``; None when the commit has no such file. Raises Refused otherwise."""
    try:
        exists = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{sha}^{{commit}}"],
            capture_output=True,
            check=False,
        )
        if exists.returncode != 0:
            raise Refused(f"the prod commit {sha[:12]} is not in the history of main")
        proc = subprocess.run(
            ["git", "-C", str(root), "show", f"{sha}:{path}"],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise Refused(f"cannot run git: {type(exc).__name__}") from None
    if proc.returncode != 0:
        return None
    try:
        return proc.stdout.decode()
    except UnicodeDecodeError:
        raise Refused(f"{path} at {sha[:12]} is not text") from None


# How far back the ledger is searched for the last approved prod apply.
GATE_HISTORY = 200

BOOTSTRAP = (
    "DEVIATION: bootstrap trust: {why}, so reviewers.{role} of main alone decides this approval "
    "(D15); from the first approved apply in this mode on, a reviewer must also be listed at the "
    "last approved commit"
)


def last_approved(store: LedgerStore, repo: str) -> LedgerRecord | None:
    """The newest prod record that carries an approver: the last approved apply. The in-sync
    path (an empty plan, no approval) never counts. None when the ledger has none; refused when
    the newest :data:`GATE_HISTORY` records hold none but more may exist (fail closed)."""
    try:
        records = store.history(repo, "prod", GATE_HISTORY)
    except LedgerError as exc:
        raise Refused(f"cannot read the prod ledger: {exc}") from None
    for rec in records:
        if rec.approver:
            return rec
    if len(records) >= GATE_HISTORY:
        raise Refused(
            f"no approved prod record among the newest {GATE_HISTORY}: the trust anchor of the "
            "workflow gate cannot be found"
        )
    return None


def gate_settings(cfg: Config) -> tuple[str, tuple[str, ...], tuple[str, ...], bool]:
    """What decides who may approve prod: the mechanism, its reviewers (prod and break-glass)
    and solo_mode."""
    return (
        cfg.gate_mechanism,
        tuple(sorted(_fold(cfg.reviewers.prod))),
        tuple(sorted(_fold(cfg.reviewers.breakglass))),
        cfg.solo_mode,
    )


def gate_change(
    store: LedgerStore,
    repo: str,
    cfg: Config,
    read: Callable[[str], str | None],
) -> tuple[bool, list[str]]:
    """Does ``cfg`` (the configuration of the commit planned) change the gate settings against
    the last approved prod apply, with the workflow gate on either side? Such a change needs an
    approval even with an empty plan, so that the in-sync path can never add a reviewer, turn
    solo_mode on, or leave or enter the workflow gate unapproved (D15). ``read(sha)`` returns the
    configuration text at ``sha`` (None: no file). A change of the environment gate alone keeps
    today's in-sync path. Raises :class:`Refused` on anything it cannot read."""
    anchor = last_approved(store, repo)
    if anchor is None:
        prev, where = Config(), "no approved prod apply yet"
    else:
        try:
            text = read(anchor.sha)
            prev = parse_config_text(text) if text is not None else Config()
        except (ConfigError, GitHubError) as exc:
            raise Refused(
                f"cannot read the configuration at the last approved commit: {exc}"
            ) from None
        where = f"the last approved commit {anchor.sha[:12]}"
    if "workflow" not in (cfg.gate_mechanism, prev.gate_mechanism):
        return False, []
    if gate_settings(cfg) == gate_settings(prev):
        return False, []
    return True, [
        "the gate settings (gate_mechanism, reviewers.prod, reviewers.breakglass, solo_mode) "
        f"differ from {where}: this change needs a prod approval even if its plan is empty"
    ]


def baseline(
    gh: GitHub,
    store: LedgerStore,
    *,
    repo: str,
    sha: str,
    actor: str,
    run_id: int,
    main_solo: bool,
    config_path: str,
    root: Path,
    role: Role = "prod",
    plan_started_at: str = "",
) -> list[str]:
    """The checks that need the ledger (the ``apply`` job); raises :class:`Refused`.

    Trust is anchored on the last approved prod apply, not on the commit live in prod: an
    empty plan goes live without an approval (the in-sync path), so a reviewer it added must
    not count (D15). A break-glass approval (``role`` breakglass) checks
    ``reviewers.breakglass`` at that anchor and may re-apply the live commit (to undo a drift),
    but refuses a plan whose request run started before the newest prod record: prod changed
    under it, so it is a replay or lost a race (request again)."""
    try:
        cur = store.current(repo, "prod")
    except LedgerError as exc:
        raise Refused(f"cannot read the prod ledger: {exc}") from None
    if role == "breakglass":
        made = _parse_time(plan_started_at, "the start of the break-glass request")
        if cur is not None and _parse_time(cur.recorded_at, "the prod ledger record") >= made:
            raise Refused(
                f"prod changed after this break-glass request started (the ledger records "
                f"{cur.sha[:12]} since): start iac-breakglass again and approve the new digest"
            )
    elif cur is not None and cur.sha == sha and cur.converged:
        raise Refused(f"{sha[:12]} is already live and converged in prod")
    anchor = last_approved(store, repo)
    notes: list[str] = []
    prev: Config | None = None
    if anchor is not None:
        text = _git_show(root, anchor.sha, config_path)
        try:
            prev = parse_config_text(text) if text is not None else Config()
        except ConfigError as exc:
            raise Refused(f"cannot read {config_path} at the last approved commit: {exc}") from None
    if prev is not None and prev.gate_mechanism == "workflow":
        assert anchor is not None
        if actor.casefold() not in _fold(reviewers_of(prev, role)):
            raise Refused(
                f"{actor} was not in reviewers.{role} at the last approved commit "
                f"({anchor.sha[:12]}): a reviewer added since then approves from the next change "
                "after the one that adds them is approved, so that no change approves itself"
            )
        solo = main_solo and prev.solo_mode
    else:
        why = (
            "there is no approved prod apply yet"
            if anchor is None
            else f"the last approved commit ({anchor.sha[:12]}) predates gate_mechanism: workflow"
        )
        notes.append(BOOTSTRAP.format(why=why, role=role))
        solo = main_solo
    if solo:
        return notes
    if main_solo:
        notes.append("solo_mode was off at the last approved commit: independence is checked")
        try:
            pushers = find_run_pushers(gh, repo, run_id)
        except GitHubError as exc:
            raise Refused(f"cannot read run {run_id}: {exc}") from None
        check_independent(actor, change_people(gh, repo, sha, pushers), _made(role))
    # every commit since the last approval, the ones that went live in sync included
    base = anchor if anchor is not None else cur
    if base is None:
        return notes
    if anchor is None:
        _git_show(root, base.sha, config_path)  # the live commit must be in main's history
    try:
        commits = gh.compare_commits(repo, base.sha, sha)
    except GitHubError as exc:
        raise Refused(f"cannot list the commits since {base.sha[:12]}: {exc}") from None
    people: set[str] = set()
    unmapped = 0
    for c in commits:
        found = [x for x in (_login(c.get("author")), _login(c.get("committer"))) if x]
        if _login(c.get("author")) is None:
            unmapped += 1
        people.update(found)
    check_independent(actor, people, f"wrote a commit since {base.sha[:12]}")
    if unmapped:
        notes.append(
            f"{unmapped} commit(s) since {base.sha[:12]} have an author GitHub cannot map to a "
            "login; their authors were not checked"
        )
    return notes


def find_run_pushers(gh: GitHub, repo: str, run_id: int) -> tuple[str, ...]:
    run = gh.get_run(repo, run_id)
    return tuple(x for x in (_login(run.get("actor")), _login(run.get("triggering_actor"))) if x)


# ---- the approval issue ------------------------------------------------------------------------


def issue_body(
    *, sha: str, digest: str, run_url: str, changes: int, critical: int, high: int
) -> str:
    """Identifiers, counts and links only: never a plan value."""
    return (
        "A prod plan waits for approval. With `gate_mechanism: workflow` no job waits for a "
        "reviewer: nothing is applied until a reviewer listed in `reviewers.prod` of "
        "`.ayasee.yaml` on `main` starts the **iac-approve** workflow on `main` with these "
        "inputs (D15).\n\n"
        f"- sha: `{sha}`\n"
        f"- manifest-digest: `{digest}`\n\n"
        "```\n"
        f"gh workflow run iac-approve.yml --ref main -f sha={sha} -f manifest-digest={digest}\n"
        "```\n\n"
        f"- Plan run: {run_url}\n"
        f"- Changes: {changes} (critical {critical}, high {high}); the details are in the run's "
        "summary, never here\n"
        "- The reviewer must not have pushed, written or merged the change (unless solo_mode).\n"
        "- The plan artifact is kept 7 days; after that, or after a newer commit reaches the "
        "stage before prod, re-run the staged run and approve the new digest.\n\n"
        f"> {DEVIATION_GATE}\n"
    )


def open_issue(
    gh: GitHub, repo: str, *, sha: str, digest: str, run_url: str, counts: dict[str, int]
) -> int:
    """Open the approval issue, or rewrite the open one (only the newest plan can be applied)."""
    body = issue_body(sha=sha, digest=digest, run_url=run_url, **counts)
    title = f"prod approval: {sha[:12]}"
    existing = gh.list_issues(repo, [PROD_APPROVAL_LABEL, "env:prod"])
    if not existing:
        return gh.create_issue(repo, title, body, [PROD_APPROVAL_LABEL, "env:prod"])
    number = existing[0].get("number")
    if not isinstance(number, int):
        raise GitHubError(0, "issue without a number")
    gh.update_issue(repo, number, body=body, title=title)
    gh.create_issue_comment(repo, number, f"Replaced by the plan of {run_url} (`{sha[:12]}`).")
    return number


def breakglass_issue_body(
    *,
    incident: str,
    sha: str,
    digest: str,
    run_url: str,
    requester: str,
    counts: dict[str, int],
    rollback_from: str | None,
) -> str:
    """Identifiers, counts and links only: never a plan value."""
    rollback = (
        f"- **Rollback** from `{rollback_from[:12]}`: the commit is older than the one live in "
        "prod\n"
        if rollback_from
        else ""
    )
    return (
        f"Break-glass was requested by @{requester} for incident `{incident}`. With "
        "`gate_mechanism: workflow` no job waits for a reviewer: nothing is applied until a "
        "reviewer listed in `reviewers.breakglass` of `.ayasee.yaml` on `main`, who is not the "
        "requester, starts the **iac-approve** workflow on `main` with these inputs (D3, D15)."
        "\n\n"
        f"- incident: `{incident}`\n"
        f"- sha: `{sha}`\n"
        f"- manifest-digest: `{digest}`\n\n"
        "```\n"
        f"gh workflow run iac-approve.yml --ref main -f incident={incident} -f sha={sha} "
        f"-f manifest-digest={digest}\n"
        "```\n\n"
        f"- Plan run: {run_url}\n"
        f"{rollback}"
        f"- Changes: {counts['total']} (critical {counts['critical']}, high {counts['high']}); "
        "the details are in the run's summary, never here\n"
        "- The approver must not have requested, written or merged the change (unless "
        "solo_mode). A post-approval issue is opened after the apply (DORA RTS 2024/1774 "
        "Art. 17(1)(g)).\n"
        "- The plan is refused once prod changed after it was made, and the artifact is kept "
        "7 days: then start iac-breakglass again and approve the new digest.\n\n"
        f"> {DEVIATION_GATE}\n"
    )


def open_breakglass_issue(
    gh: GitHub,
    repo: str,
    *,
    incident: str,
    sha: str,
    digest: str,
    run_url: str,
    requester: str,
    counts: dict[str, int],
    rollback_from: str | None,
) -> int:
    """Open the break-glass approval issue of ``incident``, or rewrite its open one (only the
    newest plan of a request can be applied)."""
    body = breakglass_issue_body(
        incident=incident,
        sha=sha,
        digest=digest,
        run_url=run_url,
        requester=requester,
        counts=counts,
        rollback_from=rollback_from,
    )
    title = f"break-glass approval: {incident} {sha[:12]}"
    labels = [BREAKGLASS_APPROVAL_LABEL, "env:prod"]
    for issue in gh.list_issues(repo, labels):
        number = issue.get("number")
        same = str(issue.get("title", "")).startswith(f"break-glass approval: {incident} ")
        if isinstance(number, int) and same:
            gh.update_issue(repo, number, body=body, title=title)
            gh.create_issue_comment(repo, number, f"Replaced by the plan of {run_url}.")
            return number
    return gh.create_issue(repo, title, body, labels)


def close_issues(
    gh: GitHub,
    repo: str,
    *,
    sha: str,
    digest: str,
    run_url: str,
    approver: str,
    label: str = PROD_APPROVAL_LABEL,
) -> int:
    """Close the open approval issues of this plan; returns how many."""
    closed = 0
    for issue in gh.list_issues(repo, [label, "env:prod"]):
        number = issue.get("number")
        body = issue.get("body") if isinstance(issue.get("body"), str) else ""
        if not isinstance(number, int) or sha not in body or digest not in body:
            continue
        gh.create_issue_comment(repo, number, f"Applied by {run_url}, approved by @{approver}.")
        gh.update_issue(repo, number, state="closed")
        closed += 1
    return closed


# ---- CLI ---------------------------------------------------------------------------------------


def _report(notes: list[str]) -> None:
    for line in notes:
        kind = "warning" if line.startswith(("DEVIATION", "NON-COMPLIANT")) else "notice"
        print(f"{kind}: {line}", file=sys.stderr)


def _load_quiet(path: str) -> Config:
    p = Path(path)
    try:
        return parse_config_text(p.read_text()) if p.is_file() else Config()
    except (ConfigError, OSError, UnicodeDecodeError) as exc:
        raise Refused(f"cannot read {path}: {exc}") from None


def _authorize(args: argparse.Namespace) -> int:
    gh: GitHub | None = None
    role: Role = "breakglass" if args.incident else "prod"
    try:
        cfg = _load_quiet(args.config_path)
        gh = GitHub.from_env()
        plan, notes = authorize(
            gh,
            repo=args.repo,
            sha=args.sha,
            digest=args.digest,
            ref=args.ref,
            event=args.event,
            actor=args.actor,
            triggering_actor=args.triggering_actor,
            head=args.head,
            cfg=cfg,
            environment=cfg.environment_prefix + ("prod-breakglass" if args.incident else "prod"),
            root=Path(args.root),
            incident=args.incident,
        )
    except (Refused, GitHubError) as exc:
        print(f"approve: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # unexpected data: fail closed, never a traceback
        print(f"approve: unexpected {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        if gh is not None:
            gh.close()
    _report(notes)
    print(f"run-id={plan.run_id}")
    print(f"approver={args.actor}")
    print(f"solo-mode={'true' if cfg.solo_mode else 'false'}")
    print(f"environment-prefix={cfg.environment_prefix}")
    print(f"env-prod={cfg.environment_prefix}prod")
    print(f"env-breakglass={cfg.environment_prefix}prod-breakglass")
    print(f"kind={role}")
    print(f"artifact={artifact_name(role, args.sha)}")
    print(f"plan-started-at={plan.started_at}")
    print(f"requester={plan.pushers[0]}")
    return 0


def _request(args: argparse.Namespace) -> int:
    """Prints ``allow-rollback=true|false``; writes the change counts to ``--counts-out``."""
    try:
        if not SHA_RE.fullmatch(args.sha) or not INCIDENT_RE.fullmatch(args.incident):
            raise Refused("sha or incident is malformed")
        req = check_request_file(Path(args.request), incident=args.incident, sha=args.sha)
        Path(args.counts_out).write_text(json.dumps(req.counts) + "\n")
    except Refused as exc:
        print(f"approve: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"approve: cannot write the counts: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"allow-rollback={'true' if req.allow_rollback else 'false'}")
    return 0


def _digest(args: argparse.Namespace) -> int:
    try:
        if not DIGEST_RE.fullmatch(args.digest) or not SHA_RE.fullmatch(args.sha):
            raise Refused("sha or manifest-digest is malformed")
        check_digest(Path(args.manifest), args.digest, args.sha)
    except Refused as exc:
        print(f"approve: {exc}", file=sys.stderr)
        return 1
    return 0


def _baseline(args: argparse.Namespace) -> int:
    gh: GitHub | None = None
    try:
        if not SHA_RE.fullmatch(args.sha):
            raise Refused("sha is malformed")
        if args.solo_mode not in ("true", "false"):
            raise Refused("solo-mode must be true or false")
        if args.role not in ("prod", "breakglass"):
            raise Refused("role must be prod or breakglass")
        store = open_store(args.store)
        gh = GitHub.from_env()
        notes = baseline(
            gh,
            store,
            repo=args.repo,
            sha=args.sha,
            actor=args.actor,
            run_id=args.run_id,
            main_solo=args.solo_mode == "true",
            config_path=args.config_path,
            root=Path(args.root),
            role=args.role,
            plan_started_at=args.plan_started_at,
        )
    except (Refused, GitHubError, LedgerError) as exc:
        print(f"approve: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # fail closed, never a traceback
        print(f"approve: unexpected {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        if gh is not None:
            gh.close()
    _report(notes)
    return 0


def _gate_change(args: argparse.Namespace) -> int:
    """Prints ``gate-changed=true|false``; the anchor's configuration is read with the GitHub
    contents API, so the job needs no history."""
    gh: GitHub | None = None
    try:
        cfg = _load_quiet(args.config_path)
        store = open_store(args.store)
        gh = GitHub.from_env()
        client = gh
        changed, notes = gate_change(
            store, args.repo, cfg, lambda sha: client.get_file(args.repo, args.config_path, sha)
        )
    except (Refused, GitHubError, LedgerError) as exc:
        print(f"approve: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # fail closed, never a traceback
        print(f"approve: unexpected {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        if gh is not None:
            gh.close()
    _report(notes)
    print(f"gate-changed={'true' if changed else 'false'}")
    return 0


def _counts(args: argparse.Namespace) -> dict[str, int]:
    out = {}
    for key in ("changes", "critical", "high"):
        value = getattr(args, key)
        if not re.fullmatch(r"[0-9]{1,9}", value):
            raise Refused(f"{key} must be a count")
        out[key] = int(value)
    return out


def _issue(args: argparse.Namespace) -> int:
    gh: GitHub | None = None
    try:
        if not SHA_RE.fullmatch(args.sha) or not DIGEST_RE.fullmatch(args.digest):
            raise Refused("sha or manifest-digest is malformed")
        if not _URL_RE.fullmatch(args.run_url):
            raise Refused("run-url must be an https URL")
        counts = _counts(args)
        gh = GitHub.from_env()
        number = open_issue(
            gh, args.repo, sha=args.sha, digest=args.digest, run_url=args.run_url, counts=counts
        )
    except (Refused, GitHubError) as exc:
        print(f"approve: {exc}", file=sys.stderr)
        return 1
    finally:
        if gh is not None:
            gh.close()
    print(f"approve: approval issue #{number}")
    return 0


def _breakglass_issue(args: argparse.Namespace) -> int:
    gh: GitHub | None = None
    try:
        if not SHA_RE.fullmatch(args.sha) or not DIGEST_RE.fullmatch(args.digest):
            raise Refused("sha or manifest-digest is malformed")
        if not INCIDENT_RE.fullmatch(args.incident):
            raise Refused("incident must look like ABC-123 or INC123")
        if args.rollback_from and not SHA_RE.fullmatch(args.rollback_from):
            raise Refused("rollback-from must be a full commit SHA")
        if not _URL_RE.fullmatch(args.run_url):
            raise Refused("run-url must be an https URL")
        if not _LOGIN_RE.fullmatch(args.requester):
            raise Refused("requester must be a GitHub login")
        counts = {}
        for key in COUNTS:
            value = getattr(args, key)
            if not re.fullmatch(r"[0-9]{1,9}", value):
                raise Refused(f"{key} must be a count")
            counts[key] = int(value)
        gh = GitHub.from_env()
        number = open_breakglass_issue(
            gh,
            args.repo,
            incident=args.incident,
            sha=args.sha,
            digest=args.digest,
            run_url=args.run_url,
            requester=args.requester,
            counts=counts,
            rollback_from=args.rollback_from or None,
        )
    except (Refused, GitHubError) as exc:
        print(f"approve: {exc}", file=sys.stderr)
        return 1
    finally:
        if gh is not None:
            gh.close()
    print(f"approve: break-glass approval issue #{number}")
    return 0


def _close(args: argparse.Namespace) -> int:
    gh: GitHub | None = None
    try:
        if not SHA_RE.fullmatch(args.sha) or not DIGEST_RE.fullmatch(args.digest):
            raise Refused("sha or manifest-digest is malformed")
        if not _URL_RE.fullmatch(args.run_url):
            raise Refused("run-url must be an https URL")
        gh = GitHub.from_env()
        n = close_issues(
            gh,
            args.repo,
            sha=args.sha,
            digest=args.digest,
            run_url=args.run_url,
            approver=args.approver,
            label=BREAKGLASS_APPROVAL_LABEL if args.breakglass else PROD_APPROVAL_LABEL,
        )
    except (Refused, GitHubError) as exc:
        print(f"approve: {exc}", file=sys.stderr)
        return 1
    finally:
        if gh is not None:
            gh.close()
    print(f"approve: closed {n} approval issue(s)")
    return 0


def _setup(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="approve_command", metavar="<action>", required=True)
    p = sub.add_parser("authorize", help="the checks of the verify job (no secret needed)")
    p.add_argument("--repo", required=True, type=_repo)
    p.add_argument("--sha", required=True)
    p.add_argument("--digest", required=True)
    p.add_argument("--ref", required=True)
    p.add_argument("--event", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--triggering-actor", required=True)
    p.add_argument("--head", required=True, help="the checked-out tip of main")
    p.add_argument("--config-path", required=True)
    p.add_argument("--root", default=".")
    p.add_argument("--incident", default="", help="a break-glass approval: its incident")
    p.set_defaults(handler=_authorize)
    p = sub.add_parser("request", help="check request.json of a break-glass plan")
    p.add_argument("--request", required=True)
    p.add_argument("--incident", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--counts-out", required=True, help="where to write the change counts")
    p.set_defaults(handler=_request)
    p = sub.add_parser("digest", help="check a downloaded manifest against the approved digest")
    p.add_argument("--manifest", required=True)
    p.add_argument("--digest", required=True)
    p.add_argument("--sha", required=True)
    p.set_defaults(handler=_digest)
    p = sub.add_parser("baseline", help="the checks that need the ledger (the apply job)")
    p.add_argument("--store", required=True)
    p.add_argument("--repo", required=True, type=_repo)
    p.add_argument("--sha", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--run-id", required=True, type=int, help="the staged run of the plan")
    p.add_argument("--solo-mode", required=True, help="solo_mode of main: true or false")
    p.add_argument("--config-path", required=True)
    p.add_argument("--root", default=".")
    p.add_argument("--role", default="prod", help="prod or breakglass")
    p.add_argument("--plan-started-at", default="", help="breakglass: when the request run started")
    p.set_defaults(handler=_baseline)
    p = sub.add_parser(
        "gate-change",
        help="print gate-changed=true when the gate settings differ from the last approved apply",
    )
    p.add_argument("--store", required=True)
    p.add_argument("--repo", required=True, type=_repo)
    p.add_argument("--config-path", required=True, help="the configuration of the planned commit")
    p.set_defaults(handler=_gate_change)
    p = sub.add_parser("issue", help="open or rewrite the prod approval issue")
    p.add_argument("--repo", required=True, type=_repo)
    p.add_argument("--sha", required=True)
    p.add_argument("--digest", required=True)
    p.add_argument("--run-url", required=True)
    p.add_argument("--changes", required=True)
    p.add_argument("--critical", required=True)
    p.add_argument("--high", required=True)
    p.set_defaults(handler=_issue)
    p = sub.add_parser("breakglass-issue", help="open or rewrite a break-glass approval issue")
    p.add_argument("--repo", required=True, type=_repo)
    p.add_argument("--incident", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--digest", required=True)
    p.add_argument("--run-url", required=True)
    p.add_argument("--requester", required=True)
    p.add_argument("--rollback-from", default="")
    for key in COUNTS:
        p.add_argument(f"--{key}", required=True)
    p.set_defaults(handler=_breakglass_issue)
    p = sub.add_parser("close-issue", help="close the approval issue of an applied plan")
    p.add_argument("--repo", required=True, type=_repo)
    p.add_argument("--sha", required=True)
    p.add_argument("--digest", required=True)
    p.add_argument("--run-url", required=True)
    p.add_argument("--approver", required=True)
    p.add_argument("--breakglass", action="store_true", help="a break-glass approval issue")
    p.set_defaults(handler=_close)


cli.register("approve", _setup)
