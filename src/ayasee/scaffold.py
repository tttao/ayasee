"""``ayasee init``: scaffold an adopting repository from a released profile (issue #11).

It writes the configuration, the caller workflows, ``root.hcl`` and the bootstrap root of a
profile (``cloudflare-s3`` or ``databricks-azure``), with the stages and ``iac_root`` asked for,
and pins every reference to ayasee at one release commit, so there is no ``OWNER`` placeholder to
search for and no ``sed`` to run:

- the callers ``uses: <source>/.github/workflows/iac-*.yml@<release SHA>``;
- the bootstrap modules ``git::https://github.com/<source>.git//modules/<m>?ref=<release SHA>``.

The release commit is found on GitHub from the installed version: the head of
``release/v<version>``, accepted only when its single parent is the commit the tag
``v<version>`` points at (docs/guides/releasing.md). ``--ref`` gives it instead, and is checked:
a release commit whose ``__version__`` is this version, else a refusal (a SHA of another release
under these templates is the trap of issue #32); ``--unverified-ref`` skips that check. It is
required when ayasee runs from a source checkout, whose templates may differ from any release.

It writes no unit: ``<units_root>/<stage>/`` is created empty (a ``.gitkeep``), and
``shared_units`` starts empty. Existing files are never overwritten without ``--force``; on a
refusal nothing is written."""

from __future__ import annotations

import argparse
import difflib
import os
import posixpath
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from ayasee import __version__, cli
from ayasee.config import ConfigError, parse_config_text, staged_layout
from ayasee.github import DEFAULT_API_URL, GitHub, GitHubError
from ayasee.maintip import _repo
from ayasee.releasepin import NotARelease, release_version

DEFAULT_SOURCE = "tttao/ayasee"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_REL_DIR = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
_STAGE_LIST = re.compile(r"^stages: \[[^\]]*\]$", re.M)
_TF_STAGES = re.compile(r"^(\s*stages\s*=\s*)\[[^\]]*\]$", re.M)
_TF_SOURCE = re.compile(r'source(\s*)= "\.\./\.\./\.\./modules/([a-z0-9-]+)"')
_USES = re.compile(r"OWNER/ayasee/(\.github/workflows/[a-z-]+\.yml)@0{40}( # [^\n]*)?")
_PUSH_MAIN = "  push:\n    branches: [main]\n"
STAGED_PATHS_CALLERS = ".github/workflows/iac-*.yml"


class InitError(Exception):
    """The scaffold cannot be written; nothing was written."""


@dataclass(frozen=True)
class Profile:
    name: str
    stages: tuple[str, ...]
    workflows: tuple[str, ...]
    bootstrap: tuple[str, ...]


WORKFLOWS = ("iac-pr.yml", "iac-staged.yml", "iac-nightly.yml", "iac-breakglass.yml")

PROFILES = {
    p.name: p
    for p in (
        Profile(
            "cloudflare-s3",
            ("test", "prod"),
            WORKFLOWS,
            ("main.tf", ".terraform.lock.hcl"),
        ),
        Profile(
            "databricks-azure",
            ("dev", "uat", "prod"),
            WORKFLOWS,
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


def verify_ref(gh: GitHub, source: str, sha: str, version: str = __version__) -> None:
    """``--ref`` must be a release commit of exactly the version whose templates are written.
    Raises :class:`InitError` otherwise (an API failure raises GitHubError: never a pass)."""
    try:
        found = release_version(gh, source, sha)
    except NotARelease as exc:
        raise InitError(f"--ref: {exc}; --unverified-ref skips this check") from None
    if found != version:
        raise InitError(
            f"--ref {sha[:12]} is the release commit of {found}, but these templates are "
            f"{version}: the callers would pass inputs and secrets the pinned workflows do not "
            f"have. Use ayasee@{found} for it, or the release commit of {version}"
        )


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
        if name == "iac-staged.yml" and iac_root != ".":
            if _PUSH_MAIN not in text:
                raise InitError("template: iac-staged.yml: no push trigger on main")
            # only commits that change the IaC start a staged run (R70, issue #15); the ayasee
            # callers, not all of .github/, so that an application workflow starts none (#33)
            paths = f'    paths: ["{iac_root}/**", ".ayasee.yaml", "{STAGED_PATHS_CALLERS}"]\n'
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


def _resolve_pin(args: argparse.Namespace) -> tuple[str, str]:
    """The release commit to pin and the label written after it (``init`` and ``--upgrade``)."""
    _, packaged = templates_root()
    if args.ref:
        if args.unverified_ref:
            print(
                "init: warning: --ref is not verified as the release commit of "
                f"{__version__}; doctor will still check it",
                file=sys.stderr,
            )
        else:
            gh = GitHub(
                os.environ.get("GITHUB_API_URL") or DEFAULT_API_URL,
                os.environ.get("GITHUB_TOKEN", ""),
            )
            try:
                verify_ref(gh, args.source, args.ref)
            finally:
                gh.close()
        return args.ref, f"ayasee release commit (--ref, {__version__} templates)"
    if not packaged:
        raise InitError(
            "ayasee runs from a source checkout, whose templates may differ from any "
            "release; pass --ref <release SHA>"
        )
    gh = GitHub(
        os.environ.get("GITHUB_API_URL") or DEFAULT_API_URL,
        os.environ.get("GITHUB_TOKEN", ""),
    )
    try:
        sha = resolve_release(gh, args.source)
    finally:
        gh.close()
    return sha, f"ayasee v{__version__} (release/v{__version__})"


def _inside(dest: Path, rel: str) -> Path:
    """``dest/rel``, refused when it resolves outside ``dest`` (a symlink or a ``..``)."""
    root = dest.resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise InitError(f"{rel}: resolves outside the repository; nothing written")
    return dest / rel


def _key_diff(old: str, new: str) -> str:
    """Top-level keys of two ``.ayasee.yaml`` texts: added, changed and kept-only-here."""
    try:
        a, b = yaml.safe_load(old), yaml.safe_load(new)
    except yaml.YAMLError:
        return ""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return ""
    lines = [f"  key only in the scaffold: {k}" for k in b if k not in a]
    lines += [f"  key only in your file (--force drops it): {k}" for k in a if k not in b]
    lines += [f"  key with another value: {k}" for k in a if k in b and a[k] != b[k]]
    return "\n".join(lines)


def _describe_conflict(rel: str, old: str, new: str) -> str:
    diff = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            f"a/{rel} (yours)",
            f"b/{rel} (scaffold)",
        )
    )
    keys = _key_diff(old, new) if rel == ".ayasee.yaml" else ""
    return f"{rel} differs:\n{keys}\n{diff}" if keys else f"{rel} differs:\n{diff}"


def plan_writes(
    dest: Path, files: dict[str, str]
) -> tuple[dict[str, str], list[str], dict[str, str]]:
    """Split ``files`` into (to write, unchanged, conflicts: path -> report).

    A file that does not exist is written. One that exists with the same content is unchanged. An
    empty scaffold file (a ``.gitkeep``) is unchanged whatever is there: ``init`` must not refuse
    over a file it wrote itself with no content. Any other file is a conflict."""
    todo: dict[str, str] = {}
    same: list[str] = []
    conflicts: dict[str, str] = {}
    for rel, content in files.items():
        path = _inside(dest, rel)
        if not path.exists() and not path.is_symlink():
            todo[rel] = content
        elif content == "" and path.is_file():
            same.append(rel)
        elif path.is_file() and path.read_text() == content:
            same.append(rel)
        elif path.is_file():
            conflicts[rel] = _describe_conflict(rel, path.read_text(), content)
        else:
            conflicts[rel] = f"{rel} exists and is not a regular file"
    return todo, same, conflicts


# ---- --upgrade: rewrite the release pin and nothing else ---------------------------------------


def _pin_patterns(source: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    repo = re.escape(source)
    uses = re.compile(
        rf"^(\s*(?:-\s+)?uses:\s*{repo}/\.github/workflows/iac-[a-z-]+\.yml)@[0-9a-f]{{40}}"
        rf"(?:[ \t]+#[^\n]*)?$",
        re.M,
    )
    mod = re.compile(
        rf"(git::https://github\.com/{repo}\.git//modules/[a-z0-9-]+\?ref=)[0-9a-f]{{40}}"
    )
    return uses, mod


def upgrade_text(text: str, source: str, sha: str, label: str) -> tuple[str, int]:
    """``text`` with every ayasee pin of ``source`` moved to ``sha``; the count of pins."""
    uses, mod = _pin_patterns(source)
    text, n1 = uses.subn(lambda m: f"{m.group(1)}@{sha} # {label}", text)
    text, n2 = mod.subn(lambda m: f"{m.group(1)}{sha}", text)
    return text, n1 + n2


def _skeleton(text: str, source: str) -> str:
    """``text`` with every pin blanked: two texts with the same skeleton differ only by pins."""
    uses, mod = _pin_patterns(source)
    text = uses.sub(lambda m: f"{m.group(1)}@PIN", text)
    return mod.sub(lambda m: f"{m.group(1)}PIN", text)


def _upgrade(args: argparse.Namespace, dest: Path) -> int:
    for flag, value in (
        ("--profile", args.profile),
        ("--stages", args.stages),
        ("--iac-root", args.iac_root),
        ("--force", args.force or None),
    ):
        if value:
            raise InitError(f"--upgrade only moves the release pin; {flag} does not apply")
    sha, label = _resolve_pin(args)
    base = ""
    cfg = dest / ".ayasee.yaml"
    if cfg.is_file():
        iac_root = parse_config_text(cfg.read_text()).iac_root
        base = "" if iac_root == "." else f"{iac_root}/"
    candidates = sorted((dest / ".github" / "workflows").glob("*.yml"))
    candidates += sorted((dest / f"{base}bootstrap").glob("*.tf"))
    changed: dict[Path, str] = {}
    total = 0
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            continue
        _inside(dest, str(path.relative_to(dest)))  # a symlinked directory out of the checkout
        old = path.read_text()
        new, n = upgrade_text(old, args.source, sha, label)
        total += n
        if new != old:
            if _skeleton(new, args.source) != _skeleton(old, args.source):
                raise InitError(f"{path}: the rewrite changed more than the pin; nothing written")
            changed[path] = new
    if total == 0:
        raise InitError(f"no ayasee reference of {args.source} found; nothing to upgrade")
    for path, new in changed.items():
        path.write_text(new)
        print(f"pinned {path.relative_to(dest)}")
    if not changed:
        print(f"already pinned to {args.source}@{sha}; nothing changed")
    else:
        print(f"\nPinned {len(changed)} file(s) to {args.source}@{sha}; triggers untouched.")
    return 0


def _run(args: argparse.Namespace) -> int:
    dest = Path(args.dir)
    try:
        if args.upgrade:
            return _upgrade(args, dest)
        if not args.profile:
            raise InitError("--profile is required (or --upgrade)")
        profile = PROFILES[args.profile]
        stages = (
            [s.strip() for s in args.stages.split(",")] if args.stages else list(profile.stages)
        )
        iac_root = posixpath.normpath((args.iac_root or ".").strip("/") or ".")
        _check_args(profile, stages, iac_root)
        templates, _ = templates_root()
        sha, label = _resolve_pin(args)
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
        todo, same, conflicts = plan_writes(dest, files)
        if conflicts and not args.force:
            for report in conflicts.values():
                print(report, file=sys.stderr)
            raise InitError(
                "these files exist and differ (nothing written; --force overwrites them): "
                + ", ".join(conflicts)
            )
        todo.update({rel: files[rel] for rel in conflicts})
    except (InitError, ConfigError, GitHubError, OSError) as exc:
        print(f"init: error: {exc}", file=sys.stderr)
        return 1
    for rel in same:
        print(f"unchanged {rel}")
    for rel, content in todo.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        print(f"wrote {rel}")
    base = "" if iac_root == "." else f"{iac_root}/"
    print(
        f"\nPinned to {args.source}@{sha}.\n"
        "Next:\n"
        f"  1. run the bootstrap root in {base}bootstrap (a repository administrator, locally)\n"
        "  2. create the state store (the profile's README), then the secrets: run one by one\n"
        f"     the commands of `tofu output -raw secret_commands` in {base}bootstrap\n"
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
    parser.add_argument("--profile", choices=sorted(PROFILES), help="required unless --upgrade")
    parser.add_argument(
        "--stages", help="comma list, prod last (default: the profile's, e.g. test,prod)"
    )
    parser.add_argument(
        "--iac-root", help="directory of the estate in a monorepo (default: the root, .)"
    )
    parser.add_argument("--dir", default=".", help="the repository checkout (default: .)")
    parser.add_argument(
        "--source", type=_repo, default=DEFAULT_SOURCE, help="owner/name of ayasee (a mirror)"
    )
    parser.add_argument(
        "--ref",
        type=_sha,
        help="the release SHA to pin, instead of looking it up (verified: it must be this "
        "version's release commit)",
    )
    parser.add_argument(
        "--unverified-ref",
        action="store_true",
        help="pin --ref without checking it is the release commit of this version (a mirror whose "
        "release commit was re-pinned, or a source checkout ahead of its release)",
    )
    parser.add_argument("--force", action="store_true", help="overwrite existing files that differ")
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="only move the release SHA pinned in the callers and the bootstrap sources "
        "(triggers and paths: are not touched)",
    )
    parser.set_defaults(handler=_run)


cli.register("init", _setup)
