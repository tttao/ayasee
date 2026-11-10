import json

import httpx
import pytest
import respx

from ayasee.github import GitHub, GitHubError

BASE = "https://ghe.example/api/v3"


@respx.mock
def test_create_deployment_body_and_id():
    route = respx.post(f"{BASE}/repos/o/n/deployments").mock(
        return_value=httpx.Response(201, json={"id": 77})
    )
    gh = GitHub(BASE, "tok")
    assert gh.create_deployment("o/n", "abc123", "abc123", "prod", True) == 77
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer tok"
    assert json.loads(req.content) == {
        "ref": "abc123",
        "environment": "prod",
        "auto_merge": False,
        "required_contexts": [],
        "production_environment": True,
        "payload": {"sha": "abc123", "branch": "main"},
    }


@respx.mock
def test_create_deployment_status():
    route = respx.post(f"{BASE}/repos/o/n/deployments/77/statuses").mock(
        return_value=httpx.Response(201, json={})
    )
    GitHub(BASE, "tok").create_deployment_status("o/n", 77, "success", "https://x/run/1")
    assert json.loads(route.calls[0].request.content) == {
        "state": "success",
        "log_url": "https://x/run/1",
    }


@respx.mock
def test_error_has_status_and_never_token():
    respx.post(f"{BASE}/repos/o/n/deployments").mock(
        return_value=httpx.Response(403, json={"message": "Resource not accessible"})
    )
    with pytest.raises(GitHubError) as ei:
        GitHub(BASE, "SECRET-TOKEN").create_deployment("o/n", "a", "a", "dev", False)
    assert ei.value.status == 403
    assert "Resource not accessible" in str(ei.value)
    assert "SECRET-TOKEN" not in str(ei.value)


@respx.mock
def test_no_deployment_created_is_error():
    respx.post(f"{BASE}/repos/o/n/deployments").mock(
        return_value=httpx.Response(202, json={"message": "Auto-merged"})
    )
    with pytest.raises(GitHubError):
        GitHub(BASE, "t").create_deployment("o/n", "a", "a", "dev", False)


def test_from_env(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(GitHubError):
        GitHub.from_env()
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    assert GitHub.from_env()._client.base_url.host == "ghe.example"


def test_non_https_base_url_refused():
    with pytest.raises(GitHubError):
        GitHub("http://api.example", "t")
    GitHub("http://localhost:8080", "t").close()
    GitHub("http://127.0.0.1:8080", "t").close()
    GitHub("https://api.example", "t").close()


# ---- sandbox driver support ----


def _gh():
    return GitHub(BASE, "tok")


@respx.mock
def test_get_ref_sha_and_missing_branch():
    respx.get(f"{BASE}/repos/o/n/git/ref/heads/main").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "abc"}})
    )
    respx.get(f"{BASE}/repos/o/n/git/ref/heads/nope").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    assert _gh().get_ref_sha("o/n", "main") == "abc"
    assert _gh().get_ref_sha("o/n", "nope") is None


@respx.mock
def test_get_ref_sha_other_errors_raise():
    respx.get(f"{BASE}/repos/o/n/git/ref/heads/main").mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )
    with pytest.raises(GitHubError) as ei:
        _gh().get_ref_sha("o/n", "main")
    assert ei.value.status == 500


@respx.mock
def test_commit_tree_sha():
    respx.get(f"{BASE}/repos/o/n/git/commits/c1").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": "t1"}})
    )
    assert _gh().commit_tree_sha("o/n", "c1") == "t1"


@respx.mock
def test_create_tree_sends_content_and_deletions():
    route = respx.post(f"{BASE}/repos/o/n/git/trees").mock(
        return_value=httpx.Response(201, json={"sha": "t2"})
    )
    assert _gh().create_tree("o/n", "t1", {"a.txt": "hi", "gone.txt": None}) == "t2"
    body = json.loads(route.calls[0].request.content)
    assert body["base_tree"] == "t1"
    assert {"path": "a.txt", "mode": "100644", "type": "blob", "content": "hi"} in body["tree"]
    assert {"path": "gone.txt", "mode": "100644", "type": "blob", "sha": None} in body["tree"]


@respx.mock
def test_create_commit_and_refs():
    commit = respx.post(f"{BASE}/repos/o/n/git/commits").mock(
        return_value=httpx.Response(201, json={"sha": "c2"})
    )
    create = respx.post(f"{BASE}/repos/o/n/git/refs").mock(
        return_value=httpx.Response(201, json={})
    )
    update = respx.patch(f"{BASE}/repos/o/n/git/refs/heads/main").mock(
        return_value=httpx.Response(200, json={})
    )
    gh = _gh()
    assert gh.create_commit("o/n", "msg", "t2", ["c1"]) == "c2"
    assert json.loads(commit.calls[0].request.content) == {
        "message": "msg",
        "tree": "t2",
        "parents": ["c1"],
    }
    gh.create_branch("o/n", "feat", "c2")
    assert json.loads(create.calls[0].request.content) == {"ref": "refs/heads/feat", "sha": "c2"}
    gh.update_branch("o/n", "main", "c2")
    assert json.loads(update.calls[0].request.content) == {"sha": "c2", "force": False}


@respx.mock
def test_pull_request_create_find_merge():
    respx.post(f"{BASE}/repos/o/n/pulls").mock(return_value=httpx.Response(201, json={"number": 5}))
    find = respx.get(f"{BASE}/repos/o/n/pulls").mock(
        return_value=httpx.Response(200, json=[{"number": 5}])
    )
    merge = respx.put(f"{BASE}/repos/o/n/pulls/5/merge").mock(
        return_value=httpx.Response(200, json={"sha": "m1", "merged": True})
    )
    gh = _gh()
    assert gh.create_pull_request("o/n", "feat", "main", "t") == 5
    assert gh.find_pull_request("o/n", "feat") == 5
    assert find.calls[0].request.url.params["head"] == "o:feat"
    assert gh.merge_pull_request("o/n", 5) == "m1"
    assert json.loads(merge.calls[0].request.content) == {"merge_method": "squash"}


@respx.mock
def test_find_pull_request_none():
    respx.get(f"{BASE}/repos/o/n/pulls").mock(return_value=httpx.Response(200, json=[]))
    assert _gh().find_pull_request("o/n", "feat") is None


@respx.mock
def test_merge_without_sha_is_an_error():
    respx.put(f"{BASE}/repos/o/n/pulls/5/merge").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(GitHubError):
        _gh().merge_pull_request("o/n", 5)


@respx.mock
def test_get_file_decodes_and_handles_missing():
    import base64

    respx.get(f"{BASE}/repos/o/n/contents/a/b.json").mock(
        return_value=httpx.Response(
            200, json={"encoding": "base64", "content": base64.b64encode(b"{}").decode()}
        )
    )
    respx.get(f"{BASE}/repos/o/n/contents/nope").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    gh = _gh()
    assert gh.get_file("o/n", "a/b.json", "br") == "{}"
    assert gh.get_file("o/n", "nope", "br") is None


@respx.mock
def test_list_dir():
    respx.get(f"{BASE}/repos/o/n/contents/d").mock(
        return_value=httpx.Response(200, json=[{"name": "x.json"}, {"name": "y.json"}])
    )
    respx.get(f"{BASE}/repos/o/n/contents/none").mock(return_value=httpx.Response(404, json={}))
    assert _gh().list_dir("o/n", "d", "br") == ["x.json", "y.json"]
    assert _gh().list_dir("o/n", "none", "br") == []


@respx.mock
def test_runs_jobs_and_log():
    runs = respx.get(f"{BASE}/repos/o/n/actions/workflows/main.yml/runs").mock(
        return_value=httpx.Response(200, json={"workflow_runs": [{"id": 1}]})
    )
    respx.get(f"{BASE}/repos/o/n/actions/runs/1").mock(
        return_value=httpx.Response(200, json={"id": 1, "status": "completed"})
    )
    respx.get(f"{BASE}/repos/o/n/actions/runs/1/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [{"name": "staged / dev"}]})
    )
    respx.get(f"{BASE}/repos/o/n/actions/jobs/9/logs").mock(
        return_value=httpx.Response(302, headers={"location": "https://logs.example/x"})
    )
    respx.get("https://logs.example/x").mock(return_value=httpx.Response(200, text="line\n"))
    gh = _gh()
    assert gh.list_workflow_runs("o/n", "main.yml", head_sha="abc") == [{"id": 1}]
    assert runs.calls[0].request.url.params["head_sha"] == "abc"
    assert gh.get_run("o/n", 1)["status"] == "completed"
    assert gh.list_run_jobs("o/n", 1) == [{"name": "staged / dev"}]
    assert gh.job_log("o/n", 9) == "line\n"


@respx.mock
def test_dispatch_and_reviews():
    disp = respx.post(f"{BASE}/repos/o/n/actions/workflows/nightly.yml/dispatches").mock(
        return_value=httpx.Response(204)
    )
    respx.get(f"{BASE}/repos/o/n/actions/runs/1/pending_deployments").mock(
        return_value=httpx.Response(200, json=[{"environment": {"id": 3, "name": "prod"}}])
    )
    rev = respx.post(f"{BASE}/repos/o/n/actions/runs/1/pending_deployments").mock(
        return_value=httpx.Response(200, json=[])
    )
    gh = _gh()
    gh.dispatch_workflow("o/n", "nightly.yml", "main", {"a": "b"})
    assert json.loads(disp.calls[0].request.content) == {"ref": "main", "inputs": {"a": "b"}}
    assert gh.pending_deployments("o/n", 1)[0]["environment"]["name"] == "prod"
    gh.review_pending_deployments("o/n", 1, [3], False, "no")
    assert json.loads(rev.calls[0].request.content) == {
        "environment_ids": [3],
        "state": "rejected",
        "comment": "no",
    }


@respx.mock
def test_close_pull_request():
    route = respx.patch(f"{BASE}/repos/o/n/pulls/5").mock(return_value=httpx.Response(200, json={}))
    _gh().close_pull_request("o/n", 5)
    assert json.loads(route.calls[0].request.content) == {"state": "closed"}


@respx.mock
def test_current_login():
    respx.get(f"{BASE}/user").mock(return_value=httpx.Response(200, json={"login": "alice"}))
    assert _gh().current_login() == "alice"


@respx.mock
def test_current_login_for_an_app_token_is_forbidden():
    respx.get(f"{BASE}/user").mock(return_value=httpx.Response(403, json={"message": "no"}))
    with pytest.raises(GitHubError) as ei:
        _gh().current_login()
    assert ei.value.status == 403


@respx.mock
def test_list_run_jobs_latest_filter():
    route = respx.get(f"{BASE}/repos/o/n/actions/runs/1/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": []})
    )
    _gh().list_run_jobs("o/n", 1)
    _gh().list_run_jobs("o/n", 1, latest=True)
    assert "filter" not in route.calls[0].request.url.params
    assert route.calls[1].request.url.params["filter"] == "latest"


@respx.mock
def test_main_sha():
    respx.get(f"{BASE}/repos/o/n/commits/main").mock(
        return_value=httpx.Response(200, json={"sha": "abc", "commit": {"message": "x"}})
    )
    assert GitHub(BASE, "tok").main_sha("o/n") == "abc"


@respx.mock
def test_compare_returns_ahead_and_html_url():
    respx.get(f"{BASE}/repos/o/n/compare/aaa...bbb").mock(
        return_value=httpx.Response(200, json={"ahead_by": 3, "html_url": "https://x/c"})
    )
    assert GitHub(BASE, "tok").compare("o/n", "aaa", "bbb") == (3, "https://x/c")


@respx.mock
def test_compare_rejects_unexpected_body():
    respx.get(f"{BASE}/repos/o/n/compare/a...b").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(GitHubError):
        GitHub(BASE, "tok").compare("o/n", "a", "b")


@respx.mock
def test_waiting_runs_paginates_wrapped_list():
    route = respx.get(f"{BASE}/repos/o/n/actions/runs")
    route.side_effect = [
        httpx.Response(
            200,
            json={"workflow_runs": [{"id": 1}]},
            headers={"link": f'<{BASE}/repos/o/n/actions/runs?page=2>; rel="next"'},
        ),
        httpx.Response(200, json={"workflow_runs": [{"id": 2}]}),
    ]
    runs = GitHub(BASE, "tok").waiting_runs("o/n")
    assert [r["id"] for r in runs] == [1, 2]
    assert route.calls[0].request.url.params["status"] == "waiting"


@respx.mock
def test_pending_deployments_lists_environments():
    respx.get(f"{BASE}/repos/o/n/actions/runs/5/pending_deployments").mock(
        return_value=httpx.Response(200, json=[{"environment": {"name": "prod"}}])
    )
    assert GitHub(BASE, "tok").pending_deployments("o/n", 5)[0]["environment"]["name"] == "prod"


@respx.mock
def test_code_scanning_counts_per_severity():
    def alerts(request):
        n = {"critical": 1, "high": 3}[request.url.params["severity"]]
        return httpx.Response(200, json=[{"number": i} for i in range(n)])

    respx.get(f"{BASE}/repos/o/n/code-scanning/alerts").mock(side_effect=alerts)
    assert GitHub(BASE, "tok").code_scanning_counts("o/n") == {"critical": 1, "high": 3}


@respx.mock
@pytest.mark.parametrize("status", [403, 404])
def test_code_scanning_counts_none_when_unavailable(status):
    respx.get(f"{BASE}/repos/o/n/code-scanning/alerts").mock(
        return_value=httpx.Response(status, json={"message": "nope"})
    )
    assert GitHub(BASE, "tok").code_scanning_counts("o/n") is None


@respx.mock
def test_code_scanning_counts_raises_on_other_errors():
    respx.get(f"{BASE}/repos/o/n/code-scanning/alerts").mock(return_value=httpx.Response(500))
    with pytest.raises(GitHubError):
        GitHub(BASE, "tok").code_scanning_counts("o/n")


@respx.mock
@pytest.mark.parametrize("content", ["!!!", "//79AA=="])  # not base64; bytes ff fe 00 (not utf-8)
def test_get_file_wraps_decode_errors(content):
    respx.get(f"{BASE}/repos/o/n/contents/f").mock(
        return_value=httpx.Response(200, json={"encoding": "base64", "content": content})
    )
    with pytest.raises(GitHubError):
        GitHub(BASE, "tok").get_file("o/n", "f", "main")


@respx.mock
def test_waiting_runs_workflow_filter():
    respx.get(f"{BASE}/repos/o/n/actions/runs").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_runs": [
                    {"id": 1, "path": "a.yml@refs/heads/main"},
                    {"id": 2, "path": "b.yml"},
                ]
            },
        )
    )
    assert [r["id"] for r in GitHub(BASE, "tok").waiting_runs("o/n", ["a.yml"])] == [1]
    assert len(GitHub(BASE, "tok").waiting_runs("o/n")) == 2


@respx.mock
@pytest.mark.parametrize("content", ["!!!", "//4="])  # bad base64; valid base64 of non-UTF-8
def test_get_file_bad_bytes_raise_github_error(content):
    respx.get(f"{BASE}/repos/o/n/contents/f").mock(
        return_value=httpx.Response(200, json={"encoding": "base64", "content": content})
    )
    with pytest.raises(GitHubError):
        GitHub(BASE, "tok").get_file("o/n", "f", "main")


@respx.mock
def test_waiting_runs_filters_by_workflow_path():
    respx.get(f"{BASE}/repos/o/n/actions/runs").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_runs": [
                    {"id": 1, "path": ".github/workflows/a.yml"},
                    {"id": 2, "path": ".github/workflows/b.yml@refs/heads/main"},
                ]
            },
        )
    )
    runs = GitHub(BASE, "tok").waiting_runs("o/n", [".github/workflows/b.yml"])
    assert [r["id"] for r in runs] == [2]
