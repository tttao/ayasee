import json

import httpx
import pytest
import respx

from ayasee import approvals, cli, issues, labels
from ayasee.github import GitHub, GitHubError

BASE = "https://ghe.example/api/v3"


def _a(login, state="approved", envs=("prod",), **extra):
    return {
        "state": state,
        "user": {"login": login},
        "environments": [{"name": e} for e in envs],
        **extra,
    }


@respx.mock
def test_run_approvals_gets_list():
    route = respx.get(f"{BASE}/repos/o/n/actions/runs/9/approvals").mock(
        return_value=httpx.Response(200, json=[_a("alice")])
    )
    assert GitHub(BASE, "tok").run_approvals("o/n", 9)[0]["user"]["login"] == "alice"
    assert route.calls[0].request.headers["authorization"] == "Bearer tok"


@respx.mock
def test_run_approvals_error_hides_token():
    respx.get(f"{BASE}/repos/o/n/actions/runs/9/approvals").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    with pytest.raises(GitHubError) as ei:
        GitHub(BASE, "SECRET").run_approvals("o/n", 9)
    assert ei.value.status == 404 and "SECRET" not in str(ei.value)


def test_approvers_lists_distinct_sorted_logins():
    items = [_a("bob"), _a("alice", envs=("uat", "prod")), _a("bob")]
    assert approvals.approvers(items, "prod") == ["alice", "bob"]


def test_approvers_ignores_rejected_and_other_environments():
    items = [_a("rej", state="rejected"), _a("other", envs=("uat",)), _a("ok")]
    assert approvals.approvers(items, "prod") == ["ok"]
    assert approvals.approvers(items, "dev") == []


def test_approvers_none():
    assert approvals.approvers([], "prod") == []


@respx.mock
def test_cli_approvals_list(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    respx.get(f"{BASE}/repos/o/n/actions/runs/9/approvals").mock(
        return_value=httpx.Response(
            200, json=[_a("bob"), _a("alice"), _a("x", state="rejected"), _a("p", envs=("uat",))]
        )
    )
    argv = ["approvals", "list", "--repo", "o/n", "--run-id", "9", "--env"]
    assert cli.main([*argv, "prod"]) == 0
    assert capsys.readouterr().out.strip() == "alice,bob"
    assert cli.main([*argv, "dev"]) == 1
    assert "no approval" in capsys.readouterr().err


@respx.mock
def test_open_or_comment_comments_on_existing():
    list_route = respx.get(f"{BASE}/repos/o/n/issues").mock(
        return_value=httpx.Response(200, json=[{"number": 5}])
    )
    comment = respx.post(f"{BASE}/repos/o/n/issues/5/comments").mock(
        return_value=httpx.Response(201, json={})
    )
    assert GitHub(BASE, "t").open_or_comment_issue("o/n", "T", "B", ["a", "b"]) == 5
    assert dict(list_route.calls[0].request.url.params)["labels"] == "a,b"
    assert json.loads(comment.calls[0].request.content) == {"body": "B"}


@respx.mock
def test_open_or_comment_creates_when_none():
    respx.get(f"{BASE}/repos/o/n/issues").mock(return_value=httpx.Response(200, json=[]))
    create = respx.post(f"{BASE}/repos/o/n/issues").mock(
        return_value=httpx.Response(201, json={"number": 12})
    )
    assert GitHub(BASE, "t").open_or_comment_issue("o/n", "T", "B", ["a"]) == 12
    assert json.loads(create.calls[0].request.content) == {
        "title": "T",
        "body": "B",
        "labels": ["a"],
    }


@respx.mock
def test_cli_stop_the_line(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    respx.get(f"{BASE}/repos/o/n/issues").mock(return_value=httpx.Response(200, json=[]))
    create = respx.post(f"{BASE}/repos/o/n/issues").mock(
        return_value=httpx.Response(201, json={"number": 3})
    )
    sha = "a" * 40
    rc = cli.main(
        ["issue", "stop-the-line", "--repo", "o/n", "--env", "dev", "--sha", sha]
        + ["--run-url", "https://x/runs/1"]
    )
    assert rc == 0
    sent = json.loads(create.calls[0].request.content)
    assert sent["title"] == "stop-the-line: dev apply failed"
    assert sent["labels"] == [issues.STOP_LABEL, "ayasee:apply-failed", "env:dev"]
    assert sha[:12] in sent["body"] and sha not in sent["body"]
    assert "https://x/runs/1" in sent["body"]


def test_cli_stop_the_line_without_token(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    rc = cli.main(
        ["issue", "stop-the-line", "--repo", "o/n", "--env", "dev", "--sha", "a" * 40]
        + ["--run-url", "u"]
    )
    assert rc == 1


@respx.mock
def test_cli_issue_open_count(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    route = respx.get(f"{BASE}/repos/o/n/issues").mock(
        return_value=httpx.Response(200, json=[{"number": 1}, {"number": 2, "pull_request": {}}])
    )
    argv = ["issue", "open-count", "--repo", "o/n", "--label", "ayasee:apply-failed"]
    assert cli.main([*argv, "--label", "env:dev"]) == 0
    assert capsys.readouterr().out.strip() == "1"
    assert dict(route.calls[0].request.url.params)["labels"] == "ayasee:apply-failed,env:dev"


def _post_approval_argv(**over):
    args = {
        "--repo": "o/n",
        "--incident": "INC123",
        "--sha": "a" * 40,
        "--run-url": "https://x/runs/1",
        "--approvers": "alice,bob",
        **over,
    }
    return ["issue", "post-approval", *(x for kv in args.items() for x in kv)]


@respx.mock
def test_cli_post_approval_opens_issue_with_checklist(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    create = respx.post(f"{BASE}/repos/o/n/issues").mock(
        return_value=httpx.Response(201, json={"number": 8})
    )
    assert cli.main(_post_approval_argv()) == 0
    assert "#8" in capsys.readouterr().out
    sent = json.loads(create.calls[0].request.content)
    assert sent["title"] == "post-approval: break-glass INC123"
    assert sent["labels"] == [labels.POST_APPROVAL_LABEL, "env:prod"]
    assert labels.POST_APPROVAL_LABEL == "ayasee:post-approval"
    body = sent["body"]
    for item in (
        "Change reviewed by an independent approver",
        "Incident record linked",
        "Follow-up PR merged to main",
        "Drift check clean",
    ):
        assert f"- [ ] {item}" in body
    assert "`INC123`" in body and "`" + "a" * 12 + "`" in body and "a" * 13 not in body
    assert "https://x/runs/1" in body and "alice, bob" in body


@respx.mock
def test_cli_post_approval_always_opens_a_new_issue(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    listing = respx.get(f"{BASE}/repos/o/n/issues").mock(
        return_value=httpx.Response(200, json=[{"number": 1}])
    )
    create = respx.post(f"{BASE}/repos/o/n/issues").mock(
        return_value=httpx.Response(201, json={"number": 2})
    )
    assert cli.main(_post_approval_argv()) == 0
    assert cli.main(_post_approval_argv()) == 0
    assert create.call_count == 2 and not listing.called


@pytest.mark.parametrize(
    "over",
    [
        {"--incident": "inc1"},
        {"--incident": "INC1\nx"},
        {"--incident": "AB-1 `x`"},
        {"--sha": "abc"},
        {"--sha": "A" * 40},
        {"--run-url": "http://x/runs/1"},
        {"--run-url": "https://x/runs/1 `y`"},
        {"--rollback-from": "abc"},
        {"--approvers": ""},
        {"--approvers": "alice,bo b"},
    ],
)
def test_cli_post_approval_rejects_bad_input(monkeypatch, over):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    assert cli.main(_post_approval_argv(**over)) == 2


def test_cli_post_approval_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert cli.main(_post_approval_argv()) == 1


@respx.mock
def test_cli_post_approval_has_counts_only_and_rollback_line(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    cs = tmp_path / "cs.json"
    cs.write_text(
        json.dumps(
            {
                "risk": {"critical": 1, "high": 2, "medium": 3},
                "items": [{"address": "secret_thing.password", "unit": "u"}] * 6,
            }
        )
    )
    create = respx.post(f"{BASE}/repos/o/n/issues").mock(
        return_value=httpx.Response(201, json={"number": 4})
    )
    live = "b" * 40
    argv = _post_approval_argv(**{"--changeset": str(cs), "--rollback-from": live})
    assert cli.main(argv) == 0
    body = json.loads(create.calls[0].request.content)["body"]
    assert "Changes applied: critical 1, high 2, medium 3, total 6" in body
    assert f"Rollback from `{'b' * 12}`" in body and "b" * 13 not in body
    assert "secret_thing" not in body


@respx.mock
def test_cli_post_approval_without_changeset_or_rollback_has_neither(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    create = respx.post(f"{BASE}/repos/o/n/issues").mock(
        return_value=httpx.Response(201, json={"number": 4})
    )
    assert cli.main(_post_approval_argv()) == 0
    body = json.loads(create.calls[0].request.content)["body"]
    assert "Changes applied" not in body and "Rollback" not in body


def test_cli_post_approval_bad_changeset(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    assert cli.main(_post_approval_argv(**{"--changeset": str(tmp_path / "none.json")})) == 2


@respx.mock
def test_cli_approvals_rejected(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    respx.get(f"{BASE}/repos/o/n/actions/runs/9/approvals").mock(
        return_value=httpx.Response(
            200,
            json=[_a("bob"), _a("x", state="rejected"), _a("p", state="rejected", envs=("uat",))],
        )
    )
    argv = ["approvals", "rejected", "--repo", "o/n", "--run-id", "9", "--env"]
    assert cli.main([*argv, "prod"]) == 0
    assert cli.main([*argv, "uat"]) == 0
    assert cli.main([*argv, "dev"]) == 1


@respx.mock
def test_cli_approvals_rejected_only_approved_is_one(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    respx.get(f"{BASE}/repos/o/n/actions/runs/9/approvals").mock(
        return_value=httpx.Response(200, json=[_a("bob")])
    )
    argv = ["approvals", "rejected", "--repo", "o/n", "--run-id", "9", "--env", "prod"]
    assert cli.main(argv) == 1


@respx.mock
def test_cli_approvals_rejected_api_error_is_neither(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    respx.get(f"{BASE}/repos/o/n/actions/runs/9/approvals").mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )
    argv = ["approvals", "rejected", "--repo", "o/n", "--run-id", "9", "--env", "prod"]
    assert cli.main(argv) == 2


def test_cli_approvals_rejected_unexpected_error_is_neither(monkeypatch, capsys):
    """M2: an unexpected exception is exit 2 (unknown), never 1 (not rejected)."""
    from ayasee import approvals

    monkeypatch.setenv("GITHUB_TOKEN", "t")

    def boom(*_a, **_k):
        raise KeyError("surprise")

    monkeypatch.setattr(approvals, "rejected", boom)
    monkeypatch.setattr(approvals.GitHub, "run_approvals", lambda *a, **k: [])
    argv = ["approvals", "rejected", "--repo", "o/n", "--run-id", "9", "--env", "prod"]
    assert cli.main(argv) == 2
    err = capsys.readouterr().err
    assert "unexpected KeyError" in err and "surprise" not in err


def _job(name, steps):
    return {"name": name, "steps": [{"conclusion": c} for c in steps]}


def _rejected_cli(monkeypatch, approvals_json, jobs_json, status=200):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    respx.get(f"{BASE}/repos/o/n/actions/runs/9/approvals").mock(
        return_value=httpx.Response(200, json=approvals_json)
    )
    jobs = respx.get(f"{BASE}/repos/o/n/actions/runs/9/jobs").mock(
        return_value=httpx.Response(status, json={"jobs": jobs_json})
    )
    argv = [
        "approvals",
        "rejected",
        "--repo",
        "o/n",
        "--run-id",
        "9",
        "--env",
        "prod",
        "--job",
        "prod",
    ]
    return cli.main(argv), jobs


@respx.mock
def test_rejected_now_the_job_ran_no_step(monkeypatch):
    rc, jobs = _rejected_cli(monkeypatch, [_a("x", state="rejected")], [_job("staged / prod", [])])
    assert rc == 0
    assert jobs.calls[0].request.url.params["filter"] == "latest"


@respx.mock
def test_rejected_with_only_skipped_steps_counts(monkeypatch):
    rc, _ = _rejected_cli(
        monkeypatch, [_a("x", state="rejected")], [_job("staged / prod", ["skipped", "skipped"])]
    )
    assert rc == 0


@respx.mock
def test_rejected_earlier_attempt_then_failed_apply_now_is_not_a_rejection(monkeypatch):
    rc, _ = _rejected_cli(
        monkeypatch,
        [_a("x", state="rejected"), _a("y")],
        [_job("staged / prod", ["success", "success", "failure"])],
    )
    assert rc == 1


@respx.mock
def test_apply_failure_without_any_rejection(monkeypatch):
    rc, _ = _rejected_cli(monkeypatch, [_a("y")], [_job("staged / prod", ["success", "failure"])])
    assert rc == 1


@respx.mock
def test_missing_job_in_the_latest_attempt_is_an_error(monkeypatch):
    rc, _ = _rejected_cli(monkeypatch, [_a("x", state="rejected")], [_job("staged / dev", [])])
    assert rc == 2
