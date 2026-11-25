"""A scanner that crashes writes no report: the step that reads the report says so and fails (#57).

Trivy 0.70.0 panicked on the bootstrap that ``ayasee init`` writes; the job then failed in
``sarif-scrub`` with a bare FileNotFoundError. Each step that reads a scanner's report now checks
that the report exists and fails with an ``::error::`` that names the scanner.
"""

import os
import re
import subprocess

import pytest
import yaml
from conftest import ROOT, WORKFLOWS
from conftest import index as _index
from conftest import steps as _steps

# the Trivy version every trivy-action step states, and the one the regression test runs
INSTALL = (ROOT / "scripts" / "install-dev-tools.sh").read_text()


def _wf(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _run_step(step: dict, env: dict, tmp_path) -> subprocess.CompletedProcess:
    """Runs a step's script as GitHub does (bash -e), with ayasee absent from PATH."""
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        env={"PATH": "/usr/bin:/bin", "RUNNER_TEMP": str(tmp_path), **env},
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )


SCRUB_STEPS = [
    ("iac-pr.yml", "checks", "Trivy"),
    ("iac-pr.yml", "checks", "Checkov"),
    ("iac-nightly.yml", "rescan", "Trivy"),
    ("iac-nightly.yml", "rescan", "Checkov"),
]


@pytest.mark.parametrize(("workflow", "job", "scanner"), SCRUB_STEPS)
def test_a_missing_sarif_fails_the_scrub_step_as_a_scanner_crash(workflow, job, scanner, tmp_path):
    steps = _steps(_wf(workflow), job)
    step = steps[_index(steps, lambda s: s.get("name") == f"Scrub the {scanner} SARIF")]
    missing = tmp_path / "sarif" / "never-written.sarif"
    env = {k: str(v) for k, v in step["env"].items() if not str(v).startswith("${{")}
    env |= {"SARIF_IN": str(missing), "SARIF_OUT": str(tmp_path / "out.sarif"), "IAC_ROOT": "."}
    done = _run_step(step, env, tmp_path)
    assert done.returncode == 1, done.stdout + done.stderr
    assert f"::error::{scanner} wrote no SARIF" in done.stdout
    assert "crashed" in done.stdout
    assert not (tmp_path / "out.sarif").exists()


def test_the_nightly_trivy_scrub_runs_after_a_trivy_failure():
    # without it, a crash would stop the job at the Trivy step with no word about the report
    steps = _steps(_wf("iac-nightly.yml"), "rescan")
    step = steps[_index(steps, lambda s: s.get("name") == "Scrub the Trivy SARIF")]
    assert step["if"] == "${{ !cancelled() && steps.trivy.outcome != 'skipped' }}"


def test_both_pr_scanners_still_run_after_an_earlier_failure():
    steps = _steps(_wf("iac-pr.yml"), "checks")
    for name in ("Scrub the Trivy SARIF", "Scrub the Checkov SARIF"):
        step = steps[_index(steps, lambda s, name=name: s.get("name") == name)]
        assert step["if"].startswith("${{ !cancelled() && steps."), name
    checkov = steps[_index(steps, lambda s: s.get("id") == "checkov")]
    assert checkov["if"] == "${{ !cancelled() }}"


def test_a_missing_prod_plan_report_fails_as_a_trivy_crash(tmp_path):
    steps = _steps(_wf("iac-staged.yml"), "prod-plan")
    step = steps[
        _index(steps, lambda s: s.get("name") == "List Trivy findings (ids and severities only)")
    ]
    done = _run_step(step, {"REPORT": str(tmp_path / "trivy-prod-plan.report")}, tmp_path)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "::error::Trivy wrote no report" in done.stdout


def test_a_prod_plan_report_still_lists_ids_and_severities_only(tmp_path):
    report = tmp_path / "r.json"
    report.write_text(
        '{"Results":[{"Target":"a/b/plan.json","Misconfigurations":'
        '[{"Severity":"HIGH","ID":"AVD-1","Message":"ayasee-fixture-secret-marker"}]}]}'
    )
    steps = _steps(_wf("iac-staged.yml"), "prod-plan")
    step = steps[
        _index(steps, lambda s: s.get("name") == "List Trivy findings (ids and severities only)")
    ]
    env = {"REPORT": str(report), "PATH": os.environ["PATH"]}
    done = _run_step(step, env, tmp_path)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "HIGH AVD-1 plan.json"


def _trivy_steps():
    for name in ("iac-pr.yml", "iac-nightly.yml", "iac-staged.yml"):
        wf = _wf(name)
        for job, body in wf["jobs"].items():
            for step in body.get("steps", []):
                if "aquasecurity/trivy-action@" in step.get("uses", ""):
                    yield name, job, step


def test_every_trivy_step_states_the_version_the_dev_tools_install():
    pinned = re.search(r"^TRIVY_VERSION=(\d+\.\d+\.\d+)$", INSTALL, re.M)
    assert pinned, "scripts/install-dev-tools.sh pins TRIVY_VERSION"
    found = list(_trivy_steps())
    assert len(found) == 3
    for name, job, step in found:
        assert step["with"].get("version") == f"v{pinned[1]}", (name, job)
    assert re.search(r"^TRIVY_SHA256=[0-9a-f]{64} ", INSTALL, re.M)
