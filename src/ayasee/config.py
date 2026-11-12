"""``.ayasee.yaml``: strict, fail-closed configuration with compliant defaults (D2, D9)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ayasee import cli

DEFAULT_PATH = Path(".ayasee.yaml")
NON_COMPLIANT = (
    "NON-COMPLIANT: solo_mode disables independent approval (DORA RTS 2024/1774 Art. 17(1)(b))"
)
DEVIATION_SECRETS = (
    "DEVIATION: identity: secrets stores provider API tokens as GitHub environment secrets "
    "instead of OIDC federation (D7)"
)
ENGINES = ("opentofu", "terraform")
GATES = ("always", "risky")
IDENTITIES = ("oidc", "secrets")
# Wired plan-encryption key providers (D1); openbao, aws_kms and gcp_kms are not yet.
KEY_PROVIDERS = ("azure_vault", "pbkdf2")
# Stage names become GitHub environment names, paths and labels (same rule as sg_check_env).
STAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# Environments with a fixed role that a pre-prod stage of iac-staged must never run in.
RESERVED_ENVIRONMENTS = frozenset({"prod-plan", "prod-breakglass", "dev-plan"})


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
    units_root: str = "stages"
    ledger: LedgerConfig = field(default_factory=LedgerConfig)
    gate: str = "always"
    solo_mode: bool = False
    allow_cascade: bool = False
    extra_rules: list[str] = field(default_factory=list)
    drift_noise: dict[str, list[str]] = field(default_factory=dict)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    identity: str = "oidc"
    plan_encryption: PlanEncryptionConfig = field(default_factory=PlanEncryptionConfig)

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
    if "units_root" in top:
        cfg.units_root = _str(top["units_root"], "units_root")
    if "ledger" in top:
        section = _mapping(top["ledger"], "ledger", {"url"})
        if section.get("url") is not None:
            cfg.ledger.url = _str(section["url"], "ledger.url")
    if "gate" in top:
        cfg.gate = _literal(top["gate"], "gate", GATES)
    if "solo_mode" in top:
        cfg.solo_mode = _bool(top["solo_mode"], "solo_mode")
    if "allow_cascade" in top:
        cfg.allow_cascade = _bool(top["allow_cascade"], "allow_cascade")
    if "extra_rules" in top:
        cfg.extra_rules = _str_list(top["extra_rules"], "extra_rules")
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


def staged_layout(stages: list[str], start_stage: str) -> dict[str, str]:
    """Map ``stages`` onto the fixed jobs of ``iac-staged`` (issue #1), or raise ConfigError.

    ``iac-staged`` has two pre-prod job slots, ``dev`` and ``uat``. Three stages ``[a, b, prod]``
    use both; two stages ``[b, prod]`` use only the ``uat`` slot, so the job just before
    ``prod-plan`` always runs the stage just before prod and the guard of ``prod-plan`` chains from
    it. ``start_stage`` is empty (the first stage), a pre-prod stage name, or ``prod-plan``.
    The returned mapping is written to ``$GITHUB_OUTPUT`` as ``key=value`` lines.
    """
    if len(stages) not in (2, 3):
        raise ConfigError(
            f"stages: iac-staged runs two or three stages ([<pre-prod>, prod] or "
            f"[<first>, <pre-prod>, prod]), got {len(stages)}"
        )
    if stages[-1] != "prod":
        raise ConfigError("stages: iac-staged needs prod as the last stage")
    pre = stages[:-1]
    for name in pre:
        if not STAGE_NAME.match(name):
            raise ConfigError(f"stages: invalid stage name {name!r} (use [a-z0-9][a-z0-9_-]*)")
        if name in RESERVED_ENVIRONMENTS or name.startswith("drift-") or name == "prod":
            raise ConfigError(f"stages: {name!r} is a reserved environment name")
    first = pre[0] if len(pre) == 2 else ""
    preprod = pre[-1]
    start = start_stage or pre[0]
    if start not in (*pre, "prod-plan"):
        raise ConfigError(
            f"start-stage must be empty, one of {', '.join(pre)} or prod-plan; got {start!r}"
        )
    run_dev = bool(first) and start == first
    run_uat = start != "prod-plan"
    return {
        "run-dev": "true" if run_dev else "false",
        "run-uat": "true" if run_uat else "false",
        "first-stage": first,
        "pre-prod-stage": preprod,
        "order": ",".join(stages),
    }


def labels(cfg: Config) -> list[str]:
    """The opt-ins that weaken a compliant default, one line each, for run summaries.

    ``solo_mode`` disables independent approval (D2) and ``identity: secrets`` stores provider
    tokens instead of using OIDC (D7). The same lines go to stderr on every load.
    """
    out = []
    if cfg.solo_mode:
        out.append(NON_COMPLIANT)
    if cfg.identity == "secrets":
        out.append(DEVIATION_SECRETS)
    return out


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
    lab = sub.add_parser(
        "labels",
        help="print one line per opt-in that weakens a compliant default (solo_mode, identity)",
    )
    lab.add_argument("--path", default=str(DEFAULT_PATH))
    lab.set_defaults(handler=_labels)


cli.register("config", _setup)
