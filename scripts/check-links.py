#!/usr/bin/env python3
"""Offline link checker for the repository's Markdown.

Checks relative links (files, directories) and ``#anchor`` fragments, with heading slugs
computed the way GitHub does. External links (any URI scheme) are never fetched; list them
with ``--list-external``. Scans docs/**/*.md, README.md, SECURITY.md, CONTRIBUTING.md,
GOVERNANCE.md and examples/**/*.md under ``--root`` (default: the repository root).

Exit status: 0 when every relative link resolves, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

ROOT_FILES = ("README.md", "SECURITY.md", "CONTRIBUTING.md", "GOVERNANCE.md")
GLOBS = ("docs/**/*.md", "examples/**/*.md")

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ATX = re.compile(r"^ {0,3}#{1,6}[ \t]+(.*?)[ \t]*#*[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_REF_DEF = re.compile(r"^ {0,3}\[[^\]]+\]:[ \t]*(<[^>]*>|\S+)")
_CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")
_HTML_ID = re.compile(r"""<[a-zA-Z][^>]*?\b(?:id|name)=["']([^"']+)["']""")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


@dataclass(frozen=True)
class Problem:
    file: str
    line: int
    target: str
    reason: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.reason}: {self.target}"


@dataclass(frozen=True)
class Link:
    file: Path
    line: int
    target: str


def slugify(heading: str) -> str:
    """GitHub-style heading slug (without the -1, -2 suffix for duplicates)."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("`", "").replace("*", "")
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _blank_code(line: str) -> str:
    return _CODE_SPAN.sub(lambda m: " " * len(m.group(0)), line)


def _inline_targets(line: str) -> list[str]:
    """Targets of ``[text](target)`` and ``![alt](target)`` in a line without code spans."""
    out = []
    pos = 0
    while True:
        i = line.find("](", pos)
        if i < 0:
            return out
        j = i + 2
        while j < len(line) and line[j] in " \t":
            j += 1
        if j < len(line) and line[j] == "<":
            end = line.find(">", j)
            if end < 0:
                pos = j
                continue
            out.append(line[j + 1 : end])
            pos = end
            continue
        depth, k = 0, j
        while k < len(line):
            ch = line[k]
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            elif ch in " \t" and depth == 0:
                break
            k += 1
        out.append(line[j:k])
        pos = k


def parse(path: Path) -> tuple[set[str], list[Link]]:
    """Anchors defined in a Markdown file and the links it contains."""
    anchors: set[str] = set()
    seen: dict[str, int] = {}
    links: list[Link] = []
    fence: str | None = None
    prev = ""

    def add_heading(text: str) -> None:
        slug = slugify(text)
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        anchors.add(slug if n == 0 else f"{slug}-{n}")

    for no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        m = _FENCE.match(raw)
        if fence is None and m:
            fence = m.group(1)[0] * 3
            prev = ""
            continue
        if fence is not None:
            if m and m.group(1).startswith(fence):
                fence = None
            prev = ""
            continue
        for anchor in _HTML_ID.findall(raw):
            anchors.add(anchor)
        line = _blank_code(raw)
        atx = _ATX.match(raw)
        setext = _SETEXT.match(raw)
        if atx:
            add_heading(atx.group(1))
        elif (
            setext
            and prev.strip()
            and not _ATX.match(prev)
            and not prev.lstrip().startswith(("-", "*", "+", ">", "|"))
        ):
            add_heading(prev)
        ref = _REF_DEF.match(line)
        if ref:
            links.append(Link(path, no, ref.group(1).strip("<>")))
        for target in _inline_targets(line):
            links.append(Link(path, no, target))
        prev = "" if (atx or setext) else raw
    return anchors, links


def _vendored(path: Path, root: Path) -> bool:
    """Files under a hidden directory (.terraform, .git, ...) or node_modules are not ours."""
    parts = path.relative_to(root).parts[:-1]
    return any(part.startswith(".") or part == "node_modules" for part in parts)


def markdown_files(root: Path) -> list[Path]:
    files = [root / name for name in ROOT_FILES if (root / name).is_file()]
    for pattern in GLOBS:
        files += sorted(p for p in root.glob(pattern) if p.is_file() and not _vendored(p, root))
    return files


def _split(target: str) -> tuple[str, str]:
    path, _, frag = target.partition("#")
    return path.split("?", 1)[0], frag


def external_links(root: Path) -> list[tuple[str, int, str]]:
    out = []
    for md in markdown_files(root):
        _, links = parse(md)
        for link in links:
            if _SCHEME.match(link.target) and not link.target.startswith("mailto:"):
                out.append((md.relative_to(root).as_posix(), link.line, link.target))
    return out


def check(root: Path) -> list[Problem]:
    root = root.resolve()
    cache: dict[Path, set[str]] = {}
    parsed = {md: parse(md) for md in markdown_files(root)}
    for md, (anchors, _) in parsed.items():
        cache[md.resolve()] = anchors

    def anchors_of(path: Path) -> set[str]:
        path = path.resolve()
        if path not in cache:
            cache[path] = parse(path)[0]
        return cache[path]

    problems: list[Problem] = []
    for md, (_, links) in parsed.items():
        rel = md.relative_to(root).as_posix()

        def bad(link: Link, reason: str, rel: str = rel) -> None:
            problems.append(Problem(rel, link.line, link.target, reason))

        for link in links:
            if _SCHEME.match(link.target):
                continue
            path_part, frag = _split(link.target)
            frag = unquote(frag)
            if not path_part:
                if frag and frag not in anchors_of(md):
                    bad(link, "no such anchor in this file")
                continue
            path_part = unquote(path_part)
            base = root if path_part.startswith("/") else md.parent
            dest = (base / path_part.lstrip("/")).resolve()
            if root != dest and root not in dest.parents:
                bad(link, "link leaves the repository")
            elif not dest.exists():
                bad(link, "no such file")
            elif frag and dest.is_file() and dest.suffix == ".md" and frag not in anchors_of(dest):
                bad(link, "no such anchor")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument(
        "--list-external",
        action="store_true",
        help="print external (scheme) links, as file:line url; they are never fetched",
    )
    args = ap.parse_args(argv)
    root = Path(args.root)
    if args.list_external:
        for file, line, url in external_links(root):
            print(f"{file}:{line} {url}")
    problems = check(root)
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
