"""``ayasee main-tip``: the commit at the tip of ``main`` (R70 supersession check).

Without ``--check`` it prints the tip of ``main``.

With ``--check SHA`` it decides whether the staged run started for ``SHA`` may go on (issue #15).
The run goes on when ``SHA`` is the tip, or when every commit after it leaves the IaC alone: no
change inside ``iac_root`` (documentation excepted, R38), to the configuration file or under
``.github/``. The change then has no newer run to carry it (a ``paths:`` filter on the caller
skips the application-only push), so this run promotes it, and the ledger records ``SHA``, the
commit that was actually checked out, planned and applied.

It refuses with ``superseded`` when a later commit touches the IaC, when the tip does not descend
from ``SHA``, and when the list of changed files may be incomplete. Any error refuses too."""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
from pathlib import Path, PurePosixPath

from ayasee import cli
from ayasee.changed import DEFAULT_CONFIG, _ignorable
from ayasee.config import ConfigError, load_config
from ayasee.github import GitHub, GitHubError

_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
# Workflows and local actions decide what a run does: a change there always supersedes.
_ALWAYS = PurePosixPath(".github")


class Superseded(Exception):
    """The run of this commit must stop: a newer run carries the change."""


def _repo(value: str) -> str:
    if not _REPO.match(value):
        raise argparse.ArgumentTypeError("expected owner/name")
    return value


def _sha(value: str) -> str:
    if not _SHA.match(value):
        raise argparse.ArgumentTypeError("expected a full 40-character commit SHA")
    return value


def touches_iac(path: str, iac_root: str, config_path: str) -> bool:
    """Whether a change to ``path`` (repository-relative) can change what a staged run does."""
    p = PurePosixPath(posixpath.normpath(path))
    if p == PurePosixPath(posixpath.normpath(config_path)) or p.is_relative_to(_ALWAYS):
        return True
    if _ignorable(path):
        return False
    return p.is_relative_to(PurePosixPath(posixpath.normpath(iac_root)))


def decide(gh: GitHub, repo: str, sha: str, iac_root: str, config_path: str) -> str:
    """A one-line notice when the run may go on; raises :class:`Superseded` otherwise."""
    tip = gh.main_sha(repo)
    if tip == sha:
        return ""
    status, paths, complete = gh.compare_files(repo, sha, tip)
    if status != "ahead":
        raise Superseded(f"superseded: the tip of main ({tip}) does not descend from {sha}")
    if not complete:
        raise Superseded(
            f"superseded: the commits after {sha} change too many files to check; "
            f"the run of {tip} promotes the change"
        )
    touched = sorted({p for p in paths if touches_iac(p, iac_root, config_path)})
    if touched:
        shown = ", ".join(touched[:5]) + (", ..." if len(touched) > 5 else "")
        raise Superseded(
            f"superseded: {sha} is not the tip of main ({tip}) and later commits change "
            f"the IaC ({shown}); the run of the newer commit promotes the change"
        )
    return (
        f"{sha} is not the tip of main ({tip}), but no later commit changes the IaC; "
        "this run promotes the change"
    )


def _run(args: argparse.Namespace) -> int:
    gh: GitHub | None = None
    try:
        if args.check is None:
            gh = GitHub.from_env()
            print(gh.main_sha(args.repo))
            return 0
        iac_root = load_config(Path(args.config_path)).iac_root
        gh = GitHub.from_env()
        notice = decide(gh, args.repo, args.check, iac_root, args.config_path)
    except Superseded as exc:
        print(f"main-tip: {exc}", file=sys.stderr)
        return 1
    except (GitHubError, ConfigError) as exc:
        print(f"main-tip: error: {exc}", file=sys.stderr)
        return 1
    finally:
        if gh is not None:
            gh.close()
    if notice:
        print(notice)
    return 0


def _setup(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", required=True, type=_repo, help="owner/name")
    parser.add_argument(
        "--check",
        type=_sha,
        metavar="SHA",
        help="decide whether the run of SHA may go on (exit 1 when it is superseded)",
    )
    parser.add_argument(
        "--config-path",
        default=DEFAULT_CONFIG,
        help="the ayasee configuration file at SHA (gives iac_root); with --check only",
    )
    parser.set_defaults(handler=_run)


cli.register("main-tip", _setup)
