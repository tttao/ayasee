"""Can this repository's GitHub plan hold the gate? (issue #39)

The prod gate, the ``main``-only environments and the guard of the ledger token rest on three
GitHub features that a private repository does not have on every plan
(docs.github.com, "Deployments and environments" and "Managing environments"):

- **required reviewers on environments**: in a private repository, GitHub Enterprise only (Cloud
  or Server); GitHub Free, Pro and Team have them on public repositories only;
- **deployment branch policies**: in a private repository, GitHub Pro, Team or Enterprise;
- **rulesets and branch protection**: in a private repository, GitHub Pro, Team or Enterprise.

:func:`detect` answers from the API and returns a :class:`PlanSupport`. It fails closed: a
verdict is ``SUPPORTED`` only on evidence (a public or internal repository, an Enterprise host, a
plan read from the owner, or required reviewers that an environment already carries), and
anything else that is not a definite ``UNSUPPORTED`` is ``UNKNOWN``. Every call is a read:
``/repos/{repo}`` and ``/repos/{repo}/rulesets`` need Metadata read, ``/repos/{repo}/environments``
Actions read, and the owner's plan is visible only to an owner of the organization (``/orgs``) or
to the user themself (``/user``); a call that fails is evidence of nothing.

Callers decide what a verdict means: ``ayasee doctor`` fails on ``UNSUPPORTED`` and ``UNKNOWN``,
``ayasee init`` warns, ``ayasee require-reviewers`` refuses a definite ``UNSUPPORTED``. With
``gate_mechanism: workflow`` (D15, issue #40), which does not rest on environment reviewers, the
doctor reads the same result to say what that gate keeps, and ``ayasee approve`` reads it to tell
a plan without branch policies from a prod environment left open."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ayasee.github import GitHub, GitHubError

SUPPORTED, UNSUPPORTED, UNKNOWN = "supported", "unsupported", "unknown"

REVIEWERS = "environment_reviewers"
BRANCH_POLICIES = "deployment_branch_policies"
RULESETS = "rulesets"

# feature -> (what GitHub calls it, the plans that have it on a private repository, what falls)
FEATURES: dict[str, tuple[str, str, str]] = {
    REVIEWERS: (
        "required reviewers on environments",
        "GitHub Enterprise",
        "the prod gate (D9) falls: nothing waits for an approval before prod, and "
        "prevent_self_review (D2), the separate break-glass reviewers (D3) and the reviewer of "
        "prod-pr-plan go with it",
    ),
    BRANCH_POLICIES: (
        "deployment branch policies",
        "GitHub Pro, Team or Enterprise",
        "the main-only environments fall: any branch can run a job in prod-plan, prod or a "
        "stage and read its secrets",
    ),
    RULESETS: (
        "rulesets and branch protection",
        "GitHub Pro, Team or Enterprise",
        "the guard of the ledger token falls: a token that writes the ledger can push to main",
    ),
}
FREE_MISSING = (REVIEWERS, BRANCH_POLICIES, RULESETS)

# what each plan lacks on a private repository; a plan name not listed here is unknown
PRIVATE_MISSING: dict[str, tuple[str, ...]] = {
    "free": FREE_MISSING,
    "pro": (REVIEWERS,),
    "team": (REVIEWERS,),
    "enterprise": (),
}

_UPGRADE = re.compile(r"upgrade to github|make this repository public", re.I)


def is_plan_error(exc: GitHubError) -> bool:
    """The 403 GitHub answers when the repository's plan lacks the feature."""
    return exc.status == 403 and bool(_UPGRADE.search(exc.message))


@dataclass(frozen=True)
class PlanSupport:
    """``verdict``: SUPPORTED, UNSUPPORTED or UNKNOWN. ``visibility``: public, private, internal or
    None when unread. ``plan``: free, pro, team or enterprise when known. ``missing``: the
    :data:`FEATURES` keys known to be missing (empty for UNKNOWN: unknown is not missing).
    ``evidence``: how the verdict was reached, one short phrase each, never a value."""

    verdict: str
    visibility: str | None
    plan: str | None = None
    missing: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    @property
    def supported(self) -> bool:
        return self.verdict == SUPPORTED

    @property
    def inferred(self) -> bool:
        """SUPPORTED on a private repository without its plan: reviewers already in place."""
        return self.supported and self.visibility == "private" and self.plan is None

    def explain(self) -> list[str]:
        """The verdict in ayasee's words: which features are missing, which guarantees fall."""
        where = f"a {self.visibility} repository" if self.visibility else "this repository"
        plan = f" on the GitHub {self.plan.capitalize()} plan" if self.plan else ""
        if self.verdict == SUPPORTED:
            how = "; ".join(self.evidence) or "public"
            return [f"{where}{plan} has every feature the gate needs ({how})"]
        if self.verdict == UNSUPPORTED:
            lines = [f"{where}{plan} does not have what the default gate rests on:"]
            for key in self.missing:
                name, plans, falls = FEATURES[key]
                lines.append(f"- no {name} (a private repository has them on {plans}): {falls}")
            lines.append(
                "ayasee does not support such a repository with its default gate: make it public "
                "or move it to GitHub Enterprise, or set gate_mechanism: workflow (D15, issue "
                "#40), a gate that ayasee enforces without these features, labelled a DEVIATION"
            )
            return lines
        return [
            f"cannot confirm that {where} has required reviewers on environments "
            f"({'; '.join(self.evidence) or 'no evidence either way'}); a private repository "
            "has them on GitHub Enterprise only, and without them nothing waits for an approval "
            "before prod",
            "an organization owner's token shows the plan, and once the bootstrap has set the "
            "reviewers they are seen; on another plan ayasee does not support the repository with "
            "its default gate; gate_mechanism: workflow (D15, issue #40) does not need them",
        ]


def _visibility(data: dict[str, object]) -> str | None:
    vis = data.get("visibility")
    if isinstance(vis, str):
        return vis.lower()
    private = data.get("private")
    if isinstance(private, bool):
        return "private" if private else "public"
    return None


def _plan_name(data: dict[str, object]) -> str | None:
    plan = data.get("plan")
    name = plan.get("name") if isinstance(plan, dict) else None
    return name.lower() if isinstance(name, str) else None


def _owner_plan(gh: GitHub, data: dict[str, object]) -> tuple[str | None, str]:
    """The plan of the repository's owner when the token may read it, and how it was read."""
    owner = data.get("owner")
    login = owner.get("login") if isinstance(owner, dict) else None
    kind = owner.get("type") if isinstance(owner, dict) else None
    if not isinstance(login, str):
        return None, "no owner in the repository response"
    try:
        if kind == "Organization":
            plan = _plan_name(gh.organization(login))
            how = "the organization's plan"
        elif kind == "User":
            me = gh.authenticated_user()
            if me.get("login") != login:
                return None, "the owner's plan is visible to the owner only"
            plan, how = _plan_name(me), "the owner's plan"
        else:
            return None, "unknown owner type"
    except GitHubError as exc:
        return None, f"cannot read the owner's plan ({exc.status or 'no answer'})"
    return plan, how if plan else f"{how} is not visible to this token"


def reviewer_count(env: dict[str, object]) -> int:
    """The required reviewers of an environment as the API returns it; 0 for anything else."""
    rules = env.get("protection_rules")
    for rule in rules if isinstance(rules, list) else []:
        if isinstance(rule, dict) and rule.get("type") == "required_reviewers":
            reviewers = rule.get("reviewers")
            return len(reviewers) if isinstance(reviewers, list) else 0
    return 0


def _enterprise_host(gh: GitHub) -> bool:
    host = gh.api_host
    if host == "api.github.com":
        return False
    if host.endswith(".ghe.com"):  # GitHub Enterprise Cloud with data residency
        return True
    try:
        return isinstance(gh.meta().get("installed_version"), str)  # GitHub Enterprise Server
    except GitHubError:
        return False


def detect(gh: GitHub, repo: str) -> PlanSupport:
    """Whether ``repo`` can carry environment reviewers, branch policies and rulesets."""
    try:
        data = gh.repository(repo)
    except GitHubError as exc:
        return PlanSupport(UNKNOWN, None, evidence=(f"cannot read the repository: {exc}",))
    vis = _visibility(data)
    if vis == "public":
        return PlanSupport(SUPPORTED, vis, evidence=("public",))
    if vis == "internal":  # internal repositories exist on GitHub Enterprise only
        return PlanSupport(SUPPORTED, vis, "enterprise", evidence=("internal",))
    if vis != "private":
        return PlanSupport(UNKNOWN, vis, evidence=("unrecognised visibility",))
    if _enterprise_host(gh):
        return PlanSupport(SUPPORTED, vis, "enterprise", evidence=("a GitHub Enterprise host",))

    plan, how = _owner_plan(gh, data)
    if plan in PRIVATE_MISSING:
        missing = PRIVATE_MISSING[plan]
        return PlanSupport(UNSUPPORTED if missing else SUPPORTED, vis, plan, missing, (how,))
    evidence = [how if plan is None else "an owner plan ayasee does not know"]

    # no plan: GitHub's own answers are the evidence
    try:
        gh.rulesets(repo)
    except GitHubError as exc:
        if is_plan_error(exc):
            ev = (*evidence, "the rulesets API asks for a plan upgrade")
            return PlanSupport(UNSUPPORTED, vis, "free", FREE_MISSING, ev)
        evidence.append(f"cannot read the rulesets ({exc.status or 'no answer'})")
    try:
        envs = gh.environments(repo)
    except GitHubError as exc:
        if is_plan_error(exc):
            ev = (*evidence, "the environments API asks for a plan upgrade")
            return PlanSupport(UNSUPPORTED, vis, "free", FREE_MISSING, ev)
        evidence.append(f"cannot read the environments ({exc.status or 'no answer'})")
        envs = []
    gated = sorted(str(e.get("name")) for e in envs if reviewer_count(e))
    if gated:
        ev = (f"required reviewers are set on {', '.join(gated)}",)
        return PlanSupport(SUPPORTED, vis, evidence=ev)
    evidence.append("no environment carries required reviewers yet")
    return PlanSupport(UNKNOWN, vis, evidence=tuple(evidence))
