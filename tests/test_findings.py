import json
from datetime import UTC, datetime, timedelta

import httpx
import respx

from ayasee import cli
from ayasee.findings import count_new
from ayasee.github import GitHub, GitHubError

BASE = "https://ghe.example/api/v3"
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def ago(h):
    return (NOW - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")


@respx.mock
def test_update_issue_sends_only_given_fields():
    route = respx.patch(f"{BASE}/repos/o/n/issues/4").mock(
        return_value=httpx.Response(200, json={})
    )
    gh = GitHub(BASE, "t")
    gh.update_issue("o/n", 4, state="closed")
    assert json.loads(route.calls[0].request.content) == {"state": "closed"}
    gh.update_issue("o/n", 4, labels=["a"])
    assert json.loads(route.calls[1].request.content) == {"labels": ["a"]}
    gh.update_issue("o/n", 4, body="hi")
    assert json.loads(route.calls[2].request.content) == {"body": "hi"}


@respx.mock
def test_get_issue_returns_the_issue():
    respx.get(f"{BASE}/repos/o/n/issues/4").mock(
        return_value=httpx.Response(200, json={"number": 4, "body": "x"})
    )
    assert GitHub(BASE, "t").get_issue("o/n", 4)["body"] == "x"


@respx.mock
def test_create_issue_posts_body():
    route = respx.post(f"{BASE}/repos/o/n/issues").mock(
        return_value=httpx.Response(201, json={"number": 2})
    )
    assert GitHub(BASE, "t").create_issue("o/n", "T", "B", ["l"]) == 2
    assert json.loads(route.calls[0].request.content) == {
        "title": "T",
        "body": "B",
        "labels": ["l"],
    }


@respx.mock
def test_list_code_scanning_alerts_params_and_pagination():
    first = respx.get(f"{BASE}/repos/o/n/code-scanning/alerts").mock(
        side_effect=[
            httpx.Response(
                200,
                json=[{"number": 1}],
                headers={"Link": f'<{BASE}/repos/o/n/code-scanning/alerts?page=2>; rel="next"'},
            ),
            httpx.Response(200, json=[{"number": 2}]),
        ]
    )
    alerts = GitHub(BASE, "t").list_code_scanning_alerts("o/n", severity="high")
    assert [a["number"] for a in alerts] == [1, 2]
    params = dict(first.calls[0].request.url.params)
    assert params["state"] == "open" and params["severity"] == "high"


@respx.mock
def test_list_code_scanning_alerts_refuses_foreign_next_link():
    respx.get(f"{BASE}/repos/o/n/code-scanning/alerts").mock(
        return_value=httpx.Response(
            200, json=[], headers={"Link": '<https://evil.example/x>; rel="next"'}
        )
    )
    try:
        GitHub(BASE, "t").list_code_scanning_alerts("o/n")
    except GitHubError as exc:
        assert "different host" in str(exc)
    else:
        raise AssertionError("expected GitHubError")


class FakeAlerts:
    def list_code_scanning_alerts(self, repo, state="open", severity=None):
        data = {
            "high": [{"number": 1, "created_at": ago(1)}, {"number": 2, "created_at": ago(30)}],
            "critical": [{"number": 3, "created_at": ago(25)}, {"number": 1, "created_at": ago(1)}],
        }
        return data[severity]


def test_count_new_window_and_dedupe():
    assert count_new(FakeAlerts(), "o/n", 26, now=NOW) == 2
    assert count_new(FakeAlerts(), "o/n", 2, now=NOW) == 1


@respx.mock
def test_cli_findings_new_prints_count(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    respx.get(f"{BASE}/repos/o/n/code-scanning/alerts").mock(
        return_value=httpx.Response(200, json=[{"number": 1, "created_at": stamp}])
    )
    assert cli.main(["findings", "new", "--repo", "o/n", "--since-hours", "26"]) == 0
    assert capsys.readouterr().out.strip() == "1"
