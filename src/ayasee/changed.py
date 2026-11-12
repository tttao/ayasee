"""``ayasee changed-units``: which units of one environment a pull request changes.

Prints a comma-separated list of unit names (a unit's name is its path below
``<units-root>/<env>`` with ``/`` written ``__``), an empty line when nothing changed, or
``ALL`` when the answer cannot be narrowed safely: a changed file outside
``<units-root>/<env>/`` (modules, root.hcl, other environments, .ayasee.yaml), a changed
file that belongs to no unit (a removed unit, a shared file), or a diff that cannot be computed.
Documentation-only files (``*.md``, ``docs/**``, ``LICENSE``) are ignored (R38): a PR that
changes nothing else gives the empty result, and in a mixed PR they do not widen it.

In a monorepo, ``--iac-root`` names the directory that holds the estate (``root.hcl``, the
modules and the units root). A changed file outside it is application code and is ignored the
same way, except the configuration file (``--config-path``), which always gives ``ALL``.

``--shared a,b`` answers a different question: which of the prod units that serve every stage
(``shared_units``) a pull request changes, so that ``iac-pr`` plans only those against prod. A
change to one of them gives its name; a change anywhere else inside ``iac_root`` but outside
``<units-root>`` (modules, ``root.hcl``), a file under ``<units-root>/prod/`` that belongs to no
unit, or the configuration file gives ``ALL`` (every shared unit). Changes to the other stages and
to the prod units that are not shared are ignored: they are planned elsewhere.
"""

from __future__ import annotations

import argparse
import os
import posixpath
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import PurePosixPath

from ayasee import cli

IGNORABLE_DIRS = ("docs",)  # R38: documentation never changes infrastructure
IGNORABLE_SUFFIXES = (".md",)
IGNORABLE_NAMES = ("LICENSE",)
ALL = None  # classify()/changed_units() return this when every unit must be planned
_REV = re.compile(r"^[0-9a-fA-F]{7,64}$")
UNIT_FILE = "terragrunt.hcl"
DEFAULT_CONFIG = ".ayasee.yaml"


def _ignorable(f: str) -> bool:
    path = PurePosixPath(f)
    return (
        path.suffix in IGNORABLE_SUFFIXES
        or path.name in IGNORABLE_NAMES
        or (path.parts[0] in IGNORABLE_DIRS and len(path.parts) > 1)
    )


def classify(
    files: list[str],
    units_root: str,
    env: str,
    is_unit_dir: Callable[[str], bool],
    iac_root: str = ".",
    config_path: str = DEFAULT_CONFIG,
) -> list[str] | None:
    """Unit names touched by ``files``; ``ALL`` (None) when any file cannot be tied to a unit.

    ``is_unit_dir(rel)`` says whether the repository-relative directory ``rel`` is a unit.
    Files outside ``iac_root`` are ignored, except ``config_path``.
    """
    prefix = PurePosixPath(units_root) / env
    root = PurePosixPath(posixpath.normpath(iac_root))
    config = PurePosixPath(posixpath.normpath(config_path))
    names: set[str] = set()
    for f in files:
        path = PurePosixPath(f)
        if path == config:
            return ALL
        if _ignorable(f) or not path.is_relative_to(root):
            continue
        if not path.is_relative_to(prefix) or path == prefix:
            return ALL
        unit = next(
            (
                d
                for d in path.parents
                if d != prefix and d.is_relative_to(prefix) and is_unit_dir(str(d))
            ),
            None,
        )
        if unit is None:
            return ALL
        names.add(str(unit.relative_to(prefix)).replace("/", "__"))
    return sorted(names)


def classify_shared(
    files: list[str],
    units_root: str,
    shared: list[str],
    is_unit_dir: Callable[[str], bool],
    iac_root: str = ".",
    config_path: str = DEFAULT_CONFIG,
    prod: str = "prod",
) -> list[str] | None:
    """Shared prod units touched by ``files``; ``ALL`` (None) when every one must be planned."""
    if not shared:
        return []
    units = PurePosixPath(posixpath.normpath(units_root))
    prefix = units / prod
    root = PurePosixPath(posixpath.normpath(iac_root))
    config = PurePosixPath(posixpath.normpath(config_path))
    wanted = set(shared)
    names: set[str] = set()
    for f in files:
        path = PurePosixPath(f)
        if path == config:
            return ALL
        if _ignorable(f) or not path.is_relative_to(root):
            continue
        if not path.is_relative_to(units):
            return ALL  # modules, root.hcl: may change what a shared unit plans
        if not path.is_relative_to(prefix) or path == prefix:
            continue  # another stage
        unit = next(
            (
                d
                for d in path.parents
                if d != prefix and d.is_relative_to(prefix) and is_unit_dir(str(d))
            ),
            None,
        )
        if unit is None:
            return ALL  # a shared prod file such as stages/prod/common.hcl
        name = str(unit.relative_to(prefix)).replace("/", "__")
        if name in wanted:
            names.add(name)
    return sorted(names)


def _diff_files(base: str, head: str) -> list[str] | None:
    if not (_REV.match(base) and _REV.match(head)):
        return None
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "-z", "--no-renames", f"{base}...{head}", "--"],
            capture_output=True,
            env={**os.environ, "LC_ALL": "C"},
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return [f.decode("utf-8", "surrogateescape") for f in proc.stdout.split(b"\0") if f]


def changed_units(
    base: str,
    head: str,
    units_root: str,
    env: str,
    iac_root: str = ".",
    config_path: str = DEFAULT_CONFIG,
) -> list[str] | None:
    """Changed units of ``env`` between ``base`` and ``head`` (run in the checked-out head)."""
    files = _diff_files(base, head)
    if files is None:
        return ALL
    return classify(
        files,
        units_root,
        env,
        lambda rel: os.path.isfile(os.path.join(rel, UNIT_FILE)),
        iac_root=iac_root,
        config_path=config_path,
    )


def changed_shared_units(
    base: str,
    head: str,
    units_root: str,
    shared: list[str],
    iac_root: str = ".",
    config_path: str = DEFAULT_CONFIG,
) -> list[str] | None:
    """Changed shared prod units between ``base`` and ``head`` (run in the checked-out head)."""
    files = _diff_files(base, head)
    if files is None:
        return ALL
    return classify_shared(
        files,
        units_root,
        shared,
        lambda rel: os.path.isfile(os.path.join(rel, UNIT_FILE)),
        iac_root=iac_root,
        config_path=config_path,
    )


def _run(args: argparse.Namespace) -> int:
    iac_root = args.iac_root.rstrip("/") or "."
    if args.shared is not None:
        if args.env != "prod":
            print("ayasee: changed-units: --shared needs --env prod", file=sys.stderr)
            return 1
        shared = [s for s in args.shared.split(",") if s]
        units = changed_shared_units(
            args.base,
            args.head,
            args.units_root.strip("/"),
            shared,
            iac_root=iac_root,
            config_path=args.config_path,
        )
        # ALL is spelled out: the caller plans exactly the shared units
        print(",".join(shared if units is ALL else units))
        return 0
    units = changed_units(
        args.base,
        args.head,
        args.units_root.strip("/"),
        args.env,
        iac_root=iac_root,
        config_path=args.config_path,
    )
    print("ALL" if units is ALL else ",".join(units))
    return 0


def _setup(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base", required=True, help="base commit SHA")
    parser.add_argument("--head", required=True, help="head commit SHA")
    parser.add_argument("--units-root", required=True)
    parser.add_argument("--env", required=True)
    parser.add_argument(
        "--iac-root",
        default=".",
        help="directory that holds the estate; changes outside it are ignored (default: .)",
    )
    parser.add_argument(
        "--config-path",
        default=DEFAULT_CONFIG,
        help="the ayasee configuration file; a change to it always plans every unit",
    )
    parser.add_argument(
        "--shared",
        help="comma list of shared prod units (shared_units): print which of them changed",
    )
    parser.set_defaults(handler=_run)


cli.register("changed-units", _setup)
