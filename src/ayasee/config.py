"""``.ayasee.yaml``: strict, fail-closed configuration with compliant defaults (D2, D9)."""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ayasee import cli
from ayasee.ledger import names_s3_endpoint
from ayasee.providerenv import refusal as provider_env_refusal

DEFAULT_PATH = Path(".ayasee.yaml")
NON_COMPLIANT = (
    "NON-COMPLIANT: solo_mode disables independent approval (DORA RTS 2024/1774 Art. 17(1)(b))"
)
DEVIATION_SECRETS = (
    "DEVIATION: identity: secrets stores provider API tokens as GitHub environment secrets "
    "instead of OIDC federation (D7); stages are isolated only as far as the token scopes are "
    "(a pre-prod token whose scope covers prod objects, e.g. a shared DNS zone, can change them "
    "without the prod gate)"
)
ENGINES = ("opentofu", "terraform")
GATES = ("always", "risky")
IDENTITIES = ("oidc", "secrets")
# Wired plan-encryption key providers (D1); gcp_kms and openbao are not yet (issue #10: neither
# has a credential path into the prod jobs).
KEY_PROVIDERS = ("azure_vault", "pbkdf2", "aws_kms")
# Stage names become GitHub environment names, paths and labels (same rule as sg_check_env).
STAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# Environments with a fixed role that a pre-prod stage must never be called. Besides these, every
# name that starts with drift- or ends in -plan is reserved: prod-plan, prod-pr-plan and
# <first stage>-plan, the pull-request plan environment (issue #11), which accepts every branch.
# prod-pr is reserved because, as a first stage, its PR plan environment would be prod-pr-plan.
RESERVED_ENVIRONMENTS = frozenset({"prod", "prod-pr", "prod-breakglass"})
PLAN_SUFFIX = "-plan"
# a unit name as the plan action writes it: the path below <units_root>/<stage>, / written __
_UNIT_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


class ConfigError(Exception):
    """The configuration file is invalid."""


@dataclass
class LedgerConfig:
    url: str | None = None


@dataclass
class NotifyConfig:
    teams: bool = True


@dataclass
class PlanEncryptionConfig:
    key_provider: str = "azure_vault"


@dataclass
class Config:
    stages: list[str] = field(default_factory=lambda: ["dev", "uat", "prod"])
    engine: str = "opentofu"
    terragrunt: bool = True
    iac_root: str = "."
    units_root: str = "stages"
    ledger: LedgerConfig = field(default_factory=LedgerConfig)
    gate: str = "always"
    solo_mode: bool = False
    allow_cascade: bool = False
    extra_rules: list[str] = field(default_factory=list)
    shared_units: list[str] = field(default_factory=list)
    drift_noise: dict[str, list[str]] = field(default_factory=dict)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    identity: str = "oidc"
    plan_encryption: PlanEncryptionConfig = field(default_factory=PlanEncryptionConfig)
    provider_env: list[str] = field(default_factory=list)

    @property
    def binding_mode(self) -> str:
        """D1 for OpenTofu (encrypted plan), D1(b) for plain Terraform (re-plan)."""
        return "encrypted_plan" if self.engine == "opentofu" else "replan"


def _mapping(value: Any, where: str, allowed: set[str]) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected a mapping, got {type(value).__name__}")
    unknown = sorted(str(k) for k in value if k not in allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s): {', '.join(unknown)}")
    return value


def _str(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{where}: expected a string, got {type(value).__name__}")
    return value


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{where}: expected true or false, got {value!r}")
    return value


def _literal(value: Any, where: str, choices: tuple[str, ...]) -> str:
    if value not in choices:
        raise ConfigError(f"{where}: must be one of {', '.join(choices)}; got {value!r}")
    return value


def _str_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list):
        raise ConfigError(f"{where}: expected a list, got {type(value).__name__}")
    return [_str(v, f"{where}[{i}]") for i, v in enumerate(value)]


def _rel_dir(value: Any, where: str) -> str:
    """A repository-relative directory, normalised (``./a/b/`` gives ``a/b``; the root is ``.``)."""
    text = _str(value, where)
    if not text:
        raise ConfigError(f"{where}: must not be empty")
    if text.startswith("/"):
        raise ConfigError(f"{where}: must be relative to the repository root, got {text!r}")
    norm = posixpath.normpath(text)
    if norm == ".." or norm.startswith("../"):
        raise ConfigError(f"{where}: must stay inside the repository, got {text!r}")
    return norm


def _inside(path: str, root: str) -> bool:
    return root == "." or path == root or path.startswith(root + "/")


def _parse(raw: Any) -> Config:
    top = _mapping(raw, "config", set(Config.__dataclass_fields__))
    cfg = Config()
    if "stages" in top:
        stages = _str_list(top["stages"], "stages")
        if not stages or any(not s for s in stages):
            raise ConfigError("stages: must be a non-empty list of non-empty names")
        if len(set(stages)) != len(stages):
            raise ConfigError("stages: names must be unique")
        if "prod" not in stages:
            raise ConfigError("stages: must contain 'prod'")
        cfg.stages = stages
    if "engine" in top:
        cfg.engine = _literal(top["engine"], "engine", ENGINES)
    if "terragrunt" in top:
        cfg.terragrunt = _bool(top["terragrunt"], "terragrunt")
    if "iac_root" in top:
        cfg.iac_root = _rel_dir(top["iac_root"], "iac_root")
    if "units_root" in top:
        cfg.units_root = _rel_dir(top["units_root"], "units_root")
    if cfg.units_root == cfg.iac_root or not _inside(cfg.units_root, cfg.iac_root):
        message = (
            f"units_root: must be a directory inside iac_root ({cfg.iac_root!r}), "
            f"got {cfg.units_root!r}"
        )
        joined = posixpath.join(cfg.iac_root, cfg.units_root)
        top_dir = cfg.units_root.split("/")[0]
        if cfg.iac_root != "." and cfg.units_root != "." and top_dir != cfg.iac_root.split("/")[0]:
            # the usual mistake: units_root written relative to iac_root (or left at its default)
            message += (
                "; units_root is relative to the repository root, not to iac_root: "
                f"if it was written relative to iac_root, write units_root: {joined}"
            )
        raise ConfigError(message)
    if "ledger" in top:
        section = _mapping(top["ledger"], "ledger", {"url"})
        if section.get("url") is not None:
            cfg.ledger.url = _str(section["url"], "ledger.url")
            if names_s3_endpoint(cfg.ledger.url):
                # issue #10: the file is code a pull request can change, and the endpoint
                # receives the signed ledger requests of the job
                raise ConfigError(
                    "ledger.url: an s3:// URL with endpoint= must be set in the AYASEE_LEDGER_URL "
                    "variable of the environment, never in the configuration file"
                )
    if "gate" in top:
        cfg.gate = _literal(top["gate"], "gate", GATES)
    if "solo_mode" in top:
        cfg.solo_mode = _bool(top["solo_mode"], "solo_mode")
    if "allow_cascade" in top:
        cfg.allow_cascade = _bool(top["allow_cascade"], "allow_cascade")
    if "extra_rules" in top:
        cfg.extra_rules = _str_list(top["extra_rules"], "extra_rules")
    if "shared_units" in top:
        shared = _str_list(top["shared_units"], "shared_units")
        if any(not _UNIT_NAME.fullmatch(s) for s in shared):
            raise ConfigError(
                "shared_units: each entry is a prod unit name (its path below "
                "<units_root>/prod, with / written __)"
            )
        if len(set(shared)) != len(shared):
            raise ConfigError("shared_units: names must be unique")
        cfg.shared_units = shared
    if "drift_noise" in top:
        noise = top["drift_noise"]
        if not isinstance(noise, dict):
            raise ConfigError(f"drift_noise: expected a mapping, got {type(noise).__name__}")
        cfg.drift_noise = {
            _str(k, "drift_noise key"): _str_list(v, f"drift_noise.{k}") for k, v in noise.items()
        }
    if "notify" in top:
        section = _mapping(top["notify"], "notify", {"teams"})
        if "teams" in section:
            cfg.notify.teams = _bool(section["teams"], "notify.teams")
    if "identity" in top:
        cfg.identity = _literal(top["identity"], "identity", IDENTITIES)
    if "plan_encryption" in top:
        section = _mapping(top["plan_encryption"], "plan_encryption", {"key_provider"})
        if "key_provider" in section:
            cfg.plan_encryption.key_provider = _literal(
                section["key_provider"], "plan_encryption.key_provider", KEY_PROVIDERS
            )
    if "provider_env" in top:
        names = _str_list(top["provider_env"], "provider_env")
        for name in names:
            reason = provider_env_refusal(name)
            if reason:
                raise ConfigError(f"provider_env: {name!r}: {reason}")
        if len(set(names)) != len(names):
            raise ConfigError("provider_env: names must be unique")
        if names and cfg.identity != "secrets":
            raise ConfigError(
                "provider_env: only with identity: secrets (the oidc identity passes no "
                "provider secret)"
            )
        cfg.provider_env = names
    return cfg


def load_config(path: Path = DEFAULT_PATH) -> Config:
    """Load ``path``; a missing file yields the defaults. Raises :class:`ConfigError`."""
    path = Path(path)
    if not path.exists():
        return Config()
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path}: cannot read: {type(exc).__name__}") from exc
    cfg = _parse({} if raw is None else raw)
    for line in labels(cfg):
        print(line, file=sys.stderr)
    return cfg


# Pre-prod job slots of iac-staged: jobs stage-1 .. stage-4 (issue #10).
PRE_PROD_SLOTS = 4


def _check_preprod_name(name: str) -> None:
    """Refuse a pre-prod stage name that is not a safe GitHub environment name or that is one of
    the environments with a fixed role (prod, prod-pr, prod-breakglass, drift-*, *-plan)."""
    if not STAGE_NAME.match(name):
        raise ConfigError(f"stages: invalid stage name {name!r} (use [a-z0-9][a-z0-9_-]*)")
    if name in RESERVED_ENVIRONMENTS or name.startswith("drift-") or name.endswith(PLAN_SUFFIX):
        raise ConfigError(f"stages: {name!r} is a reserved environment name")


def pr_plan_environment(stages: list[str]) -> str:
    """The GitHub environment ``iac-pr`` plans a pull request in: ``<first stage>-plan`` (issue
    #11), so ``[test, prod]`` plans in ``test-plan`` and the default stages in ``dev-plan``.

    That environment accepts every branch, so its name must never be one with a fixed role: the
    first stage must be a pre-prod stage, and no pre-prod stage may itself be called ``*-plan``
    (``[dev, dev-plan, prod]`` would give the PR plan and the second stage one environment).
    """
    pre = [s for s in stages if s != "prod"]
    if not pre or stages[0] == "prod":
        raise ConfigError(
            "stages: a pull request plans the first stage, which must be a pre-prod stage "
            f"(got {stages!r})"
        )
    for name in pre:
        _check_preprod_name(name)
    return stages[0] + PLAN_SUFFIX


def staged_layout(stages: list[str], start_stage: str) -> dict[str, str]:
    """Map ``stages`` onto the fixed jobs of ``iac-staged`` (issues #1, #10), or raise ConfigError.

    ``iac-staged`` has :data:`PRE_PROD_SLOTS` pre-prod job slots, ``stage-1`` to ``stage-4``, each
    needing the one before it. The pre-prod stages fill the slots right-aligned: the last slot
    always runs the stage just before prod, and unused slots come first. ``start_stage`` (empty
    for the first stage, a pre-prod stage name, or ``prod-plan``) skips the slots before it, so
    the skipped slots are always a prefix and every slot after the first running one runs. The
    guard of each job chains from the stage before it in ``order``, whatever slot that is.
    The returned mapping is written to ``$GITHUB_OUTPUT`` as ``key=value`` lines.
    """
    if not 2 <= len(stages) <= PRE_PROD_SLOTS + 1:
        raise ConfigError(
            f"stages: iac-staged runs 2 to {PRE_PROD_SLOTS + 1} stages (one to "
            f"{PRE_PROD_SLOTS} pre-prod stages, then prod), got {len(stages)}"
        )
    if stages[-1] != "prod":
        raise ConfigError("stages: iac-staged needs prod as the last stage")
    pre = stages[:-1]
    for name in pre:
        _check_preprod_name(name)
    start = start_stage or pre[0]
    if start not in (*pre, "prod-plan"):
        raise ConfigError(
            f"start-stage must be empty, one of {', '.join(pre)} or prod-plan; got {start!r}"
        )
    slots = [""] * (PRE_PROD_SLOTS - len(pre)) + pre
    first_run = len(slots) if start == "prod-plan" else slots.index(start)
    out = {
        f"run-stage-{i}": "true" if i - 1 >= first_run else "false"
        for i in range(1, PRE_PROD_SLOTS + 1)
    }
    out.update({f"stage-{i}": name for i, name in enumerate(slots, 1)})
    out["order"] = ",".join(stages)
    return out


def label_keys(cfg: Config) -> list[dict[str, str]]:
    """The opt-ins that weaken a compliant default, as ``{kind, setting}`` (the cockpit's form).

    ``solo_mode`` disables independent approval (D2, kind ``non-compliant``) and
    ``identity: secrets`` stores provider tokens instead of using OIDC (D7, kind ``deviation``).
    """
    out = []
    if cfg.solo_mode:
        out.append({"kind": "non-compliant", "setting": "solo_mode"})
    if cfg.identity == "secrets":
        out.append({"kind": "deviation", "setting": "identity"})
    return out


_LABEL_TEXT = {"solo_mode": NON_COMPLIANT, "identity": DEVIATION_SECRETS}


def labels(cfg: Config) -> list[str]:
    """The same opt-ins as :func:`label_keys`, one line each, for run summaries.

    The same lines go to stderr on every load.
    """
    return [_LABEL_TEXT[k["setting"]] for k in label_keys(cfg)]


def parse_config_text(text: str) -> Config:
    """Parse the text of a configuration file without printing anything (the cockpit reads
    other repositories' files). Raises :class:`ConfigError`."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"cannot parse: {type(exc).__name__}") from exc
    return _parse({} if raw is None else raw)


def _labels(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(Path(args.path))
    except ConfigError as exc:
        print(f"ayasee: config error: {exc}", file=sys.stderr)
        return 1
    for line in labels(cfg):
        print(line)
    return 0


def _staged(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(Path(args.path))
        layout = staged_layout(cfg.stages, args.start_stage)
    except ConfigError as exc:
        print(f"ayasee: config error: {exc}", file=sys.stderr)
        return 1
    for key, value in layout.items():
        print(f"{key}={value}")
    return 0


def _pr_plan_environment(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(Path(args.path))
        name = pr_plan_environment(cfg.stages)
    except ConfigError as exc:
        print(f"ayasee: config error: {exc}", file=sys.stderr)
        return 1
    print(name)
    return 0


def _run(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(Path(args.path))
    except ConfigError as exc:
        print(f"ayasee: config error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({**asdict(cfg), "binding_mode": cfg.binding_mode}, indent=2))
    return 0


def unsupported(cfg: Config) -> list[str]:
    """Settings the parser accepts but no workflow implements yet (R35, R73), one message each.

    The reusable workflows refuse such a configuration in their first job instead of silently
    running something else (for example the OpenTofu encrypted-plan path for ``engine: terraform``).
    """
    out = []
    if cfg.gate == "risky":
        out.append("gate: risky is not supported yet")
    if cfg.engine == "terraform":
        out.append("engine: not supported yet (engine: terraform; only opentofu runs today)")
    if not cfg.terragrunt:
        out.append("terragrunt: not supported yet (terragrunt: false; Terragrunt layouts only)")
    if cfg.extra_rules:
        out.append("extra_rules: not supported yet (no workflow reads extra rule files)")
    if not cfg.notify.teams:
        out.append(
            "notify.teams: not supported yet (notify.teams: false; leave the teams-webhook "
            "secret unset to disable Teams notifications)"
        )
    return out


def _check_supported(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(Path(args.path))
    except ConfigError as exc:
        print(f"ayasee: config error: {exc}", file=sys.stderr)
        return 1
    problems = unsupported(cfg)
    for msg in problems:
        print(msg, file=sys.stderr)
    return 1 if problems else 0


def shared_layout_problems(cfg: Config, repo: Path = Path(".")) -> list[str]:
    """Why ``shared_units`` does not match the tree under ``repo``, one message each.

    A shared unit serves every stage, so it lives under ``<units_root>/prod`` (a directory with a
    ``terragrunt.hcl``) and under no other stage: a copy in another stage would be a second object.
    """
    out = []
    for name in cfg.shared_units:
        rel = name.replace("__", "/")
        prod = repo / cfg.units_root / "prod" / rel
        if not (prod / "terragrunt.hcl").is_file():
            out.append(f"shared_units: {name}: no unit at {cfg.units_root}/prod/{rel}")
        for stage in cfg.stages:
            if stage != "prod" and (repo / cfg.units_root / stage / rel).exists():
                out.append(
                    f"shared_units: {name}: also present in stage {stage} "
                    f"({cfg.units_root}/{stage}/{rel}); a shared unit exists in prod only"
                )
    return out


def _check_shared(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(Path(args.path))
    except ConfigError as exc:
        print(f"ayasee: config error: {exc}", file=sys.stderr)
        return 1
    problems = shared_layout_problems(cfg)
    for msg in problems:
        print(msg, file=sys.stderr)
    return 1 if problems else 0


def _setup(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="config_command", metavar="<action>", required=True)
    show = sub.add_parser("show", help="print the resolved configuration as JSON")
    show.add_argument("--path", default=str(DEFAULT_PATH))
    show.set_defaults(handler=_run)
    check = sub.add_parser(
        "check-supported",
        help="exit 1, one line per setting, when the configuration uses an unsupported setting",
    )
    check.add_argument("--path", default=str(DEFAULT_PATH))
    check.set_defaults(handler=_check_supported)
    staged = sub.add_parser(
        "staged",
        help="print the iac-staged job layout (key=value lines) or exit 1 for an unsupported one",
    )
    staged.add_argument("--path", default=str(DEFAULT_PATH))
    staged.add_argument("--start-stage", default="", help="empty, a pre-prod stage or prod-plan")
    staged.set_defaults(handler=_staged)
    prplan = sub.add_parser(
        "pr-plan-environment",
        help="print the environment of the pull-request plan (<first stage>-plan) or exit 1",
    )
    prplan.add_argument("--path", default=str(DEFAULT_PATH))
    prplan.set_defaults(handler=_pr_plan_environment)
    lab = sub.add_parser(
        "labels",
        help="print one line per opt-in that weakens a compliant default (solo_mode, identity)",
    )
    lab.add_argument("--path", default=str(DEFAULT_PATH))
    lab.set_defaults(handler=_labels)
    shared = sub.add_parser(
        "check-shared",
        help="exit 1, one line per problem, when shared_units does not match the units on disk",
    )
    shared.add_argument("--path", default=str(DEFAULT_PATH))
    shared.set_defaults(handler=_check_shared)


cli.register("config", _setup)
