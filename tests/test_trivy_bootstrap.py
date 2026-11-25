"""Trivy scans what ``ayasee init`` writes without crashing, every time (#57).

Trivy 0.70.0 to at least 0.76.2 panics, at random (Go map order), on a ``for_each`` local that holds
a null object. ``modules/github-environments`` held two with the workflow gate and unset reviewers,
which is what init writes for a private repository below GitHub Enterprise; the adopter's PR
checks then failed on most runs. ayasee's own examples call the module by relative path with
reviewers set, so nothing here saw it.

This test scaffolds each profile and gate, points the ``git::…//modules/<m>?ref=<sha>`` sources at a
copy of this checkout's ``modules/`` (the offline equivalent of the download the adopter's Trivy
does; the panic reproduces through it) and runs ``trivy config`` as ``iac-pr`` does, several
times. Trivy comes from ``scripts/install-dev-tools.sh trivy``, the version the workflows state.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ayasee import cli, scaffold  # noqa: F401  (registers init)

ROOT = Path(__file__).resolve().parents[1]
SHA = "4" * 40
RUNS = 5  # the unfixed module panicked on 9 to 10 runs out of 10
GIT_SOURCE = re.compile(
    r'"git::https://github\.com/[^"/]+/[^"/]+\.git//modules/([a-z0-9-]+)\?ref=[0-9a-f]{40}"'
)

CASES = [
    ("cloudflare-s3", "free"),  # gate_mechanism workflow, reviewers unset: the #57 case
    ("cloudflare-s3", "enterprise"),  # gate_mechanism environment
    ("databricks-azure", "free"),
    ("databricks-azure", "enterprise"),
]


def _trivy() -> str:
    local = ROOT / ".tools" / "bin" / "trivy"
    found = str(local) if local.exists() else shutil.which("trivy")
    if not found:
        if os.environ.get("AYASEE_REQUIRE_TRIVY") == "1":
            pytest.fail("trivy is required here: scripts/install-dev-tools.sh trivy")
        pytest.skip("trivy not installed (scripts/install-dev-tools.sh trivy)")
    return found


def _point_sources_at_this_checkout(repo: Path) -> int:
    modules = repo / ".ayasee-modules"
    shutil.copytree(ROOT / "modules", modules, ignore=shutil.ignore_patterns(".terraform"))
    rewritten = 0
    for tf in repo.rglob("*.tf"):
        if modules in tf.parents:
            continue
        text = tf.read_text()
        rel = os.path.relpath(modules, tf.parent)
        new, n = GIT_SOURCE.subn(lambda m, rel=rel: f'"{rel}/{m[1]}"', text)
        if n:
            tf.write_text(new)
            rewritten += n
    return rewritten


@pytest.mark.integration
@pytest.mark.parametrize(("profile", "plan"), CASES)
def test_trivy_config_scans_the_init_output_without_crashing(profile, plan, tmp_path, monkeypatch):
    trivy = _trivy()
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    args = ["init", "--dir", str(repo), "--profile", profile, "--plan", plan]
    assert cli.main([*args, "--ref", SHA, "--unverified-ref", "--repo", "example/infra"]) == 0
    assert _point_sources_at_this_checkout(repo) >= 1, "init wrote no git:: module source"
    codes = []
    for i in range(RUNS):
        out = tmp_path / f"trivy-{i}.sarif"
        done = subprocess.run(
            [
                trivy,
                "config",
                "--quiet",
                "--skip-check-update",
                "--cache-dir",
                str(tmp_path / "cache"),
                "--severity",
                "HIGH,CRITICAL",
                "--exit-code",
                "1",
                "--format",
                "sarif",
                "--output",
                str(out),
                str(repo),
            ],
            capture_output=True,
            text=True,
        )
        assert "panic:" not in done.stderr, (i, done.stderr[-2000:])
        codes.append(done.returncode)
        assert out.exists(), (i, done.stderr[-2000:])
    # 0: no crash and no high or critical finding, as iac-pr's checks need
    assert codes == [0] * RUNS, codes
