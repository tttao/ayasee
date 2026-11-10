"""Static checks on every composite action.yml."""

import re
from pathlib import Path

import pytest
import yaml

ACTIONS = Path(__file__).resolve().parents[2] / "actions"
FILES = sorted(ACTIONS.glob("*/action.yml"))
EXPECTED = {"setup", "plan", "apply", "risk", "guard", "policy", "ledger", "notify", "cockpit-site"}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def test_all_expected_actions_exist():
    assert {p.parent.name for p in FILES} == EXPECTED


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.parent.name)
def test_all_actions_pin_uses_by_sha(path):
    for step in _load(path)["runs"]["steps"]:
        if "uses" in step:
            assert re.search(r"@[0-9a-f]{40}$", step["uses"]), f"{path}: {step['uses']}"


def test_plan_never_uploads_json():
    path = ACTIONS / "plan" / "action.yml"
    for step in _load(path)["runs"]["steps"]:
        uses = step.get("uses", "")
        assert "upload-artifact" not in uses, "the plan action uploads nothing"
    for script in (ACTIONS / "plan").glob("*.sh"):
        assert "upload" not in script.read_text().lower()


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.parent.name)
def test_inputs_documented(path):
    inputs = _load(path).get("inputs", {})
    assert inputs, f"{path} declares no inputs"
    for name, spec in inputs.items():
        assert spec.get("description", "").strip(), f"{path}: input {name} has no description"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.parent.name)
def test_no_input_interpolation_in_run(path):
    for step in _load(path)["runs"]["steps"]:
        assert "${{ inputs." not in step.get("run", ""), f"{path}: template injection risk"
        assert "${{" not in step.get("run", ""), f"{path}: pass values via env:"


def test_cockpit_site_action_shape():
    action = _load(ACTIONS / "cockpit-site" / "action.yml")
    inputs = action["inputs"]
    assert inputs["snapshot"]["required"] is True
    assert inputs["out"]["required"] is True
    assert inputs["history-dir"]["required"] is False
    steps = action["runs"]["steps"]
    node = next(s for s in steps if "setup-node" in s.get("uses", ""))
    assert node["with"]["package-manager-cache"] is False, "no dependency cache (R40 spirit)"
    assert "cache" not in node["with"]
    runs = "\n".join(s.get("run", "") for s in steps)
    assert "npm ci --ignore-scripts" in runs
    assert "npm run build" in runs
    assert "snapshot.json" in runs and "history" in runs
