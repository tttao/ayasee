"""The trunk-guard step (issue #11): the staged and nightly runs warn when a ledger token is passed
and main is not behind a pull-request rule that token cannot bypass."""

import pytest
import yaml
from conftest import WORKFLOWS
from conftest import index as _index
from conftest import steps as _steps


def _load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


@pytest.mark.parametrize("name", ["iac-staged.yml", "iac-nightly.yml"])
def test_resolve_checks_main_with_the_ledger_token(name):
    wf = _load(name)
    steps = _steps(wf, "resolve")
    i = _index(steps, lambda s: "ayasee trunk-guard" in s.get("run", ""))
    step = steps[i]
    # the calls are made with the ledger token so that current_user_can_bypass is about it
    assert step["env"]["GITHUB_TOKEN"] == "${{ secrets.ledger-token }}"
    assert step["env"]["REPO"] == "${{ github.repository }}"
    run = step["run"]
    # skipped without a ledger token; a warning and a summary line otherwise, never a pass-through
    assert '[ -n "$GITHUB_TOKEN" ] || exit 0' in run
    assert "::warning::" in run and "GITHUB_STEP_SUMMARY" in run
    assert "continue-on-error" not in step and "if" not in step
    setup = _index(steps, lambda s: "/actions/setup@" in s.get("uses", ""))
    assert setup < i < _index(steps, lambda s: s.get("id") == "resolve")
    # no new permission: the ledger token reads with Metadata read, github.token is not used
    assert wf["jobs"]["resolve"]["permissions"] == {"contents": "read"}
    # only this step of the job sees the token
    others = [s for j, s in enumerate(steps) if j != i]
    assert "ledger-token" not in yaml.safe_dump(others)
    assert "ledger-token" not in yaml.safe_dump(wf["jobs"]["resolve"].get("env", {}))
