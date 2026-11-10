"""`ayasee main-tip` (R70): prints the commit at the tip of main, fails closed."""

import httpx
import pytest
import respx

from ayasee import cli, maintip  # noqa: F401  (registers the subcommand)

API = "https://api.github.com"
TIP = "c" * 40


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_API_URL", raising=False)


@respx.mock
def test_prints_the_tip_of_main(capsys):
    route = respx.get(f"{API}/repos/acme/infra/commits/main").mock(
        return_value=httpx.Response(200, json={"sha": TIP})
    )
    assert cli.main(["main-tip", "--repo", "acme/infra"]) == 0
    assert capsys.readouterr().out.strip() == TIP
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok"


@respx.mock
@pytest.mark.parametrize(
    "response", [httpx.Response(404, json={}), httpx.Response(200, json={"nope": 1})]
)
def test_api_failure_fails_closed(response, capsys):
    respx.get(f"{API}/repos/acme/infra/commits/main").mock(return_value=response)
    assert cli.main(["main-tip", "--repo", "acme/infra"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "main-tip: error" in captured.err


def test_missing_token_fails_closed(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN")
    assert cli.main(["main-tip", "--repo", "acme/infra"]) == 1
    assert "GITHUB_TOKEN" in capsys.readouterr().err


def test_bad_repo_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        cli.main(["main-tip", "--repo", "not a repo"])
    assert exc.value.code == 2
