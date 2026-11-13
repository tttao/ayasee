"""``ayasee init``: scaffold an adopting repository from a released profile (issue #11).

It writes the configuration, the caller workflows, ``root.hcl`` and the bootstrap root of a
profile (``cloudflare-s3`` or ``databricks-azure``), with the stages and ``iac_root`` asked for,
and pins every reference to ayasee at one release commit, so there is no ``OWNER`` placeholder to
search for and no ``sed`` to run:

- the callers ``uses: <source>/.github/workflows/iac-*.yml@<release SHA>``;
- the bootstrap modules ``git::https://github.com/<source>.git//modules/<m>?ref=<release SHA>``.

The release commit is found on GitHub from the installed version: the head of
``release/v<version>``, accepted only when its single parent is the commit the tag
``v<version>`` points at (docs/guides/releasing.md). ``--ref`` gives it instead; it is required
when ayasee runs from a source checkout, whose templates may differ from any release.

It writes no unit: ``<units_root>/<stage>/`` is created empty (a ``.gitkeep``), and
``shared_units`` starts empty. Existing files are never overwritten without ``--force``; on a
refusal nothing is written."""

from __future__ import annotations

import argparse
import os
import posixpath
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from ayasee import __version__, cli
from ayasee.config import ConfigError, parse_config_text, staged_layout
from ayasee.github import DEFAULT_API_URL, GitHub, GitHubError
from ayasee.maintip import _repo

DEFAULT_SOURCE = "tttao/ayasee"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_REL_DIR = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
_STAGE_LIST = re.compile(r"^stages: \[[^\]]*\]$", re.M)
_TF_STAGES = re.compile(r"^(\s*stages\s*=\s*)\[[^\]]*\]$", re.M)
_TF_SOURCE = re.compile(r'source(\s*)= "\.\./\.\./\.\./modules/([a-z0-9-]+)"')
_USES = re.compile(r"OWNER/ayasee/(\.github/workflows/[a-z-]+\.yml)@0{40}( # [^\n]*)?")
_PUSH_MAIN = "  push:\n    branches: [main]\n"


class InitError(Exception):
    """The scaffold cannot be written; nothing was written."""


@dataclass(frozen=True)
class Profile:
    name: str
    stages: tuple[str, ...]
    workflows: tuple[str, ...]
    bootstrap: tuple[str, ...]


PROFILES = {
    p.name: p
    for p in (
        Profile(
            "cloudflare-s3",
            ("test", "prod"),
            ("pr.yml", "main.yml", "nightly.yml", "breakglass.yml"),
            ("main.tf", ".terraform.lock.hcl"),
        ),
        Profile(
            "databricks-azure",
            ("dev", "uat", "prod"),
            ("pr.yml", "main.yml", "nightly.yml", "breakglass.yml"),
            ("main.tf", "variables.tf", "outputs.tf", ".terraform.lock.hcl"),
        ),
    )
}


def templates_root() -> tuple[Path, bool]:
    """The profile templates and whether they are the packaged (released) ones."""
    packaged = Path(__file__).resolve().parent / "templates"
    if packaged.is_dir():
        return packaged, True
    return Path(__file__).resolve().parents[2] / "examples", False


def resolve_release(gh: GitHub, source: str, version: str = __version__) -> str:
    """The release SHA of ``v<version>``: the head of ``release/v<version>`` whose only parent is
    the tagged commit. Raises :class:`InitError` otherwise."""
    tag = f"v{version}"
    tagged = gh.tag_commit(source, tag)
    if tagged is None:
        raise InitError(f"{source} has no tag {tag}; pass --ref <release SHA>")
    head = gh.get_ref_sha(source, f"release/{tag}")
    if head is None:
        raise InitError(f"{source} has no branch release/{tag}; pass --ref <release SHA>")
    if gh.commit_parents(source, head) != [tagged]:
        raise InitError(
            f"release/{tag} of {source} is not one commit on top of {tag}; "
            "pass --ref <release SHA> after checking the release notes"
        )
    return head


def _sub_once(pattern: re.Pattern[str], repl: str, text: str, what: str) -> str:
    out, n = pattern.subn(repl, text, count=1)
    if n != 1:
        raise InitError(f"template: {what} not found")
    return out


def _flow_list(items: list[str]) -> str:
    return "[" + ", ".join(items) + "]"


def render(
    profile: Profile,
    templates: Path,
    *,
    stages: list[str],
    iac_root: str,
    source: str,
    sha: str,
    label: str,
) -> dict[str, str]:
    """Repository-relative path -> content of every file to write."""
    src = templates / profile.name
    units_root = "stages" if iac_root == "." else f"{iac_root}/stages"
    files: dict[str, str] = {}

    cfg = (src / ".ayasee.yaml").read_text()
    cfg = _sub_once(_STAGE_LIST, f"stages: {_flow_list(stages)}", cfg, "stages")
    root_keys = f"units_root: {units_root}"
    if iac_root != ".":
        root_keys = f"iac_root: {iac_root}\n{root_keys}"
    cfg = _sub_once(re.compile(r"^units_root: stages$", re.M), root_keys, cfg, "units_root")
    cfg = re.sub(r"^shared_units: \[[^\]]*\]$", "shared_units: []", cfg, flags=re.M)
    files[".ayasee.yaml"] = cfg

    for name in profile.workflows:
        text = (src / ".github" / "workflows" / name).read_text()
        text, n = _USES.subn(f"{source}/\\1@{sha} # {label}", text)
        if n == 0 or "OWNER/" in text or "0" * 40 in text:
            raise InitError(f"template: {name}: the ayasee reference was not pinned")
        if name == "main.yml" and iac_root != ".":
            if _PUSH_MAIN not in text:
                raise InitError("template: main.yml: no push trigger on main")
            # only commits that change the IaC start a staged run (R70, issue #15)
            paths = f'    paths: ["{iac_root}/**", ".ayasee.yaml", ".github/**"]\n'
            text = text.replace(_PUSH_MAIN, _PUSH_MAIN + paths, 1)
        files[f".github/workflows/{name}"] = text

    base = "" if iac_root == "." else f"{iac_root}/"
    files[f"{base}root.hcl"] = (src / "root.hcl").read_text()
    for name in profile.bootstrap:
        path = src / "bootstrap" / name
        if not path.is_file():
            continue
        text = path.read_text()
        if name == "main.tf":
            quoted = "[" + ", ".join(f'"{s}"' for s in stages) + "]"
            text = _sub_once(_TF_STAGES, f"\\g<1>{quoted}", text, "bootstrap stages")
            text, n = _TF_SOURCE.subn(
                f'source\\1= "git::https://github.com/{source}.git//modules/\\2?ref={sha}"', text
            )
            if n == 0 or "../modules/" in text:
                raise InitError("template: bootstrap: a module source was not pinned")
            # no shared unit yet: no read-only prod plan environment for pull requests
            text = text.replace("shared_units_pr_plan = true", "shared_units_pr_plan = false")
        files[f"{base}bootstrap/{name}"] = text
    for stage in stages:
        files[f"{units_root}/{stage}/.gitkeep"] = ""
    return files


def _check_args(profile: Profile, stages: list[str], iac_root: str) -> None:
    if iac_root != "." and (
        not _REL_DIR.match(iac_root) or any(p in (".", "..") for p in iac_root.split("/"))
    ):
        raise InitError(f"--iac-root: {iac_root!r} is not a relative directory")
    try:
        staged_layout(stages, "")
    except ConfigError as exc:
        raise InitError(f"--stages: {exc}") from None


def _run(args: argparse.Namespace) -> int:
    profile = PROFILES[args.profile]
    stages = [s.strip() for s in args.stages.split(",")] if args.stages else list(profile.stages)
    iac_root = posixpath.normpath(args.iac_root.strip("/") or ".")
    dest = Path(args.dir)
    try:
        _check_args(profile, stages, iac_root)
        templates, packaged = templates_root()
        if args.ref:
            sha, label = args.ref, f"ayasee release commit (--ref, {__version__} templates)"
        elif not packaged:
            raise InitError(
                "ayasee runs from a source checkout, whose templates may differ from any "
                "release; pass --ref <release SHA>"
            )
        else:
            gh = GitHub(
                os.environ.get("GITHUB_API_URL") or DEFAULT_API_URL,
                os.environ.get("GITHUB_TOKEN", ""),
            )
            try:
                sha = resolve_release(gh, args.source)
            finally:
                gh.close()
            label = f"ayasee v{__version__} (release/v{__version__})"
        files = render(
            profile,
            templates,
            stages=stages,
            iac_root=iac_root,
            source=args.source,
            sha=sha,
            label=label,
        )
        parse_config_text(files[".ayasee.yaml"])  # the written configuration must load
        existing = sorted(p for p in files if (dest / p).exists())
        if existing and not args.force:
            raise InitError(
                "these files exist (nothing written; --force overwrites them): "
                + ", ".join(existing)
            )
    except (InitError, ConfigError, GitHubError, OSError) as exc:
        print(f"init: error: {exc}", file=sys.stderr)
        return 1
    for rel, content in files.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        print(f"wrote {rel}")
    base = "" if iac_root == "." else f"{iac_root}/"
    print(
        f"\nPinned to {args.source}@{sha}.\n"
        "Next:\n"
        f"  1. run the bootstrap root in {base}bootstrap (a repository administrator, locally)\n"
        "  2. create the state store, the secrets and the ledger token (the profile's README)\n"
        f"  3. add the units under {base}stages/<stage>/\n"
        '  4. GITHUB_TOKEN="$(gh auth token)" ayasee doctor, until it has no failure\n'
        "  5. open the first pull request"
    )
    return 0


def _sha(value: str) -> str:
    if not _SHA.match(value):
        raise argparse.ArgumentTypeError("expected a full 40-character commit SHA")
    return value


def _setup(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", required=True, choices=sorted(PROFILES))
    parser.add_argument(
        "--stages", help="comma list, prod last (default: the profile's, e.g. test,prod)"
    )
    parser.add_argument(
        "--iac-root", default=".", help="directory of the estate in a monorepo (default: .)"
    )
    parser.add_argument("--dir", default=".", help="the repository checkout (default: .)")
    parser.add_argument(
        "--source", type=_repo, default=DEFAULT_SOURCE, help="owner/name of ayasee (a mirror)"
    )
    parser.add_argument("--ref", type=_sha, help="the release SHA to pin, instead of looking it up")
    parser.add_argument("--force", action="store_true", help="overwrite existing files")
    parser.set_defaults(handler=_run)


cli.register("init", _setup)
