"""``ayasee doctor``: what the first run of a repository needs, checked before it runs (issue #11).

Run it locally, at the root of the adopting repository. It prints one list of what is missing
instead of failed jobs found one at a time:

- **GitHub plan** (first, with ``GITHUB_TOKEN``): the repository can carry required reviewers on
  environments, deployment branch policies and rulesets (issue #39, :mod:`ayasee.plansupport`): a
  public repository, or a private one on a plan that has them. With the environment gate, a plan
  that lacks them is one failure that names the missing features and the guarantees that fall with
  them and points to ``gate_mechanism: workflow`` (D15), and the GitHub settings below are then
  not checked one by one; a plan that cannot be confirmed fails too. With ``gate_mechanism:
  workflow`` the plan decides what the gate keeps: Pro or Team keep the main-only environments and
  their secrets (ok), Free keeps neither (a warning that says so, and a failure when
  ``iac-pr-shared`` is called: it refuses to run there);
- **configuration**: ``.ayasee.yaml`` loads, uses only supported settings, fits the staged job
  slots, matches ``shared_units`` on disk, and has a directory per stage under ``units_root``;
- **callers**: the workflows under ``.github/workflows`` that call the ayasee reusable workflows
  pin one full release SHA (no ``OWNER`` placeholder, no all-zero SHA), the ``iac-staged`` and
  ``iac-pr`` callers exist, and the pull-request caller has no ``paths:`` filter; with
  ``gate_mechanism: workflow`` and ``shared_units``, a warning when no workflow calls
  ``iac-pr-shared`` (the shared units are then not planned in pull requests);
- **environments**: no workflow that is not an ayasee caller names an environment ayasee uses
  (``environment_prefix`` keeps them apart, issue #31);
- **GitHub** (with ``GITHUB_TOKEN``, read-only): every environment the callers use exists, under
  its ``environment_prefix`` name, and carries the ``AYASEE_MANAGED`` marker of the bootstrap (a
  warning when not); prod,
  prod-breakglass and prod-pr-plan have required reviewers and prevent self-review (unless
  ``solo_mode``), or, with ``gate_mechanism: workflow``, every login of ``reviewers.prod`` and
  ``reviewers.breakglass`` has write access to the repository (break-glass in that mode also needs
  ``reviewers.breakglass`` and an ``iac-approve`` caller that passes ``incident``); the
  environments are restricted to ``main`` except the pull-request plan ones
  (``<first stage>-plan``, and ``prod-pr-plan`` with the environment gate only: with the
  workflow gate it is main-only, since no reviewer guards it; not checked on GitHub Free
  with the workflow gate, which has no branch policies);
  each secret a caller passes exists where its jobs read it (provider, plan-key and ``s3://``
  ledger-key secrets as environment secrets of each environment that reads them, never at
  repository level); the variables the
  workflows read (``AYASEE_LEDGER_URL``, ``AZURE_*``, the plan key IDs) are set; and ``main`` has a
  pull-request ruleset when a ``ledger-token`` is passed;
- **lock** (opt-in, ``--lock-check s3://<bucket>?region=..&endpoint=..``, AWS credentials in the
  environment): the store refuses a second ``If-None-Match`` write, which ``use_lockfile`` needs.
  This is the only check that writes: one object, deleted afterwards.

Reading environments, environment secrets and variables needs a token of a repository
administrator (``GITHUB_TOKEN=$(gh auth token)``). Secret values are never read; only names.

Exit status: 0 when nothing failed and nothing was skipped, 1 when a check failed, 2 when nothing
failed but a check could not run (no token, no lock check)."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

from ayasee import cli
from ayasee.changed import DEFAULT_CONFIG
from ayasee.config import (
    Config,
    ConfigError,
    environment_names,
    parse_config_text,
    pr_plan_environment,
    shared_layout_problems,
    staged_layout,
    unsupported,
)
from ayasee.github import GitHub, GitHubError
from ayasee.maintip import _repo
from ayasee.plansupport import (
    BRANCH_POLICIES,
    SUPPORTED,
    UNSUPPORTED,
    PlanSupport,
    detect,
    is_plan_error,
)
from ayasee.releasepin import NotARelease, release_version, version_key

OK, WARN, FAIL, SKIP = "ok", "warn", "FAIL", "skip"

_CALLER = re.compile(
    r"^(?P<repo>[^/@\s]+/[^/@\s]+)/\.github/workflows/"
    r"(?P<kind>iac-(?:staged|pr-shared|pr|nightly|breakglass|approve))\.ya?ml@(?P<ref>\S+)$"
)
_SECRET_REF = re.compile(r"^\$\{\{\s*secrets\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_REMOTE = re.compile(r"github\.com[:/]([A-Za-z0-9._-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?$")

# secrets a caller passes that the plan and apply steps read: environment secrets of each stage
PROVIDER_SECRETS = frozenset(
    {"CLOUDFLARE_API_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AYASEE_PROVIDER_ENV"}
)
# plan-encryption key secrets: environment secrets of the environments that wrap or unwrap
PLAN_KEY_SECRETS = {
    "pbkdf2": ("AYASEE_PLAN_PASSPHRASE",),
    "aws_kms": ("AYASEE_KMS_ACCESS_KEY_ID", "AYASEE_KMS_SECRET_ACCESS_KEY"),
    "gcp_kms": ("AYASEE_GCP_KMS_CREDENTIALS",),
    "openbao": ("AYASEE_OPENBAO_TOKEN",),
}
PLAN_KEY_VARIABLES = {
    "azure_vault": "AYASEE_KEY_VAULT_KEY_ID",
    "aws_kms": "AYASEE_KMS_KEY_ID",
    "gcp_kms": "AYASEE_GCP_KMS_KEY_NAME",
    "openbao": "AYASEE_OPENBAO_KEY_URL",
}
# the dedicated key of an s3:// ledger (issue #10): environment secrets of every environment whose
# guard and ledger steps read or write the ledger, never the state key; both or neither
LEDGER_KEY_SECRETS = ("AYASEE_LEDGER_AWS_ACCESS_KEY_ID", "AYASEE_LEDGER_AWS_SECRET_ACCESS_KEY")
LEDGER_KEY_KINDS = frozenset({"iac-staged", "iac-nightly", "iac-breakglass", "iac-approve"})
OIDC_VARIABLES = ("AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID")
GATED = ("prod", "prod-breakglass", "prod-pr-plan")
LOCK_KEY = "ayasee-doctor-lock-check.tflock"


@dataclass
class Report:
    lines: list[tuple[str, str, str]] = field(default_factory=list)

    def add(self, level: str, section: str, message: str) -> None:
        self.lines.append((level, section, message))

    def count(self, level: str) -> int:
        return sum(1 for lv, _, _ in self.lines if lv == level)

    def render(self) -> str:
        out, section = [], None
        for level, sec, msg in self.lines:
            if sec != section:
                out.append(f"{'' if section is None else chr(10)}{sec}")
                section = sec
            out.append(f"  {level:<4}  {msg}")
        fails, warns, skips = self.count(FAIL), self.count(WARN), self.count(SKIP)
        out.append(f"\n{fails} failed, {warns} warnings, {skips} skipped")
        return "\n".join(out)

    @property
    def status(self) -> int:
        return 1 if self.count(FAIL) else 2 if self.count(SKIP) else 0


@dataclass
class Caller:
    file: str
    job: str
    kind: str  # iac-staged, iac-pr, iac-nightly, iac-breakglass
    source: str  # owner/repo of ayasee
    ref: str
    secrets: dict[str, str | None] | None  # input name -> repository secret name; None: inherit
    pr_paths: bool = False
    inputs: frozenset[str] = frozenset()  # the with: keys the caller passes


# ---- configuration ---------------------------------------------------------------------------


def check_config(report: Report, config_path: str, root: Path) -> Config | None:
    sec = "configuration"
    path = root / config_path
    try:
        # parse_config_text: the labels are reported below, not printed on stderr by the load
        cfg = parse_config_text(path.read_text()) if path.is_file() else Config()
    except (ConfigError, OSError, UnicodeDecodeError) as exc:
        report.add(FAIL, sec, f"{config_path}: {exc}")
        return None
    if not path.is_file():
        report.add(WARN, sec, f"{config_path}: not found, the defaults apply")
    else:
        report.add(OK, sec, f"{config_path} loads (stages {', '.join(cfg.stages)})")
    for msg in unsupported(cfg):
        report.add(FAIL, sec, msg)
    try:
        staged_layout(cfg.stages, "")
    except ConfigError as exc:
        report.add(FAIL, sec, f"stages: {exc}")
    for msg in shared_layout_problems(cfg, root):
        report.add(FAIL, sec, msg)
    for stage in cfg.stages:
        if not (root / cfg.units_root / stage).is_dir():
            report.add(FAIL, sec, f"no directory {cfg.units_root}/{stage} for stage {stage}")
    if cfg.solo_mode:
        report.add(WARN, sec, "solo_mode: NON-COMPLIANT, the maintainer approves their own change")
    if cfg.identity == "secrets":
        report.add(WARN, sec, "identity: secrets: DEVIATION, stage isolation rests on token scopes")
    if cfg.gate_mechanism == "workflow":
        report.add(
            WARN,
            sec,
            "gate_mechanism: workflow: DEVIATION, ayasee enforces the prod gate, not GitHub "
            f"(reviewers.prod: {', '.join(cfg.reviewers.prod)}; reviewers.breakglass: "
            f"{', '.join(cfg.reviewers.breakglass) or 'none'})",
        )
    return cfg


# ---- callers ---------------------------------------------------------------------------------


def _secrets(raw: Any) -> dict[str, str | None] | None:
    if raw == "inherit":
        return None
    out: dict[str, str | None] = {}
    if isinstance(raw, dict):
        for name, value in raw.items():
            m = _SECRET_REF.match(str(value).strip())
            out[str(name)] = m.group(1) if m else None
    return out


def _pr_has_paths(doc: dict[str, Any]) -> bool:
    on = doc.get("on", doc.get(True))  # YAML 1.1 reads a bare `on` as True
    if not isinstance(on, dict):
        return False
    pr = on.get("pull_request")
    return isinstance(pr, dict) and ("paths" in pr or "paths-ignore" in pr)


def find_callers(report: Report, root: Path) -> list[Caller]:
    callers: list[Caller] = []
    wf_dir = root / ".github" / "workflows"
    files = sorted([*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")]) if wf_dir.is_dir() else []
    for path in files:
        rel = str(path.relative_to(root))
        try:
            doc = yaml.safe_load(path.read_text())
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            report.add(FAIL, "callers", f"{rel}: cannot read: {type(exc).__name__}")
            continue
        jobs = doc.get("jobs") if isinstance(doc, dict) else None
        if not isinstance(jobs, dict):
            continue
        for name, job in jobs.items():
            uses = job.get("uses") if isinstance(job, dict) else None
            m = _CALLER.match(uses.strip()) if isinstance(uses, str) else None
            if not m:
                continue
            caller = Caller(
                rel, str(name), m["kind"], m["repo"], m["ref"], _secrets(job.get("secrets"))
            )
            caller.pr_paths = m["kind"] == "iac-pr" and _pr_has_paths(doc)
            given = job.get("with")
            caller.inputs = (
                frozenset(str(k) for k in given) if isinstance(given, dict) else frozenset()
            )
            callers.append(caller)
    return callers


def check_callers(report: Report, callers: list[Caller], cfg: Config | None = None) -> None:
    sec = "callers"
    kinds = {c.kind for c in callers}
    workflow_gate = cfg is not None and cfg.gate_mechanism == "workflow"
    for kind, level in (
        ("iac-staged", FAIL),
        ("iac-pr", FAIL),
        ("iac-nightly", WARN),
        ("iac-breakglass", WARN),
    ):
        if kind not in kinds:
            report.add(level, sec, f"no workflow calls {kind}")
    if workflow_gate and "iac-approve" not in kinds:
        report.add(
            FAIL,
            sec,
            "gate_mechanism: workflow, but no workflow calls iac-approve: nothing can apply prod",
        )
    if workflow_gate and "iac-breakglass" in kinds:
        # D15: a break-glass request is approved by reviewers.breakglass with iac-approve
        assert cfg is not None
        if not cfg.reviewers.breakglass:
            # a warning, not a failure: init writes the caller in every mode, and the run itself
            # refuses (fail closed); the emergency path is simply not there until it is listed
            report.add(
                WARN,
                sec,
                "iac-breakglass is called with gate_mechanism: workflow, but reviewers.breakglass "
                "is empty: it refuses every request, so there is no emergency path",
            )
        approvers = [c for c in callers if c.kind == "iac-approve"]
        if approvers and not any("incident" in c.inputs for c in approvers):
            report.add(
                FAIL,
                sec,
                "the iac-approve caller does not pass incident: a break-glass request cannot be "
                "approved (add an incident input, docs/concepts/staged-run.md#the-workflow-gate)",
            )
    if not workflow_gate and cfg is not None and "iac-approve" in kinds:
        report.add(
            WARN,
            sec,
            "iac-approve is called, but it refuses to run with gate_mechanism: environment",
        )
    if workflow_gate and cfg.shared_units and "iac-pr-shared" not in kinds:
        report.add(
            WARN,
            sec,
            "gate_mechanism: workflow: shared_units are not planned in pull requests: no workflow "
            "calls iac-pr-shared (a prod reviewer starts it on main for a pull request's head; "
            "docs/concepts/pr-checks.md#shared-units)",
        )
    if not workflow_gate and cfg is not None and "iac-pr-shared" in kinds:
        report.add(
            WARN,
            sec,
            "iac-pr-shared is called, but it refuses to run with gate_mechanism: environment "
            "(iac-pr plans the shared units in prod-pr-plan, behind its reviewers)",
        )
    for c in callers:
        where = f"{c.file} ({c.job} -> {c.kind})"
        if c.source.split("/")[0] == "OWNER" or set(c.ref) == {"0"}:
            report.add(FAIL, sec, f"{where}: still the OWNER/all-zero placeholder; pin a release")
        elif not _SHA.match(c.ref):
            report.add(FAIL, sec, f"{where}: @{c.ref} is not a full commit SHA; pin a release SHA")
        else:
            report.add(OK, sec, f"{where}: pinned to {c.source}@{c.ref[:12]}")
        if c.secrets is None:
            report.add(WARN, sec, f"{where}: secrets: inherit; pass each secret by name")
        else:
            for name, src in sorted(c.secrets.items()):
                if src is None:
                    report.add(WARN, sec, f"{where}: secret {name} is not ${{{{ secrets.X }}}}")
        if c.pr_paths:
            report.add(
                FAIL,
                sec,
                f"{where}: a paths: filter on pull_request skips the required iac-pr check",
            )
    pins = {(c.source, c.ref) for c in callers if _SHA.match(c.ref)}
    if len(pins) > 1:
        shown = ", ".join(f"{s}@{r[:12]}" for s, r in sorted(pins))
        report.add(FAIL, sec, f"the callers pin different releases: {shown}")


def check_pin(report: Report, gh: GitHub, callers: list[Caller]) -> None:
    """The pinned commit must be a release commit of a version at least this CLI's (issue #32).

    Chosen over comparing ``.ayasee.yaml`` keys with the pinned release: the CLI that scaffolded
    the callers writes templates for its own version, and the workflow inputs, secrets and
    environment names are not all configuration keys, so only the release version bounds them.
    A newer pin is fine (the callers are older than the workflows); an older one is a failure.
    An API failure is SKIP ("could not check"), never a pass."""
    from ayasee import __version__

    sec = "release pin"
    for source, sha in sorted({(c.source, c.ref) for c in callers if _SHA.match(c.ref)}):
        where = f"{source}@{sha[:12]}"
        try:
            pinned = release_version(gh, source, sha)
            ok = version_key(pinned) >= version_key(__version__)
        except NotARelease as exc:
            report.add(FAIL, sec, f"{exc}; pin a release commit of {__version__} or newer")
        except GitHubError as exc:
            report.add(SKIP, sec, f"{where}: could not check the release: {exc}")
        else:
            if ok:
                report.add(
                    OK, sec, f"{where} is the release commit of {pinned} (CLI {__version__})"
                )
            else:
                report.add(
                    FAIL,
                    sec,
                    f"{where} is the release commit of {pinned}, older than this CLI "
                    f"({__version__}): its workflows lack what the callers use; re-run "
                    f"`ayasee init --ref <release commit of {__version__}>` or pin that commit",
                )


def _environment_literal(env: Any) -> str | None:
    """The literal name of a job's ``environment:`` (a string or ``{name: ..}``), or None for none
    or an expression: a name built at run time cannot be read from the file."""
    name = env.get("name") if isinstance(env, dict) else env
    if not isinstance(name, str) or "${{" in name:
        return None
    return name.strip()


def check_shared_environments(report: Report, cfg: Config, root: Path) -> None:
    """Warn when a job that is not an ayasee call names an environment ayasee derives (#31).

    The adopter's own ``deploy-test`` and ``deploy-prod`` workflows are the case: they share the
    environment, so its secrets and its approvals mean two things. Only literal names can be seen.
    """
    sec = "environments"
    try:
        ours = set(environment_names(cfg).values())
    except ConfigError:
        return  # the configuration check already reported the stages
    wf_dir = root / ".github" / "workflows"
    files = sorted([*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")]) if wf_dir.is_dir() else []
    shared: dict[str, list[str]] = {}
    for path in files:
        try:
            doc = yaml.safe_load(path.read_text())
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue  # reported by find_callers
        jobs = doc.get("jobs") if isinstance(doc, dict) else None
        if not isinstance(jobs, dict):
            continue
        for name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            uses = job.get("uses")
            if isinstance(uses, str) and _CALLER.match(uses.strip()):
                continue  # an ayasee caller
            env = _environment_literal(job.get("environment"))
            if env is not None and env in ours:
                shared.setdefault(env, []).append(f"{path.relative_to(root)} (job {name})")
    for env, where in sorted(shared.items()):
        report.add(
            WARN,
            sec,
            f"{env} is also used by {', '.join(where)}, which is not an ayasee caller: its "
            "secrets and approvals would serve both; set environment_prefix (for example 'iac-')"
            " so that ayasee gets environments of its own",
        )
    if ours and not shared and cfg.environment_prefix:
        report.add(OK, sec, f"no other workflow uses {cfg.environment_prefix}* environments")


# ---- GitHub ----------------------------------------------------------------------------------


def _logical_environments_of(kind: str, cfg: Config) -> list[str]:
    pre = [s for s in cfg.stages if s != "prod"]
    if kind == "iac-staged":
        return [*pre, "prod-plan", "prod"]
    if kind == "iac-pr":
        shared = cfg.shared_units and cfg.gate_mechanism == "environment"
        return [pr_plan_environment(cfg.stages)] + (["prod-pr-plan"] if shared else [])
    if kind == "iac-nightly":
        return [f"drift-{s}" for s in cfg.stages]
    workflow_breakglass = cfg.gate_mechanism == "workflow" and bool(cfg.reviewers.breakglass)
    if kind == "iac-approve":
        # a break-glass approval applies in prod-breakglass (D15)
        return ["prod", "prod-breakglass"] if workflow_breakglass else ["prod"]
    if kind == "iac-pr-shared":
        return ["prod-pr-plan"] if cfg.shared_units else []
    if cfg.gate_mechanism == "workflow":
        return ["prod-plan", "prod-breakglass"]  # the request plans in prod-plan
    return ["prod-breakglass"]


def environments_of(kind: str, cfg: Config) -> list[str]:
    """The GitHub environments whose jobs read secrets and variables, per reusable workflow:
    ``environment_prefix`` plus the stage-level name (issue #31)."""
    return [cfg.environment_prefix + e for e in _logical_environments_of(kind, cfg)]


def _plan_key_envs(kind: str, cfg: Config) -> list[str]:
    if kind not in ("iac-staged", "iac-breakglass", "iac-approve"):
        return []
    # every environment of these that plans or applies prod holds the plan key
    keyed = {cfg.environment_prefix + e for e in ("prod-plan", "prod", "prod-breakglass")}
    return [e for e in environments_of(kind, cfg) if e in keyed]


def _reviewers(env: dict[str, Any]) -> tuple[int, bool | None]:
    for rule in env.get("protection_rules") or []:
        if isinstance(rule, dict) and rule.get("type") == "required_reviewers":
            reviewers = rule.get("reviewers")
            prevent = rule.get("prevent_self_review")
            n = len(reviewers) if isinstance(reviewers, list) else 0
            return n, prevent if isinstance(prevent, bool) else None
    return 0, None


def check_github(
    report: Report,
    gh: GitHub,
    repo: str,
    cfg: Config,
    callers: list[Caller],
    bare: bool = False,
) -> None:
    """``bare``: ``gate_mechanism: workflow`` on a plan without deployment branch policies,
    environment secrets or environment variables (GitHub Free, private): the environments carry
    nothing, so the branch policies are not checked and secrets and variables are read at
    repository level (D15)."""
    sec = f"GitHub ({repo})"
    # every decision below is keyed on the GitHub name, prefix included: a literal "prod" here
    # would check the application's own environment and skip the gate of the prefixed one
    pfx = cfg.environment_prefix
    workflow_gate = cfg.gate_mechanism == "workflow"
    gated = set() if workflow_gate else {pfx + e for e in GATED}
    prod_env = pfx + "prod"
    pr_plan = pfx + "prod-pr-plan"
    # environments a pull request's own run plans in, which accept every branch; with the
    # workflow gate prod-pr-plan is used by iac-pr-shared on main and is main-only (D15)
    pr_envs = {pfx + pr_plan_environment(cfg.stages)} | (set() if workflow_gate else {pr_plan})
    wanted: dict[str, None] = {}  # ordered set
    for kind in ("iac-staged", "iac-pr", *sorted({c.kind for c in callers})):
        for env in environments_of(kind, cfg):
            wanted[env] = None
    try:
        found = {e.get("name"): e for e in gh.environments(repo) if isinstance(e, dict)}
    except GitHubError as exc:
        report.add(FAIL, sec, f"cannot list the environments: {exc} (needs an admin's token)")
        return
    present = [e for e in wanted if e in found]
    for env in wanted:
        if env not in found:
            report.add(FAIL, sec, f"environment {env} is missing (run the bootstrap)")
    if present:
        report.add(OK, sec, f"environments present: {', '.join(present)}")
    if workflow_gate:
        _check_reviewer_access(report, gh, repo, cfg, sec)
    if bare:
        report.add(
            WARN,
            sec,
            "no deployment branch policies on this plan: the environments are not restricted to "
            "main, and the secrets are repository secrets that any collaborator with write access "
            "can read from any branch (D15)",
        )
        if any(c.kind == "iac-pr-shared" for c in callers):
            report.add(
                FAIL,
                sec,
                "iac-pr-shared is called, but it refuses to run on this plan: prod-pr-plan cannot "
                "be restricted to main, so any branch could use its credentials, and there are no "
                "environment secrets to keep them apart from the prod apply's. Remove the caller "
                "and any prod-pr-plan credential (D15)",
            )

    for env in present:
        data = found[env]
        if env in gated:
            n, prevent = _reviewers(data)
            if n == 0:
                report.add(FAIL, sec, f"{env}: no required reviewers")
            elif prevent is False and not cfg.solo_mode:
                report.add(FAIL, sec, f"{env}: self-review is allowed but solo_mode is off")
            elif prevent is True and cfg.solo_mode and env == prod_env:
                report.add(
                    WARN, sec, f"{env}: prevents self-review, so a solo maintainer cannot approve"
                )
            else:
                report.add(OK, sec, f"{env}: {n} required reviewer(s)")
        if bare:
            continue
        policy = data.get("deployment_branch_policy")
        if env in pr_envs and env != pr_plan:
            if policy:
                report.add(FAIL, sec, f"{env}: restricted branches; pull-request plans need any")
            continue
        if env in pr_envs:
            continue
        if not isinstance(policy, dict) or policy.get("custom_branch_policies") is not True:
            report.add(FAIL, sec, f"{env}: not restricted to main (deployment branch policy)")
            continue
        try:
            names = sorted(str(p.get("name")) for p in gh.environment_branch_policies(repo, env))
        except GitHubError as exc:
            report.add(FAIL, sec, f"{env}: cannot read the branch policies: {exc}")
            continue
        if names != ["main"]:
            report.add(FAIL, sec, f"{env}: deploys from {names or 'no branch'}, expected [main]")

    if not bare:  # no environment variable, so no marker, on GitHub Free
        _check_ownership(report, gh, repo, present, sec)
    _check_secrets_and_variables(report, gh, repo, cfg, callers, present, sec, bare)

    if any(c.secrets and c.secrets.get("ledger-token") for c in callers):
        try:
            rules = gh.branch_rules(repo, "main")
        except GitHubError as exc:
            if is_plan_error(exc):
                report.add(
                    WARN if bare else FAIL,
                    sec,
                    "this repository's plan has no rulesets, so nothing stops the ledger token "
                    "from pushing to main (see the GitHub plan above)",
                )
            else:
                report.add(FAIL, sec, f"cannot read the rules of main: {exc}")
        else:
            if any(r.get("type") == "pull_request" for r in rules):
                report.add(
                    OK,
                    sec,
                    "main has a pull-request ruleset (the runs check the ledger token's bypass)",
                )
            else:
                report.add(
                    FAIL,
                    sec,
                    "a ledger-token is passed but no ruleset requires pull requests on main: "
                    "the token could push to main",
                )


def _check_reviewer_access(report: Report, gh: GitHub, repo: str, cfg: Config, sec: str) -> None:
    """Every login of ``reviewers.prod`` and ``reviewers.breakglass`` can approve: write access
    or more (D15)."""
    for where, logins in (
        ("reviewers.prod", cfg.reviewers.prod),
        ("reviewers.breakglass", cfg.reviewers.breakglass),
    ):
        for login in logins:
            try:
                perm = gh.collaborator_permission(repo, login)
            except GitHubError as exc:
                report.add(FAIL, sec, f"{where}: cannot read the access of {login}: {exc}")
                continue
            if perm in ("admin", "write"):
                report.add(OK, sec, f"{where}: {login} has {perm} access")
            else:
                report.add(
                    FAIL,
                    sec,
                    f"{where}: {login} has {perm} access; iac-approve refuses a reviewer "
                    "without write access",
                )


MARKER = "AYASEE_MANAGED"


def _check_ownership(report: Report, gh: GitHub, repo: str, present: list[str], sec: str) -> None:
    """Which environments the bootstrap did not create: they have no AYASEE_MANAGED variable.

    A warning, because an environment of an older bootstrap has none either; an application's
    ``test`` or ``prod`` that ayasee shares has none and is the case this exists for (issue #31).
    """
    unmarked = []
    for env in present:
        try:
            have = gh.variable_names(repo, env)
        except GitHubError as exc:
            report.add(FAIL, sec, f"{env}: cannot read the variables: {exc}")
            continue
        if MARKER not in have:
            unmarked.append(env)
    if unmarked:
        report.add(
            WARN,
            sec,
            f"not created by the ayasee bootstrap (no {MARKER} variable): {', '.join(unmarked)}; "
            "if an application also deploys to one of them, set environment_prefix so that "
            "ayasee gets environments of its own",
        )


def _check_secrets_and_variables(
    report: Report,
    gh: GitHub,
    repo: str,
    cfg: Config,
    callers: list[Caller],
    present: list[str],
    sec: str,
    bare: bool = False,
) -> None:
    secrets: dict[str | None, set[str]] = {}
    variables: dict[str | None, set[str]] = {}

    def names(store: dict[str | None, set[str]], env: str | None, read: Any) -> set[str] | None:
        if env not in store:
            try:
                store[env] = read(repo, env)
            except GitHubError as exc:
                what = "the repository" if env is None else env
                report.add(FAIL, sec, f"cannot list the secrets or variables of {what}: {exc}")
                store[env] = set()
                return None
        return store[env]

    # (env, secret) -> what needs it; repository secrets separately
    need_env: dict[tuple[str, str], str] = {}
    need_repo: dict[str, str] = {}
    for c in callers:
        if c.secrets is None:
            continue
        key_secrets = PLAN_KEY_SECRETS.get(cfg.plan_encryption.key_provider, ())
        for name, src in c.secrets.items():
            if src is None:
                continue
            if name in PROVIDER_SECRETS:
                if cfg.identity != "secrets":
                    continue
                envs = environments_of(c.kind, cfg)
            elif name in key_secrets:
                envs = _plan_key_envs(c.kind, cfg)
            elif name in LEDGER_KEY_SECRETS and c.kind in LEDGER_KEY_KINDS:
                envs = environments_of(c.kind, cfg)
            elif name in {s for v in PLAN_KEY_SECRETS.values() for s in v}:
                continue  # another key provider's secret: unused
            else:
                need_repo.setdefault(src, f"{c.file}: {name}")
                continue
            if bare:  # no environment secrets on this plan: one repository secret
                need_repo.setdefault(src, f"{c.file}: {name}")
                continue
            for env in envs:
                need_env.setdefault((env, src), f"{c.kind}: {name}")
    for c in callers:
        if c.kind == "iac-staged" and c.secrets is not None and cfg.identity == "secrets":
            if not PROVIDER_SECRETS & {n for n, s in c.secrets.items() if s}:
                report.add(FAIL, sec, f"{c.file}: identity: secrets, but no provider secret passed")
        if c.kind in ("iac-staged", "iac-breakglass", "iac-approve") and c.secrets is not None:
            for name in PLAN_KEY_SECRETS.get(cfg.plan_encryption.key_provider, ()):
                if not c.secrets.get(name):
                    report.add(FAIL, sec, f"{c.file}: the plan key needs {name}; pass it")
        if c.kind in LEDGER_KEY_KINDS and c.secrets is not None:
            passed = [n for n in LEDGER_KEY_SECRETS if c.secrets.get(n)]
            if len(passed) == 1:
                other = next(n for n in LEDGER_KEY_SECRETS if n not in passed)
                report.add(FAIL, sec, f"{c.file}: {passed[0]} is passed without {other}")
            elif not passed and (cfg.ledger.url or "").startswith("s3://"):
                report.add(
                    FAIL,
                    sec,
                    f"{c.file}: ledger.url is s3://, but the ledger key is not passed "
                    f"({' and '.join(LEDGER_KEY_SECRETS)})",
                )

    repo_secrets = names(secrets, None, gh.secret_names) or set()
    missing = checked = 0
    for (env, src), why in sorted(need_env.items()):
        if env not in present:
            continue  # reported as a missing environment
        checked += 1
        have = names(secrets, env, gh.secret_names)
        if have is None or src in have:
            continue
        missing += 1
        if src in repo_secrets:
            report.add(
                FAIL, sec, f"{env}: {src} is a repository secret; make it an environment secret"
            )
        else:
            report.add(FAIL, sec, f"{env}: secret {src} is missing ({why})")
    for src, why in sorted(need_repo.items()):
        if src not in repo_secrets:
            report.add(
                WARN,
                sec,
                f"secret {src} is not a repository secret ({why}); fine if it is an "
                "organization secret",
            )
    if bare and need_repo:
        report.add(
            WARN,
            sec,
            "every stage reads the same repository secrets on this plan: a provider token for "
            "prod is also handed to the pre-prod jobs, so stage isolation rests on nothing but "
            "the token scopes (D15)",
        )
    if checked and not missing:
        report.add(OK, sec, f"{checked} environment secret(s) in place")

    # variables: the environment's own, else the repository's (organization ones are not seen)
    need_var: dict[tuple[str, str], str] = {}
    kinds = {c.kind for c in callers}
    if not cfg.ledger.url:
        for kind in kinds & {"iac-staged", "iac-nightly"}:
            for env in environments_of(kind, cfg):
                need_var[(env, "AYASEE_LEDGER_URL")] = "no ledger.url in the configuration"
    # the environment gate's break-glass job reads only its environment's variable; the workflow
    # gate's request and approval fall back to ledger.url
    if "iac-breakglass" in kinds and (cfg.gate_mechanism != "workflow" or not cfg.ledger.url):
        need_var[(cfg.environment_prefix + "prod-breakglass", "AYASEE_LEDGER_URL")] = (
            "the break-glass run needs it"
        )
    if cfg.identity == "oidc":
        for kind in kinds:
            for env in environments_of(kind, cfg):
                for var in OIDC_VARIABLES:
                    need_var[(env, var)] = "identity: oidc"
    key_var = PLAN_KEY_VARIABLES.get(cfg.plan_encryption.key_provider)
    if key_var:
        for kind in kinds:
            for env in _plan_key_envs(kind, cfg):
                need_var[(env, key_var)] = f"key_provider {cfg.plan_encryption.key_provider}"
    repo_vars = names(variables, None, gh.variable_names) or set()
    missing = checked = 0
    for (env, var), why in sorted(need_var.items()):
        if env not in present:
            continue  # reported as a missing environment
        checked += 1
        # no environment variables on GitHub Free: the repository's are the ones read
        have = set() if bare else names(variables, env, gh.variable_names)
        if have is None or var in have or var in repo_vars:
            continue
        missing += 1
        report.add(FAIL, sec, f"{env}: variable {var} is missing ({why})")
    if checked and not missing:
        report.add(OK, sec, f"{checked} variable(s) in place")


# ---- GitHub plan (issue #39) -----------------------------------------------------------------

PLAN_SECTION = "GitHub plan"


def check_plan(report: Report, gh: GitHub, repo: str, gate: str = "environment") -> PlanSupport:
    """Can the repository's plan hold the gate? One line, first in the report.

    With the environment gate (``gate``), ``UNSUPPORTED`` and ``UNKNOWN`` fail: the default gate
    rests on environment reviewers, and a plan that is not confirmed is not taken for one that has
    them; the failure (:meth:`PlanSupport.explain`) points to ``gate_mechanism: workflow`` (D15).
    A plan inferred from reviewers already in place (the token cannot read the owner's plan)
    warns.

    With ``gate_mechanism: workflow`` nothing fails here: Pro or Team (no reviewers) is the case
    the mode exists for; Free warns that the prod secrets and ``main`` are open to every writer;
    a plan with reviewers warns that the environment gate would be stronger."""
    support = detect(gh, repo)
    if gate == "workflow":
        report.add(*_workflow_plan_line(support))
        return support
    lines = support.explain()
    if support.verdict == SUPPORTED:
        level = WARN if support.inferred else OK
        if support.inferred:
            lines = [
                f"{lines[0]}; the owner's plan could not be read, so this rests on the "
                "reviewers GitHub reports"
            ]
    else:
        level = FAIL
    report.add(level, PLAN_SECTION, "\n        ".join(lines))
    return support


def is_bare(support: PlanSupport | None) -> bool:
    """A plan known to have no deployment branch policies (GitHub Free, private)."""
    return (
        support is not None
        and support.verdict == UNSUPPORTED
        and BRANCH_POLICIES in support.missing
    )


def _workflow_plan_line(support: PlanSupport) -> tuple[str, str, str]:
    where = f"a {support.visibility} repository" if support.visibility else "this repository"
    plan = f" on the GitHub {support.plan.capitalize()} plan" if support.plan else ""
    if support.verdict == SUPPORTED:
        return (
            WARN,
            PLAN_SECTION,
            f"{where}{plan} has required reviewers on environments: gate_mechanism: environment "
            "is the stronger gate (GitHub enforces it); the workflow gate works here too",
        )
    if is_bare(support):
        return (
            WARN,
            PLAN_SECTION,
            f"{where}{plan} has no environment reviewers, branch policies, environment secrets or "
            "rulesets. gate_mechanism: workflow still makes every prod apply wait for a reviewer "
            "of reviewers.prod and apply only the approved plan, but the prod secrets are "
            "repository secrets any collaborator with write access can read from any branch, "
            "and anyone with write access can push to main: the gate stops mistakes and "
            "unreviewed applies, not a collaborator who means harm (D15). Keep the collaborators "
            "with write access to the reviewers",
        )
    if support.verdict == UNSUPPORTED:
        return (
            OK,
            PLAN_SECTION,
            f"{where}{plan} has no environment reviewers; gate_mechanism: workflow holds the "
            "gate, and the prod environment keeps its main-only branch policy and its secrets "
            "(D15)",
        )
    return (
        WARN,
        PLAN_SECTION,
        f"cannot confirm the plan of {where} ({'; '.join(support.evidence) or 'no evidence'}); "
        "iac-approve checks at run time that prod deploys from main only, or that the plan has "
        "no branch policies at all",
    )


# ---- lock ------------------------------------------------------------------------------------


def check_lock(report: Report, urls: Iterable[str]) -> None:
    from ayasee.s3 import PreconditionFailed, S3Client, S3Error

    sec = "state lock (use_lockfile)"
    for url in urls:
        u = urlparse(url)
        q = {k: v[-1] for k, v in parse_qs(u.query).items()}
        region = q.get("region") or os.environ.get("AWS_REGION") or "us-east-1"
        if u.scheme != "s3" or not u.netloc:
            report.add(FAIL, sec, f"{url}: expected s3://<bucket>?region=..&endpoint=..")
            continue
        bucket = u.netloc
        try:
            client = S3Client(bucket, region, q.get("endpoint") or None)
        except S3Error as exc:
            report.add(FAIL, sec, f"{bucket}: {exc}")
            continue
        created = False
        try:
            client.put(LOCK_KEY, b"{}", if_none_match=True)
            created = True
            try:
                client.put(LOCK_KEY, b"{}", if_none_match=True)
            except PreconditionFailed:
                report.add(OK, sec, f"{bucket}: the second conditional write was refused")
            else:
                report.add(
                    FAIL, sec, f"{bucket}: NO LOCK, the store ignores If-None-Match (two holders)"
                )
        except PreconditionFailed:
            report.add(FAIL, sec, f"{bucket}: inconclusive, {LOCK_KEY} exists; delete it, rerun")
        except S3Error as exc:
            report.add(FAIL, sec, f"{bucket}: inconclusive: {exc}")
        finally:
            if created:
                try:
                    client.delete(LOCK_KEY)
                except S3Error as exc:
                    report.add(WARN, sec, f"{bucket}: could not delete {LOCK_KEY}: {exc}")
            client.close()


# ---- command ---------------------------------------------------------------------------------


def _quiet_gate(path: Path) -> str:
    """gate_mechanism of the configuration, read before the report starts; the configuration
    check reports a file that does not load (the environment gate is then assumed)."""
    try:
        return (
            parse_config_text(path.read_text()).gate_mechanism if path.is_file() else "environment"
        )
    except (ConfigError, OSError, UnicodeDecodeError):
        return "environment"


def _origin_repo(root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    m = _REMOTE.search(proc.stdout.strip()) if proc.returncode == 0 else None
    return m.group(1) if m else None


def run(args: argparse.Namespace) -> Report:
    root = Path(args.root)
    report = Report()
    repo = args.repo or _origin_repo(root)
    support: PlanSupport | None = None
    gate = _quiet_gate(root / args.config_path)
    if repo is None:
        report.add(SKIP, PLAN_SECTION, "no --repo and no github.com origin remote")
    elif not os.environ.get("GITHUB_TOKEN"):
        report.add(SKIP, PLAN_SECTION, "set GITHUB_TOKEN to check what the plan allows")
    else:
        gh = GitHub.from_env()
        try:
            support = check_plan(report, gh, repo, gate)
        finally:
            gh.close()
    cfg = check_config(report, args.config_path, root)
    callers = find_callers(report, root)
    check_callers(report, callers, cfg)
    if cfg is not None:
        check_shared_environments(report, cfg, root)
    sec = "GitHub"
    if cfg is None:
        report.add(SKIP, sec, "the configuration does not load")
    elif support is not None and support.verdict == UNSUPPORTED and gate != "workflow":
        report.add(
            SKIP,
            sec,
            "not checked: this repository's plan cannot hold the settings they need "
            "(GitHub plan above)",
        )
    elif repo is None:
        report.add(SKIP, sec, "no --repo and no github.com origin remote")
    elif not os.environ.get("GITHUB_TOKEN"):
        report.add(SKIP, sec, "set GITHUB_TOKEN (an admin's token: GITHUB_TOKEN=$(gh auth token))")
    else:
        gh = GitHub.from_env()
        try:
            bare = cfg.gate_mechanism == "workflow" and is_bare(support)
            check_github(report, gh, repo, cfg, callers, bare)
        finally:
            gh.close()
    pinned = [c for c in callers if _SHA.match(c.ref)]
    if pinned and os.environ.get("GITHUB_TOKEN"):
        gh = GitHub.from_env()
        try:
            check_pin(report, gh, pinned)
        finally:
            gh.close()
    elif pinned:
        report.add(SKIP, "release pin", "set GITHUB_TOKEN to check the pinned release")
    if args.lock_check:
        check_lock(report, args.lock_check)
    else:
        report.add(
            SKIP,
            "state lock (use_lockfile)",
            "pass --lock-check s3://<bucket>?region=..&endpoint=.. per state bucket",
        )
    return report


def _run(args: argparse.Namespace) -> int:
    try:
        report = run(args)
    except Exception as exc:  # a doctor never ends on a traceback
        print(f"doctor: error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(report.render())
    return report.status


def _setup(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", type=_repo, help="owner/name (default: the origin remote)")
    parser.add_argument("--root", default=".", help="the repository checkout (default: .)")
    parser.add_argument("--config-path", default=DEFAULT_CONFIG, help="relative to --root")
    parser.add_argument(
        "--lock-check",
        action="append",
        metavar="S3_URL",
        help="s3://<bucket>?region=<r>&endpoint=<https://host>: check that the state store "
        "refuses a second conditional write (writes and deletes one object; repeatable)",
    )
    parser.set_defaults(handler=_run)


cli.register("doctor", _setup)
