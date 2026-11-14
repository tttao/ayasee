"""Which release a pinned commit is (issue #32).

A release commit is the one extra commit of ``release/v<version>`` on top of the commit the tag
``v<version>`` points at (docs/guides/releasing.md). Its ``src/ayasee/__init__.py`` says which
version it is. ``init --ref`` and ``doctor`` both ask the GitHub API, read-only, so that a SHA
of another release can no longer sit under this version's templates and callers."""

from __future__ import annotations

import re
from typing import NamedTuple

from ayasee.github import GitHub

VERSION_FILE = "src/ayasee/__init__.py"
_VERSION_LINE = re.compile(r'^__version__\s*=\s*"([^"\n]+)"\s*$', re.M)
_SOURCE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


class NotARelease(Exception):
    """The commit is not a release commit; the message says why."""


class Pinned(NamedTuple):
    version: str
    key: tuple


def version_key(version: str) -> tuple:
    """Semver precedence key: a pre-release sorts below its release, ``rc.2`` below ``rc.10``."""
    m = _SEMVER.match(version)
    if not m:
        raise NotARelease(f"{version!r} is not a MAJOR.MINOR.PATCH[-pre-release] version")
    pre = m[4]
    parts = (
        ((1,),)
        if pre is None
        else tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre.split("."))
    )
    return (int(m[1]), int(m[2]), int(m[3]), 1 if pre is None else 0, parts)


def release_version(gh: GitHub, source: str, sha: str) -> str:
    """The version of ``sha`` when it is a release commit of ``source``, else
    :class:`NotARelease`. GitHubError (an API failure) is not caught here: the caller decides."""
    if not _SOURCE.match(source):
        raise NotARelease(f"{source!r} is not owner/name")
    text = gh.get_file(source, VERSION_FILE, sha)
    m = _VERSION_LINE.search(text) if text is not None else None
    if not m:
        raise NotARelease(f"{source}@{sha[:12]} has no {VERSION_FILE} with a __version__")
    version = m[1]
    version_key(version)
    tag = f"v{version}"
    tagged = gh.tag_commit(source, tag)
    if tagged is None:
        raise NotARelease(f"{source}@{sha[:12]} says {version} but {source} has no tag {tag}")
    # GitHub serves a fork's commits by SHA under the parent repository, so a parent test alone
    # would accept a forged commit on top of the tag: it must be the head of release/<tag>
    head = gh.get_ref_sha(source, f"release/{tag}")
    if head != sha:
        raise NotARelease(
            f"{source}@{sha[:12]} says {version} but is not the head of release/{tag}"
        )
    if gh.commit_parents(source, sha) != [tagged]:
        raise NotARelease(
            f"{source}@{sha[:12]} says {version} but is not the one commit on top of tag {tag} "
            "(the release commit)"
        )
    return version
