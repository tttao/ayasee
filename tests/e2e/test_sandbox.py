"""Sandbox against respx: request shapes, polling, the second identity, ledger reads, cards."""

import base64
import json

import httpx
import pytest
import respx
from e2e.sandbox import (
    LEDGER_BRANCH,
    STATE_BRANCH,
    NotASandbox,
    Sandbox,
    SandboxTimeout,
    parse_card,
)

from ayasee.github import GitHub, GitHubError
from ayasee.ledger import UnitResult, record_from_run

BASE = "https://api.example"
REPO = "o/n-sandbox"
API = f"{BASE}/repos/{REPO}"


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def sb(clock):
    return Sandbox(
        GitHub(BASE, "maint"),
        GitHub(BASE, "approver"),
        REPO,
        "https://echo.example/events",
        sleep=clock.sleep,
        clock=clock.time,
    )


def b64(text):
    return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


def rec(env, sha, at, incident=None):
    return record_from_run(
        repo=REPO,
        env=env,
        sha=sha * 12,
        run_id=1,
        run_attempt=1,
        units={"base": UnitResult("applied", True)},
        breakglass_incident=incident,
        now=at,
    )


@respx.mock
def test_push_commit_to_existing_branch(sb):
    respx.get(f"{API}/git/ref/heads/main").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "p1"}})
    )
    respx.get(f"{API}/git/commits/p1").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": "t1"}})
    )
    tree = respx.post(f"{API}/git/trees").mock(return_value=httpx.Response(201, json={"sha": "t2"}))
    commit = respx.post(f"{API}/git/commits").mock(
        return_value=httpx.Response(201, json={"sha": "c2"})
    )
    update = respx.patch(f"{API}/git/refs/heads/main").mock(
        return_value=httpx.Response(200, json={})
    )
    assert sb.push_commit({"a": "1", "b": None}, "msg") == "c2"
    assert json.loads(tree.calls[0].request.content)["base_tree"] == "t1"
    assert json.loads(commit.calls[0].request.content)["parents"] == ["p1"]
    assert json.loads(update.calls[0].request.content)["sha"] == "c2"
    assert update.calls[0].request.headers["authorization"] == "Bearer maint"


@respx.mock
def test_push_commit_creates_missing_branch_from_main(sb):
    respx.get(f"{API}/git/ref/heads/feat").mock(return_value=httpx.Response(404, json={}))
    respx.get(f"{API}/git/ref/heads/main").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "p1"}})
    )
    respx.get(f"{API}/git/commits/p1").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": "t1"}})
    )
    respx.post(f"{API}/git/trees").mock(return_value=httpx.Response(201, json={"sha": "t2"}))
    respx.post(f"{API}/git/commits").mock(return_value=httpx.Response(201, json={"sha": "c2"}))
    create = respx.post(f"{API}/git/refs").mock(return_value=httpx.Response(201, json={}))
    assert sb.push_commit({"a": "1"}, "m", branch="feat") == "c2"
    assert json.loads(create.calls[0].request.content) == {"ref": "refs/heads/feat", "sha": "c2"}


@respx.mock
def test_ship_opens_and_merges_a_pull_request(sb):
    respx.get(f"{API}/git/ref/heads/main").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "p1"}})
    )
    respx.get(url__regex=rf"{API}/git/ref/heads/e2e/.*").mock(return_value=httpx.Response(404))
    respx.get(f"{API}/git/commits/p1").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": "t1"}})
    )
    respx.post(f"{API}/git/trees").mock(return_value=httpx.Response(201, json={"sha": "t2"}))
    respx.post(f"{API}/git/commits").mock(return_value=httpx.Response(201, json={"sha": "c2"}))
    respx.post(f"{API}/git/refs").mock(return_value=httpx.Response(201, json={}))
    pr = respx.post(f"{API}/pulls").mock(return_value=httpx.Response(201, json={"number": 7}))
    respx.get(f"{API}/pulls").mock(return_value=httpx.Response(200, json=[{"number": 7}]))
    merge = respx.put(f"{API}/pulls/7/merge").mock(
        return_value=httpx.Response(200, json={"sha": "merge1"})
    )
    assert sb.ship({"a": "1"}, "m") == "merge1"
    assert json.loads(pr.calls[0].request.content)["base"] == "main"
    assert merge.called


@respx.mock
def test_wait_for_run_polls_with_backoff_until_completed(sb, clock):
    runs = [
        [],
        [{"id": 1, "head_sha": "abc", "status": "in_progress"}],
        [{"id": 1, "head_sha": "abc", "status": "completed", "conclusion": "success"}],
    ]
    route = respx.get(f"{API}/actions/workflows/main.yml/runs").mock(
        side_effect=lambda req: httpx.Response(200, json={"workflow_runs": runs.pop(0)})
    )
    run = sb.wait_for_run("main.yml", "abc", 600)
    assert run["conclusion"] == "success"
    assert route.call_count == 3
    assert clock.sleeps == [2.0, 3.0]


@respx.mock
def test_wait_for_run_ignores_other_shas_and_picks_newest(sb):
    respx.get(f"{API}/actions/workflows/main.yml/runs").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_runs": [
                    {"id": 5, "head_sha": "other", "status": "completed"},
                    {"id": 2, "head_sha": "abc", "status": "completed"},
                    {"id": 3, "head_sha": "abc", "status": "completed"},
                ]
            },
        )
    )
    assert sb.wait_for_run("main.yml", "abc", 60)["id"] == 3


@respx.mock
def test_wait_for_run_until_waiting_and_any(sb):
    respx.get(f"{API}/actions/workflows/main.yml/runs").mock(
        return_value=httpx.Response(
            200, json={"workflow_runs": [{"id": 1, "head_sha": "abc", "status": "waiting"}]}
        )
    )
    assert sb.wait_for_run("main.yml", "abc", 60, until="waiting")["id"] == 1
    assert sb.wait_for_run("main.yml", "abc", 60, until="any")["id"] == 1
    with pytest.raises(ValueError):
        sb.wait_for_run("main.yml", "abc", 60, until="bogus")


@respx.mock
def test_wait_for_run_hard_timeout(sb, clock):
    respx.get(f"{API}/actions/workflows/main.yml/runs").mock(
        return_value=httpx.Response(200, json={"workflow_runs": []})
    )
    with pytest.raises(SandboxTimeout, match="main.yml"):
        sb.wait_for_run("main.yml", "abc", 100)
    assert clock.now <= 100
    assert max(clock.sleeps) <= 30.0


@respx.mock
def test_dispatch_returns_only_the_new_run(sb):
    seen = [
        [{"id": 1}],
        [{"id": 1}],
        [{"id": 1}, {"id": 2}],
    ]
    respx.get(f"{API}/actions/workflows/nightly.yml/runs").mock(
        side_effect=lambda req: httpx.Response(200, json={"workflow_runs": seen.pop(0)})
    )
    disp = respx.post(f"{API}/actions/workflows/nightly.yml/dispatches").mock(
        return_value=httpx.Response(204)
    )
    assert sb.dispatch("nightly.yml", {"x": "y"}, 60)["id"] == 2
    assert json.loads(disp.calls[0].request.content) == {"ref": "main", "inputs": {"x": "y"}}


@respx.mock
def test_job_lookup_matches_reusable_workflow_names(sb):
    respx.get(f"{API}/actions/runs/9/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {"id": 1, "name": "staged / prod-plan", "conclusion": "success"},
                    {"id": 2, "name": "staged / prod", "conclusion": "failure"},
                    {"id": 3, "name": "lone", "conclusion": None, "status": "queued"},
                ]
            },
        )
    )
    assert sb.job_conclusion(9, "prod") == "failure"
    assert sb.job_conclusion(9, "prod-plan") == "success"
    assert sb.job_conclusion(9, "lone") == "queued"
    assert sb.job_conclusion(9, "nope") is None


@respx.mock
def test_review_uses_the_approver_identity_and_the_environment_id(sb):
    respx.get(f"{API}/actions/runs/9/pending_deployments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"environment": {"id": 11, "name": "prod-plan"}},
                {"environment": {"id": 12, "name": "prod"}},
            ],
        )
    )
    post = respx.post(f"{API}/actions/runs/9/pending_deployments").mock(
        return_value=httpx.Response(200, json=[])
    )
    sb.review(9, "prod", approve=True)
    sb.review(9, "prod", approve=False)
    first, second = (json.loads(c.request.content) for c in post.calls)
    assert first["environment_ids"] == [12] and first["state"] == "approved"
    assert second["state"] == "rejected"
    assert all(c.request.headers["authorization"] == "Bearer approver" for c in post.calls)


@respx.mock
def test_review_without_a_waiting_environment_raises(sb):
    respx.get(f"{API}/actions/runs/9/pending_deployments").mock(
        return_value=httpx.Response(200, json=[])
    )
    with pytest.raises(GitHubError, match="not waiting"):
        sb.review(9, "prod", approve=True)


@respx.mock
def test_wait_for_pending(sb):
    answers = [[], [{"environment": {"id": 1, "name": "prod"}}]]
    respx.get(f"{API}/actions/runs/9/pending_deployments").mock(
        side_effect=lambda req: httpx.Response(200, json=answers.pop(0))
    )
    assert sb.wait_for_pending(9, "prod", 60)["environment"]["id"] == 1


@respx.mock
def test_ledger_returns_the_newest_of_current_and_history(sb):
    old, new = (
        rec("prod", "a", "2026-11-06T10:00:00.000000Z"),
        rec("prod", "b", "2026-11-06T11:00:00.000000Z", incident="INC123"),
    )
    base = f"{API}/contents/ayasee-ledger/{REPO}/prod"
    respx.get(f"{base}/current.json", params={"ref": LEDGER_BRANCH}).mock(
        return_value=httpx.Response(200, json=b64(json.dumps(old.to_dict())))
    )
    respx.get(f"{base}/history", params={"ref": LEDGER_BRANCH}).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "20261106T100000000000Z-aaaaaaaaaaaa.json"},
                {"name": "20261106T110000000000Z-bbbbbbbbbbbb.json"},
            ],
        )
    )
    respx.get(f"{base}/history/20261106T110000000000Z-bbbbbbbbbbbb.json").mock(
        return_value=httpx.Response(200, json=b64(json.dumps(new.to_dict())))
    )
    got = sb.ledger("prod")
    assert got == new and got.breakglass_incident == "INC123"


@respx.mock
def test_ledger_missing_is_none(sb):
    respx.get(url__startswith=f"{API}/contents/").mock(return_value=httpx.Response(404, json={}))
    assert sb.ledger("prod") is None


@respx.mock
def test_issues_filters_by_label_and_state(sb):
    route = respx.get(f"{API}/issues").mock(
        return_value=httpx.Response(200, json=[{"number": 1}, {"number": 2, "pull_request": {}}])
    )
    assert sb.issues(["a", "b"], state="closed") == [{"number": 1}]
    params = route.calls[0].request.url.params
    assert params["labels"] == "a,b" and params["state"] == "closed"


@respx.mock
def test_cleanup_closes_ayasee_issues(sb):
    respx.get(f"{API}/issues").mock(return_value=httpx.Response(200, json=[{"number": 4}]))
    patch = respx.patch(f"{API}/issues/4").mock(return_value=httpx.Response(200, json={}))
    sb.cleanup()
    assert patch.call_count == 3  # once per label; the fake list returns the same issue
    assert json.loads(patch.calls[0].request.content) == {"state": "closed"}


@respx.mock
def test_edit_state_bumps_serial_and_commits_to_the_state_branch(sb):
    state = {"serial": 3, "resources": [{"name": "marker"}]}
    respx.get(f"{API}/contents/s.tfstate", params={"ref": STATE_BRANCH}).mock(
        return_value=httpx.Response(200, json=b64(json.dumps(state)))
    )
    respx.get(f"{API}/git/ref/heads/{STATE_BRANCH}").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "p1"}})
    )
    respx.get(f"{API}/git/commits/p1").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": "t1"}})
    )
    tree = respx.post(f"{API}/git/trees").mock(return_value=httpx.Response(201, json={"sha": "t2"}))
    respx.post(f"{API}/git/commits").mock(return_value=httpx.Response(201, json={"sha": "c2"}))
    update = respx.patch(f"{API}/git/refs/heads/{STATE_BRANCH}").mock(
        return_value=httpx.Response(200, json={})
    )
    sb.edit_state("s.tfstate", lambda s: s["resources"][0].update(name="changed"))
    written = json.loads(json.loads(tree.calls[0].request.content)["tree"][0]["content"])
    assert written["serial"] == 4 and written["resources"][0]["name"] == "changed"
    assert update.called


CARD = {
    "type": "message",
    "attachments": [
        {
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "type": "AdaptiveCard",
                "body": [
                    {"type": "TextBlock", "text": "Prod rejected · o/n"},
                    {"type": "TextBlock", "text": "Rejected"},
                    {
                        "type": "FactSet",
                        "facts": [
                            {"title": "Environment", "value": "prod"},
                            {"title": "Commit", "value": "abcdef123456"},
                        ],
                    },
                ],
            },
        }
    ],
}


def test_parse_card():
    ev = parse_card(CARD)
    assert ev.title.startswith("Prod rejected") and ev.commit == "abcdef123456"
    assert ev.env == "prod" and ev.status == "Rejected"
    assert parse_card({"hello": "world"}) is None
    assert parse_card("text") is None


@respx.mock
def test_webhook_events_reads_the_echo_and_filters_by_sha(sb):
    respx.get("https://echo.example/events").mock(
        return_value=httpx.Response(200, json=[CARD, {"junk": 1}])
    )
    assert len(sb.webhook_events()) == 1
    assert len(sb.events_for("abcdef1234567890")) == 1
    assert sb.events_for("0" * 40) == []


@respx.mock
def test_webhook_events_errors(sb):
    respx.get("https://echo.example/events").mock(return_value=httpx.Response(200, json={"a": 1}))
    with pytest.raises(GitHubError, match="array"):
        sb.webhook_events()
    respx.get("https://echo.example/events").mock(return_value=httpx.Response(500))
    with pytest.raises(GitHubError, match="unreadable"):
        sb.webhook_events()
    with pytest.raises(GitHubError, match="not set"):
        Sandbox(GitHub(BASE, "a"), GitHub(BASE, "b"), REPO).webhook_events()


def test_the_sandbox_guard_lives_in_the_constructor():
    for bad in ("o/prod", "o/x-sandbox-y", "x-sandbox", "a/b/c-sandbox", ""):
        with pytest.raises(NotASandbox):
            Sandbox(GitHub(BASE, "a"), GitHub(BASE, "b"), bad)
    Sandbox(GitHub(BASE, "a"), GitHub(BASE, "b"), "o/x-sandbox")


@respx.mock
def test_wait_for_job_waits_until_the_job_finished(sb):
    answers = [
        {"jobs": [{"id": 1, "name": "staged / prod-plan", "status": "in_progress"}]},
        {
            "jobs": [
                {
                    "id": 1,
                    "name": "staged / prod-plan",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        },
    ]
    respx.get(f"{API}/actions/runs/9/jobs").mock(
        side_effect=lambda req: httpx.Response(200, json=answers.pop(0))
    )
    assert sb.wait_for_job(9, "prod-plan", 60)["conclusion"] == "success"


@respx.mock
def test_job_log_drops_the_authorization_header_on_the_cross_origin_redirect(sb):
    respx.get(f"{API}/actions/runs/9/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [{"id": 4, "name": "staged / prod"}]})
    )
    first = respx.get(f"{API}/actions/jobs/4/logs").mock(
        return_value=httpx.Response(302, headers={"location": "https://logs.blob.example/x?sig=1"})
    )
    second = respx.get("https://logs.blob.example/x").mock(
        return_value=httpx.Response(200, text="guard: rejected prod@abc: superseded")
    )
    assert "superseded" in sb.job_log(9, "prod")
    assert first.calls[0].request.headers["authorization"] == "Bearer maint"
    assert "authorization" not in second.calls[0].request.headers


@respx.mock
def test_review_as_self_uses_the_pushing_identity(sb):
    """Solo mode (#1): the maintainer who pushed approves their own gate."""
    respx.get(f"{API}/actions/runs/9/pending_deployments").mock(
        return_value=httpx.Response(200, json=[{"environment": {"id": 12, "name": "prod"}}])
    )
    post = respx.post(f"{API}/actions/runs/9/pending_deployments").mock(
        return_value=httpx.Response(200, json=[])
    )
    sb.review(9, "prod", approve=True, as_self=True)
    (call,) = post.calls
    assert call.request.headers["authorization"] == "Bearer maint"


@respx.mock
def test_login_is_read_once_and_read_file_uses_main(sb):
    user = respx.get(f"{BASE}/user").mock(return_value=httpx.Response(200, json={"login": "me"}))
    assert sb.login == "me" and sb.login == "me"
    assert user.call_count == 1
    content = base64.b64encode(b"stages: [dev, uat, prod]\n").decode()
    route = respx.get(f"{API}/contents/.ayasee.yaml", params={"ref": "main"}).mock(
        return_value=httpx.Response(200, json={"encoding": "base64", "content": content})
    )
    assert sb.read_file(".ayasee.yaml") == "stages: [dev, uat, prod]\n"
    assert route.called
