"""Tests for scripts/check-links.py (offline relative-link and anchor checker)."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-links.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_links", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_links"] = mod
    spec.loader.exec_module(mod)
    return mod


cl = _load()


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _problems(root: Path) -> list[str]:
    return [str(p) for p in cl.check(root)]


@pytest.mark.parametrize(
    ("heading", "slug"),
    [
        ("Hello World", "hello-world"),
        ("The `ayasee.yaml` file", "the-ayaseeyaml-file"),
        ("Foo & Bar", "foo--bar"),
        ("Dependencies between units (R52)", "dependencies-between-units-r52"),
        ("What's new?", "whats-new"),
        ("1. Create and install the GitHub App", "1-create-and-install-the-github-app"),
        ("A [link](http://x.y) here", "a-link-here"),
        ("snake_case stays", "snake_case-stays"),
        ("**Bold** and *em*", "bold-and-em"),
    ],
)
def test_github_slug(heading, slug):
    assert cl.slugify(heading) == slug


def test_valid_links_and_anchors_pass(tmp_path):
    _write(tmp_path, "README.md", "# Top\n\n[a](docs/a.md) [b](docs/a.md#section-one) [t](#top)\n")
    _write(tmp_path, "docs/a.md", "# A\n\n## Section one\n\n[up](../README.md#top)\n")
    assert _problems(tmp_path) == []


def test_missing_file_is_reported_with_line(tmp_path):
    _write(tmp_path, "README.md", "# Top\n\nline\n[gone](docs/nope.md)\n")
    probs = _problems(tmp_path)
    assert len(probs) == 1
    assert "README.md:4" in probs[0] and "docs/nope.md" in probs[0]


def test_missing_anchor_is_reported(tmp_path):
    _write(tmp_path, "README.md", "# Top\n\n[x](docs/a.md#nope) [y](#also-nope)\n")
    _write(tmp_path, "docs/a.md", "# A\n")
    assert len(_problems(tmp_path)) == 2


def test_duplicate_headings_get_numbered_anchors(tmp_path):
    text = "# T\n\n## Same\n\n## Same\n\n[a](#same) [b](#same-1) [c](#same-2)\n"
    _write(tmp_path, "README.md", text)
    probs = _problems(tmp_path)
    assert len(probs) == 1 and "#same-2" in probs[0]


def test_setext_headings_count(tmp_path):
    text = "Title\n=====\n\nSub head\n--------\n\n[a](#title) [b](#sub-head)\n"
    _write(tmp_path, "README.md", text)
    assert _problems(tmp_path) == []


def test_external_and_mailto_are_skipped(tmp_path):
    _write(
        tmp_path, "README.md", "[a](https://example.invalid/x) [b](mailto:a@b.c) [c](http://x)\n"
    )
    assert _problems(tmp_path) == []


def test_links_in_code_are_ignored(tmp_path):
    text = "# T\n\n```md\n[x](missing.md)\n```\n\nInline `[y](missing2.md)` too.\n"
    _write(tmp_path, "README.md", text)
    assert _problems(tmp_path) == []


def test_reference_definitions_and_images_are_checked(tmp_path):
    _write(tmp_path, "README.md", "# T\n\n[ref]: docs/missing.md\n![img](docs/pic.png)\n")
    assert len(_problems(tmp_path)) == 2


def test_percent_encoded_paths_resolve(tmp_path):
    _write(tmp_path, "reports/My File.md", "# X\n")
    _write(tmp_path, "README.md", "[a](reports/My%20File.md)\n")
    assert _problems(tmp_path) == []


def test_link_title_and_angle_brackets(tmp_path):
    _write(tmp_path, "a.md", "# T\n")
    _write(tmp_path, "README.md", '[a](a.md "title") [b](<a.md>)\n')
    assert _problems(tmp_path) == []


def test_directory_target_is_allowed_but_anchor_on_it_is_not_checked(tmp_path):
    (tmp_path / "docs").mkdir()
    _write(tmp_path, "README.md", "[d](docs/) [e](docs)\n")
    assert _problems(tmp_path) == []


def test_link_escaping_the_repo_is_broken(tmp_path):
    _write(tmp_path / "repo", "README.md", "[x](../outside.md)\n")
    _write(tmp_path, "outside.md", "# O\n")
    assert len(_problems(tmp_path / "repo")) == 1


def test_only_configured_files_are_scanned(tmp_path):
    _write(tmp_path, "notes/other.md", "[x](gone.md)\n")
    _write(tmp_path, "README.md", "# T\n")
    assert _problems(tmp_path) == []
    _write(tmp_path, "examples/ex/README.md", "[x](gone.md)\n")
    assert len(_problems(tmp_path)) == 1


def test_hidden_and_vendored_directories_are_skipped(tmp_path):
    _write(tmp_path, "examples/ex/.terraform/providers/x/README.md", "[x](gone.md)\n")
    _write(tmp_path, "docs/node_modules/pkg/README.md", "[x](gone.md)\n")
    _write(tmp_path, "README.md", "# T\n")
    assert _problems(tmp_path) == []


def test_html_anchor_ids_count(tmp_path):
    _write(tmp_path, "README.md", '<a id="custom"></a>\n\n[x](#custom)\n')
    assert _problems(tmp_path) == []


def test_cli_exit_codes_and_external_listing(tmp_path, capsys):
    _write(tmp_path, "README.md", "[e](https://example.invalid/p)\n[bad](gone.md)\n")
    assert cl.main(["--root", str(tmp_path)]) == 1
    assert "gone.md" in capsys.readouterr().err
    assert cl.main(["--root", str(tmp_path), "--list-external"]) == 1
    out = capsys.readouterr().out
    assert "https://example.invalid/p" in out and "README.md:1" in out
    _write(tmp_path, "README.md", "[e](https://example.invalid/p)\n")
    assert cl.main(["--root", str(tmp_path)]) == 0


def test_repository_has_no_broken_links():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
