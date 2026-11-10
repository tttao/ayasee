"""Static checks on the e2e workflow, the fixture and the seed script."""

import subprocess
from pathlib import Path

import yaml

from ayasee.config import load_config

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "e2e" / "fixture"
E2E_YML = ROOT / ".github" / "workflows" / "e2e.yml"


def _wf():
    return yaml.safe_load(E2E_YML.read_text())


def test_e2e_workflow_is_manual_and_nightly_on_the_canonical_repo_only():
    wf = _wf()
    assert set(wf[True]) == {"workflow_dispatch", "schedule"}
    assert wf["permissions"] == {}
    job = wf["jobs"]["e2e"]
    assert job["if"] == "${{ github.repository == 'OWNER/ayasee' }}"
    assert job["environment"] == "e2e"
    assert job["permissions"] == {"contents": "read"}
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert "python -m e2e.run --all" in runs


def test_e2e_workflow_pins_the_app_token_action_and_limits_its_permissions():
    steps = _wf()["jobs"]["e2e"]["steps"]
    app = next(s for s in steps if s.get("uses", "").startswith("actions/create-github-app-token@"))
    assert len(app["uses"].split("@")[1].split()[0]) == 40
    assert any(k.startswith("permission-") for k in app["with"])


def test_e2e_workflow_never_inlines_inputs_into_a_script():
    for s in _wf()["jobs"]["e2e"]["steps"]:
        assert "${{" not in s.get("run", "")


def test_fixture_has_base_and_grants_for_every_stage():
    for stage in ("dev", "uat", "prod"):
        for unit in ("base", "grants"):
            assert (FIXTURE / "stages" / stage / unit / "terragrunt.hcl").is_file()
            assert (FIXTURE / "stages" / stage / unit / "main.tf").is_file()
    assert (FIXTURE / "root.hcl").is_file()


def test_grants_use_outputs_of_base_through_a_dependency_block():
    for stage in ("dev", "uat", "prod"):
        text = (FIXTURE / "stages" / stage / "grants" / "terragrunt.hcl").read_text()
        assert 'dependency "base"' in text
        assert "dependency.base.outputs." in text


def test_fixture_config_uses_a_git_branch_ledger():
    cfg = load_config(FIXTURE / ".ayasee.yaml")
    assert cfg.ledger.url.startswith("git-branch://")
    assert cfg.stages == ["dev", "uat", "prod"]


def test_fixture_callers_exist():
    for name in ("pr", "main", "nightly", "breakglass"):
        assert (FIXTURE / ".github" / "workflows" / f"{name}.yml").is_file()


def test_seed_refuses_a_non_empty_directory_and_a_bad_sha(tmp_path):
    seed = ROOT / "e2e" / "seed.sh"
    (tmp_path / "x").write_text("x")
    bad = subprocess.run([seed, str(tmp_path), "o/r", "0" * 40], capture_output=True, text=True)
    assert bad.returncode != 0 and "not empty" in bad.stderr
    short = subprocess.run(
        [seed, str(tmp_path / "n"), "o/r", "abc"], capture_output=True, text=True
    )
    assert short.returncode != 0 and "40-character" in short.stderr


def test_dependencies_are_installed_before_the_app_token_is_minted():
    steps = _wf()["jobs"]["e2e"]["steps"]
    names = [s.get("run", s.get("uses", "")) for s in steps]
    sync = next(i for i, n in enumerate(names) if n.strip() == "uv sync --frozen")
    token = next(i for i, n in enumerate(names) if n.startswith("actions/create-github-app-token"))
    assert sync < token


def test_fixture_callers_pass_the_state_token_except_pr():
    for name in ("main", "nightly", "breakglass"):
        text = (FIXTURE / ".github" / "workflows" / f"{name}.yml").read_text()
        assert "extra-env-secret: ${{ secrets.STATE_TOKEN }}" in text
    assert "STATE_TOKEN" not in (FIXTURE / ".github" / "workflows" / "pr.yml").read_text()
