"""``ayasee import-target``: what an ``iac-import`` run plans, and where (issue #58).

The ``resolve`` job of ``iac-import`` runs this on the dispatched commit before any environment
or credential is used. It prints ``key=value`` lines for ``$GITHUB_OUTPUT``:

- ``environment``: ``<environment_prefix><first stage>-plan``, the pull-request plan
  environment. It accepts every branch and holds read-only credentials, which ``iac-pr`` already
  hands to a pull request's code, so an import run of a branch reaches nothing more.
- ``stage``: the first stage. ``--stage`` may be empty or that stage; **any other stage is
  refused**: its environments are main-only (or write-capable), and a branch's code must not run
  with them. Those imports stay a local ``ayasee import-plan`` step.
- ``only-units``: empty (every unit of the stage) or the plan name of ``--unit``, a relative path
  of plain names (no ``..``, no hidden name, no ``__``) whose ``terragrunt.hcl`` exists under
  ``<units_root>/<stage>``.
- ``units-root``, ``engine``, ``identity``, ``provider-env-names``: from the configuration.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from ayasee import cli
from ayasee.config import DEFAULT_PATH, ConfigError, load_config, pr_plan_environment

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*(?:_[A-Za-z0-9.-]+)*$")


class Refused(Exception):
    pass


def check_unit(unit: str, base: Path) -> str:
    """The plan name of ``unit`` (``/`` written as ``__``), or :class:`Refused`."""
    parts = unit.split("/")
    if len(unit) > 256 or not all(_SEGMENT.fullmatch(p) and len(p) <= 128 for p in parts):
        raise Refused(f"unit {unit!r} is not a relative path of plain names")
    if not (base / unit / "terragrunt.hcl").is_file():
        raise Refused(f"unit {unit!r}: no {base / unit / 'terragrunt.hcl'}")
    return "__".join(parts)


def target(config_path: Path, stage: str, unit: str) -> dict[str, str]:
    cfg = load_config(config_path)
    environment = cfg.environment_prefix + pr_plan_environment(cfg.stages)
    first = cfg.stages[0]
    if stage not in ("", first):
        raise Refused(
            f"stage {stage!r}: iac-import plans the first stage ({first}) only, with the "
            f"read-only credentials of {environment}. The other stages' environments deploy from "
            "main only and must not run a branch's code: run `ayasee import-plan` locally with "
            "that stage's plan credentials (docs/guides/adopt.md)"
        )
    only = check_unit(unit, Path(cfg.units_root) / first) if unit else ""
    return {
        "environment": environment,
        "stage": first,
        "units-root": cfg.units_root,
        "engine": cfg.engine,
        "identity": cfg.identity,
        "provider-env-names": ",".join(cfg.provider_env),
        "only-units": only,
    }


def _run(args: argparse.Namespace) -> int:
    try:
        out = target(Path(args.path), args.stage, args.unit)
    except ConfigError as exc:
        print(f"ayasee: config error: {exc}", file=sys.stderr)
        return 1
    except Refused as exc:
        print(f"import-target: {exc}", file=sys.stderr)
        return 1
    for key, value in out.items():
        print(f"{key}={value}")
    return 0


def _setup(parser: argparse.ArgumentParser) -> None:
    parser.description = "Check the stage and unit of an iac-import run; print its outputs."
    parser.add_argument("--path", default=str(DEFAULT_PATH), help="the configuration file")
    parser.add_argument("--stage", default="", help="empty or the first stage")
    parser.add_argument("--unit", default="", help="empty (every unit) or a unit path")
    parser.set_defaults(handler=_run)


cli.register("import-target", _setup)
