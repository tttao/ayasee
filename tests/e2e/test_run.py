"""The runner: sandbox-only guard, scenario selection, exit codes."""

import httpx
import pytest
import respx
from e2e.sandbox import ScenarioFailure
from e2e.scenarios import SCENARIOS

from e2e import run as runner

ENV = {
    "E2E_REPO": "o/ayasee-sandbox",
    "E2E_TOKEN": "maintainer",
    "E2E_APPROVER_TOKEN": "approver",
}


@pytest.mark.parametrize(
    "repo",
    [
        "",
        "o/ayasee",
        "o/ayasee-sandbox-old",
        "ayasee-sandbox",
        "a/b/c-sandbox",
        "/x-sandbox",
    ],
)
def test_refuses_a_repo_that_is_not_a_sandbox(repo, capsys, monkeypatch):
    built = []
    monkeypatch.setattr(runner, "run_scenarios", lambda *a: built.append(a) or {})
    assert runner.main(["--all"], {**ENV, "E2E_REPO": repo}) == 2
    assert "E2E_REPO" in capsys.readouterr().err
    assert built == []


def test_refuses_missing_or_identical_tokens(capsys):
    assert runner.main(["--all"], {"E2E_REPO": ENV["E2E_REPO"]}) == 2
    same = {**ENV, "E2E_APPROVER_TOKEN": ENV["E2E_TOKEN"]}
    assert runner.main(["--all"], same) == 2
    assert "different" in capsys.readouterr().err


def test_unknown_scenario(capsys):
    assert runner.main(["--scenario", "nope"], ENV) == 2
    assert "unknown scenario" in capsys.readouterr().err


def test_requires_all_or_scenario():
    with pytest.raises(SystemExit):
        runner.main([], ENV)


def test_all_runs_every_scenario_in_order_and_reports(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(runner, "load", lambda n: lambda sb: ran.append(n))
    monkeypatch.setattr(runner, "build_sandbox", lambda env: _Quiet())
    assert runner.main(["--all"], ENV) == 0
    assert ran == SCENARIOS
    assert f"{len(SCENARIOS)}/{len(SCENARIOS)} scenarios passed" in capsys.readouterr().out


def test_a_failing_scenario_fails_the_run_but_not_the_others(monkeypatch, capsys):
    ran = []

    def fake_load(name):
        def scenario(sb):
            ran.append(name)
            if name == "scan_fail":
                raise ScenarioFailure("boom")

        return scenario

    monkeypatch.setattr(runner, "load", fake_load)
    monkeypatch.setattr(runner, "build_sandbox", lambda env: _Quiet())
    assert runner.main(["--all"], ENV) == 1
    assert ran == SCENARIOS
    assert "FAIL scan_fail: ScenarioFailure: boom" in capsys.readouterr().out


def test_single_scenario(monkeypatch):
    ran = []
    monkeypatch.setattr(runner, "load", lambda n: lambda sb: ran.append(n))
    monkeypatch.setattr(runner, "build_sandbox", lambda env: _Quiet())
    assert runner.main(["--scenario", "valid"], ENV) == 0
    assert ran == ["valid"]


def _users(maint="alice", approver="bot-reviewer"):
    def answer(request):
        token = request.headers["authorization"]
        who = {"Bearer maintainer": maint, "Bearer approver": approver}[token]
        if who is None:
            return httpx.Response(403, json={"message": "Resource not accessible by integration"})
        return httpx.Response(200, json={"login": who})

    respx.get("https://api.github.com/user").mock(side_effect=answer)


@respx.mock
def test_build_sandbox_wires_two_identities():
    _users()
    sb = runner.build_sandbox({**ENV, "E2E_WEBHOOK_URL": "https://echo.example/e"})
    assert sb.repo == ENV["E2E_REPO"] and sb.webhook_url == "https://echo.example/e"
    assert sb.gh._client.headers["authorization"] == "Bearer maintainer"
    assert sb.approver_gh._client.headers["authorization"] == "Bearer approver"


@respx.mock
def test_two_tokens_of_one_user_are_refused(capsys):
    _users(maint="alice", approver="alice")
    assert runner.main(["--all"], ENV) == 2
    assert "second identity" in capsys.readouterr().err


@respx.mock
def test_an_app_installation_token_has_no_user_and_is_accepted():
    _users(maint="alice", approver=None)
    assert runner.build_sandbox(ENV).repo == ENV["E2E_REPO"]


@respx.mock
def test_an_unidentifiable_maintainer_token_is_refused(capsys):
    respx.get("https://api.github.com/user").mock(return_value=httpx.Response(500))
    assert runner.main(["--all"], ENV) == 2
    assert "cannot identify" in capsys.readouterr().err


@respx.mock
def test_a_rejected_token_is_a_clear_error_not_a_distinct_identity(capsys):
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"})
    )
    assert runner.main(["--all"], ENV) == 2
    assert "HTTP 401" in capsys.readouterr().err


@pytest.mark.parametrize("url", ["http://echo.example/e", "ftp://x", "echo.example/e"])
def test_webhook_url_must_be_https(url, capsys):
    assert runner.main(["--all"], {**ENV, "E2E_WEBHOOK_URL": url}) == 2
    assert "https" in capsys.readouterr().err


class _Quiet:
    def cleanup(self):
        pass
