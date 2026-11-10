"""scripts/check-provenance.sh: the stated licence, pyproject.toml, LICENSE and NOTICE agree."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-provenance.sh"
LICENCE_FILES = {
    "Apache-2.0": "\n  Apache License\n Version 2.0\n",
    "MIT": "MIT License\n\nCopyright\n",
}


def _repo(tmp_path, spdx="Apache-2.0", file_licence="Apache-2.0", notice="ayasee\n"):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    (root / "pyproject.toml").write_text(f'[project]\nlicense = {{ text = "{spdx}" }}\n')
    (root / "LICENSE").write_text(LICENCE_FILES[file_licence])
    if notice is not None:
        (root / "NOTICE").write_text(notice)
    return root


def _doc(licence: str | None = "Apache-2.0", heading: str = "## Licence") -> str:
    bullet = "" if licence is None else f"- Licence: {licence}\n"
    return f"# P\n\n## Statement\n\n- Licence: GPL-3.0\n\n{heading}\n\nText.\n\n{bullet}\n## Next\n"


def _run(tmp_path, text: str, **repo):
    f = tmp_path / "PROVENANCE.md"
    f.write_text(text)
    root = _repo(tmp_path, **repo)
    return subprocess.run([str(SCRIPT), str(f), str(root)], capture_output=True, text=True)


def test_repository_passes():
    r = subprocess.run([str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "Apache-2.0" in r.stdout


def test_apache_passes_when_everything_agrees(tmp_path):
    r = _run(tmp_path, _doc())
    assert r.returncode == 0, r.stderr


def test_mit_passes_when_pyproject_and_licence_file_agree(tmp_path):
    r = _run(tmp_path, _doc("MIT"), spdx="MIT", file_licence="MIT")
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("value", ["", "GPL-3.0", "apache-2.0", "Apache-2.0 or MIT", "TBD"])
def test_only_the_two_exact_identifiers_pass(tmp_path, value):
    r = _run(tmp_path, _doc(value))
    assert r.returncode == 1
    assert "exactly Apache-2.0 or MIT" in r.stderr


def test_pyproject_must_agree(tmp_path):
    r = _run(tmp_path, _doc(), spdx="MIT")
    assert r.returncode == 1
    assert "pyproject.toml" in r.stderr


def test_licence_file_must_agree(tmp_path):
    r = _run(tmp_path, _doc(), file_licence="MIT")
    assert r.returncode == 1
    assert "LICENSE file" in r.stderr


@pytest.mark.parametrize("notice", [None, ""])
def test_notice_is_required(tmp_path, notice):
    r = _run(tmp_path, _doc(), notice=notice)
    assert r.returncode == 1
    assert "NOTICE" in r.stderr


def test_missing_bullet_fails(tmp_path):
    r = _run(tmp_path, _doc(None))
    assert r.returncode == 1
    assert "field missing: Licence" in r.stderr


def test_missing_section_fails(tmp_path):
    r = _run(tmp_path, _doc(heading="## Other"))
    assert r.returncode == 1
    assert "no 'Licence' section" in r.stderr


def test_missing_file_fails(tmp_path):
    r = subprocess.run([str(SCRIPT), str(tmp_path / "nope.md")], capture_output=True, text=True)
    assert r.returncode == 1
