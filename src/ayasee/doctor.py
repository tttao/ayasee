"""``ayasee doctor``: what the first run of a repository needs, checked before it runs (issue #11).

Run it locally, at the root of the adopting repository. It prints one list of what is missing
instead of failed jobs found one at a time:

- **configuration**: ``.ayasee.yaml`` loads, uses only supported settings, fits the staged job
  slots, matches ``shared_units`` on disk, and has a directory per stage under ``units_root``;
- **callers**: the workflows under ``.github/workflows`` that call the ayasee reusable workflows
  pin one full release SHA (no ``OWNER`` placeholder, no all-zero SHA), the ``iac-staged`` and
  ``iac-pr`` callers exist, and the pull-request caller has no ``paths:`` filter;
- **GitHub** (with ``GITHUB_TOKEN``, read-only): every environment the callers use exists; prod,
  prod-breakglass and prod-pr-plan have required reviewers and prevent self-review (unless
  ``solo_mode``); the environments are restricted to ``main`` except the pull-request plan ones;
  each secret a caller passes exists where its jobs read it (provider and plan-key secrets as
  environment secrets of each stage environment, never at repository level); the variables the
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
    parse_config_text,
    pr_plan_environment,
    shared_layout_problems,
    staged_layout,
    unsupported,
)
from ayasee.github import GitHub, GitHubError
from ayasee.maintip import _repo

OK, WARN, FAIL, SKIP = "ok", "warn", "FAIL", "skip"

_CALLER = re.compile(
    r"^(?P<repo>[^/@\s]+/[^/@\s]+)/\.github/workflows/"
    r"(?P<kind>iac-(?:staged|pr|nightly|breakglass))\.ya?ml@(?P<ref>\S+)$"
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
}
PLAN_KEY_VARIABLES = {"azure_vault": "AYASEE_KEY_VAULT_KEY_ID", "aws_kms": "AYASEE_KMS_KEY_ID"}
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
            callers.append(caller)
    return callers


def check_callers(report: Report, callers: list[Caller]) -> None:
    sec = "callers"
    kinds = {c.kind for c in callers}
    for kind, level in (
        ("iac-staged", FAIL),
        ("iac-pr", FAIL),
        ("iac-nightly", WARN),
        ("iac-breakglass", WARN),
    ):
        if kind not in kinds:
            report.add(level, sec, f"no workflow calls {kind}")
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


# ---- GitHub ----------------------------------------------------------------------------------


def environments_of(kind: str, cfg: Config) -> list[str]:
    """The environments whose jobs read secrets and variables, per reusable workflow."""
    pre = [s for s in cfg.stages if s != "prod"]
    if kind == "iac-staged":
        return [*pre, "prod-plan", "prod"]
    if kind == "iac-pr":
        return [pr_plan_environment(cfg.stages)] + (["prod-pr-plan"] if cfg.shared_units else [])
    if kind == "iac-nightly":
        return [f"drift-{s}" for s in cfg.stages]
    return ["prod-breakglass"]


def _plan_key_envs(kind: str) -> list[str]:
    return {"iac-staged": ["prod-plan", "prod"], "iac-breakglass": ["prod-breakglass"]}.get(
        kind, []
    )


def _reviewers(env: dict[str, Any]) -> tuple[int, bool | None]:
    for rule in env.get("protection_rules") or []:
        if isinstance(rule, dict) and rule.get("type") == "required_reviewers":
            reviewers = rule.get("reviewers")
            prevent = rule.get("prevent_self_review")
            n = len(reviewers) if isinstance(reviewers, list) else 0
            return n, prevent if isinstance(prevent, bool) else None
    return 0, None


def check_github(report: Report, gh: GitHub, repo: str, cfg: Config, callers: list[Caller]) -> None:
    sec = f"GitHub ({repo})"
    pr_envs = {pr_plan_environment(cfg.stages), "prod-pr-plan"}
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

    for env in present:
        data = found[env]
        if env in GATED:
            n, prevent = _reviewers(data)
            if n == 0:
                report.add(FAIL, sec, f"{env}: no required reviewers")
            elif prevent is False and not cfg.solo_mode:
                report.add(FAIL, sec, f"{env}: self-review is allowed but solo_mode is off")
            elif prevent is True and cfg.solo_mode and env == "prod":
                report.add(
                    WARN, sec, f"{env}: prevents self-review, so a solo maintainer cannot approve"
                )
            else:
                report.add(OK, sec, f"{env}: {n} required reviewer(s)")
        policy = data.get("deployment_branch_policy")
        if env in pr_envs and env != "prod-pr-plan":
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

    _check_secrets_and_variables(report, gh, repo, cfg, callers, present, sec)

    if any(c.secrets and c.secrets.get("ledger-token") for c in callers):
        try:
            rules = gh.branch_rules(repo, "main")
        except GitHubError as exc:
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


def _check_secrets_and_variables(
    report: Report,
    gh: GitHub,
    repo: str,
    cfg: Config,
    callers: list[Caller],
    present: list[str],
    sec: str,
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
                envs = _plan_key_envs(c.kind)
            elif name in {s for v in PLAN_KEY_SECRETS.values() for s in v}:
                continue  # another key provider's secret: unused
            else:
                need_repo.setdefault(src, f"{c.file}: {name}")
                continue
            for env in envs:
                need_env.setdefault((env, src), f"{c.kind}: {name}")
    for c in callers:
        if c.kind == "iac-staged" and c.secrets is not None and cfg.identity == "secrets":
            if not PROVIDER_SECRETS & {n for n, s in c.secrets.items() if s}:
                report.add(FAIL, sec, f"{c.file}: identity: secrets, but no provider secret passed")
        if c.kind in ("iac-staged", "iac-breakglass") and c.secrets is not None:
            for name in PLAN_KEY_SECRETS.get(cfg.plan_encryption.key_provider, ()):
                if not c.secrets.get(name):
                    report.add(FAIL, sec, f"{c.file}: the plan key needs {name}; pass it")

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
    if checked and not missing:
        report.add(OK, sec, f"{checked} environment secret(s) in place")

    # variables: the environment's own, else the repository's (organization ones are not seen)
    need_var: dict[tuple[str, str], str] = {}
    kinds = {c.kind for c in callers}
    if not cfg.ledger.url:
        for kind in kinds & {"iac-staged", "iac-nightly"}:
            for env in environments_of(kind, cfg):
                need_var[(env, "AYASEE_LEDGER_URL")] = "no ledger.url in the configuration"
    if "iac-breakglass" in kinds:
        need_var[("prod-breakglass", "AYASEE_LEDGER_URL")] = "the break-glass run needs it"
    if cfg.identity == "oidc":
        for kind in kinds:
            for env in environments_of(kind, cfg):
                for var in OIDC_VARIABLES:
                    need_var[(env, var)] = "identity: oidc"
    key_var = PLAN_KEY_VARIABLES.get(cfg.plan_encryption.key_provider)
    if key_var:
        for kind in kinds:
            for env in _plan_key_envs(kind):
                need_var[(env, key_var)] = f"key_provider {cfg.plan_encryption.key_provider}"
    repo_vars = names(variables, None, gh.variable_names) or set()
    missing = checked = 0
    for (env, var), why in sorted(need_var.items()):
        if env not in present:
            continue  # reported as a missing environment
        checked += 1
        have = names(variables, env, gh.variable_names)
        if have is None or var in have or var in repo_vars:
            continue
        missing += 1
        report.add(FAIL, sec, f"{env}: variable {var} is missing ({why})")
    if checked and not missing:
        report.add(OK, sec, f"{checked} variable(s) in place")


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
    cfg = check_config(report, args.config_path, root)
    callers = find_callers(report, root)
    check_callers(report, callers)
    repo = args.repo or _origin_repo(root)
    sec = "GitHub"
    if cfg is None:
        report.add(SKIP, sec, "the configuration does not load")
    elif repo is None:
        report.add(SKIP, sec, "no --repo and no github.com origin remote")
    elif not os.environ.get("GITHUB_TOKEN"):
        report.add(SKIP, sec, "set GITHUB_TOKEN (an admin's token: GITHUB_TOKEN=$(gh auth token))")
    else:
        gh = GitHub.from_env()
        try:
            check_github(report, gh, repo, cfg, callers)
        finally:
            gh.close()
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
