#!/usr/bin/env python3
"""Assemble the source tree of the documentation site (GitHub Pages).

The documentation lives where GitHub renders it: docs/, the root policy files and the READMEs of
the modules, the example and the end-to-end suite. This script copies those pages into one tree,
at the same relative paths, so that the links between them keep working, and adds the landing page
from website/. A relative link to a file that is not part of the site (a workflow, a script, the
LICENSE) is rewritten to the file on GitHub.

    build-docs-site.py [--out website/.src] [--repo-url https://github.com/OWNER/ayasee]

Then: mkdocs build --strict -f website/mkdocs.yml
"""

from __future__ import annotations

import argparse
import posixpath
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = (
    "docs/**/*.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "GOVERNANCE.md",
    "CHANGELOG.md",
    "modules/*/README.md",
    "examples/databricks-azure/*.md",
    "e2e/README.md",
    "e2e/ghes/README.md",
)
EXCLUDED = ("docs/superpowers/",)
STATIC = ("schema/snapshot.v1.json",)
WEBSITE = {"website/index.md": "index.md", "website/stylesheets/extra.css": "stylesheets/extra.css"}
# The site's own scripts and the vendored third-party ones (see NOTICE): served from the site
# itself, never from a CDN.
SCRIPTS = "website/javascripts/**/*"

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_INLINE = re.compile(r"(\]\()([^)\s]+)((?:\s+\"[^\"]*\")?\))")
_REF_DEF = re.compile(r"^( {0,3}\[[^\]]+\]:[ \t]*)(\S+)(.*)$")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def collect() -> dict[str, Path]:
    """Site path -> source file, for every page and static file of the site."""
    out: dict[str, Path] = {}
    for pattern in PAGES:
        for path in sorted(ROOT.glob(pattern)):
            rel = path.relative_to(ROOT).as_posix()
            if not rel.startswith(EXCLUDED):
                out[rel] = path
    for rel in STATIC:
        out[rel] = ROOT / rel
    for src, dest in WEBSITE.items():
        out[dest] = ROOT / src
    for path in sorted(ROOT.glob(SCRIPTS)):
        if path.is_file():
            out[path.relative_to(ROOT / "website").as_posix()] = path
    return out


def rewrite(text: str, site_path: str, included: set[str], repo_url: str) -> str:
    """Point relative links that leave the site at the file on GitHub."""
    base = posixpath.dirname(site_path)

    def fix(target: str) -> str:
        if _SCHEME.match(target) or target.startswith(("#", "/", "<")):
            return target
        path, _, fragment = target.partition("#")
        resolved = posixpath.normpath(posixpath.join(base, path))
        if resolved in included or resolved.startswith(".."):
            return target
        local = ROOT / resolved
        if local.is_dir():
            if f"{resolved}/README.md" in included:
                return target
            kind = "tree"
        elif local.is_file():
            kind = "blob"
        else:
            return target  # a broken link: mkdocs --strict reports it
        return f"{repo_url}/{kind}/main/{resolved}" + (f"#{fragment}" if fragment else "")

    lines, fence = [], None
    for line in text.splitlines(keepends=True):
        mark = _FENCE.match(line)
        if mark:
            if fence is None:
                fence = mark.group(1)[0]
            elif mark.group(1)[0] == fence:
                fence = None
        elif fence is None:
            line = _INLINE.sub(lambda m: m.group(1) + fix(m.group(2)) + m.group(3), line)
            ref = _REF_DEF.match(line)
            if ref:
                line = ref.group(1) + fix(ref.group(2)) + ref.group(3) + "\n"
        lines.append(line)
    return "".join(lines)


def build(out: Path, repo_url: str) -> int:
    files = collect()
    included = set(files)
    if out.exists():
        shutil.rmtree(out)
    for site_path, src in files.items():
        dest = out / site_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if site_path.endswith(".md"):
            text = rewrite(src.read_text(encoding="utf-8"), site_path, included, repo_url)
            dest.write_text(text, encoding="utf-8")
        else:
            shutil.copyfile(src, dest)
    return len(files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "website" / ".src")
    parser.add_argument("--repo-url", default="https://github.com/tttao/ayasee")
    args = parser.parse_args()
    count = build(args.out.resolve(), args.repo_url.rstrip("/"))
    print(f"build-docs-site: {count} files in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
