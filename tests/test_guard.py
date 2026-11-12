import httpx
import pytest
import respx

from ayasee import cli
from ayasee.github import MAX_PAGES, GitHub, GitHubError
from ayasee.guard import GitHubIssueQuery, GuardResult, check
from ayasee.ledger import FileStore, UnitResult, record_from_run

REPO = "acme/infra"
ORDER = ["dev", "uat", "prod-plan", "prod"]
A = "a" * 40
B = "b" * 40
BASE = "https://ghe.example/api/v3"


class Issues:
    def __init__(self, open_envs=()):
        self.open_envs = set(open_envs)
        self.calls = []

    def open_stop_the_line(self, repo, env):
        self.calls.append((repo, env))
        return [7] if env in self.open_envs else []


def _put(store, env, sha, converged=True, n=1):
    store.append(
        record_from_run(
            repo=REPO,
            env=env,
            sha=sha,
            run_id=n,
            run_attempt=1,
            units={"u": UnitResult("applied" if converged else "failed", converged)},
            now=f"2026-11-06T12:00:0{n}.000001Z",
        )
    )


@pytest.fixture
def store(tmp_path):
    return FileStore(tmp_path / "ledger")


def test_dev_always_ok_without_issues(store):
    issues = Issues(open_envs=ORDER)
    assert check(REPO, "dev", A, ORDER, store, issues) == GuardResult(True, ())
    assert issues.calls == []


def test_prod_superseded(store):
    _put(store, "uat", B)
    assert check(REPO, "prod", A, ORDER, store, Issues()).reasons == ("superseded",)


def test_prod_ok_same_sha(store):
    _put(store, "uat", A)
    assert check(REPO, "prod", A, ORDER, store, Issues()) == GuardResult(True, ())


def test_uat_red_blocks_prod(store):
    _put(store, "uat", A, converged=False)
    assert check(REPO, "prod", A, ORDER, store, Issues()).reasons == ("predecessor_not_converged",)


def test_stop_the_line_blocks_prod_not_dev(store):
    _put(store, "uat", A)
    issues = Issues(open_envs=["dev"])
    assert check(REPO, "prod", A, ORDER, store, issues).reasons == ("stop_the_line",)
    assert check(REPO, "dev", A, ORDER, store, issues).ok
    assert check(REPO, "uat", A, ORDER, store, Issues()).reasons == ("predecessor_missing",)


def test_prod_plan_uses_uat_as_predecessor(store):
    _put(store, "uat", A)
    assert check(REPO, "prod-plan", A, ORDER, store, Issues()).ok
    _put(store, "uat", B, n=2)
    assert check(REPO, "prod-plan", A, ORDER, store, Issues()).reasons == ("superseded",)


def test_predecessor_missing(store):
    assert check(REPO, "prod", A, ORDER, store, Issues()).reasons == ("predecessor_missing",)


def test_different_sha_not_converged_is_missing(store):
    _put(store, "uat", B, converged=False)
    assert check(REPO, "prod", A, ORDER, store, Issues()).reasons == ("predecessor_missing",)


def test_reasons_combine_in_stable_order(store):
    _put(store, "uat", B)
    res = check(REPO, "prod", A, ORDER, store, Issues(open_envs=["prod"]))
    assert not res.ok
    assert res.reasons == ("superseded", "stop_the_line")


def test_stop_checks_every_env_skipping_no_record_stages(store):
    _put(store, "uat", A)
    issues = Issues()
    check(REPO, "prod", A, ORDER, store, issues)
    assert issues.calls == [(REPO, "dev"), (REPO, "uat"), (REPO, "prod")]


def test_unknown_stage_raises(store):
    with pytest.raises(ValueError):
        check(REPO, "qa", A, ORDER, store, Issues())


# ---- CLI ------------------------------------------------------------------------------


def _args(tmp_path, stage, sha=A, *extra):
    return [
        "guard", "--store", f"file://{tmp_path / 'ledger'}", "--repo", REPO,
        "--stage", stage, "--sha", sha, *extra,
    ]  # fmt: skip


def test_cli_dev_ok_needs_no_token(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert cli.main(_args(tmp_path, "dev")) == 0
    assert capsys.readouterr().err == ""


def test_cli_rejected_prints_and_appends_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    _put(FileStore(tmp_path / "ledger"), "uat", B)
    summary = tmp_path / "sum.md"
    summary.write_text("existing\n")
    with respx.mock:
        respx.get(f"{BASE}/repos/{REPO}/issues").mock(return_value=httpx.Response(200, json=[]))
        rc = cli.main(_args(tmp_path, "prod", A, "--summary", str(summary)))
    assert rc == 1
    assert capsys.readouterr().err.strip() == f"guard: rejected prod@{A[:12]}: superseded"
    text = summary.read_text()
    assert text.startswith("existing\n")
    assert "`superseded`" in text


def test_cli_ok_prod(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    _put(FileStore(tmp_path / "ledger"), "uat", A)
    with respx.mock:
        respx.get(f"{BASE}/repos/{REPO}/issues").mock(return_value=httpx.Response(200, json=[]))
        assert cli.main(_args(tmp_path, "prod")) == 0


def test_cli_stop_the_line_combined(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    with respx.mock:
        respx.get(f"{BASE}/repos/{REPO}/issues").mock(
            return_value=httpx.Response(200, json=[{"number": 3}])
        )
        rc = cli.main(_args(tmp_path, "prod-plan"))
    assert rc == 1
    assert capsys.readouterr().err.strip().endswith(": predecessor_missing,stop_the_line")


def test_cli_missing_token_fails_closed(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert cli.main(_args(tmp_path, "prod")) == 1
    err = capsys.readouterr().err
    assert err.startswith("guard: error: ") and "GITHUB_TOKEN" in err
    assert "Traceback" not in err


def test_cli_github_error_fails_closed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    _put(FileStore(tmp_path / "ledger"), "uat", A)
    with respx.mock:
        respx.get(f"{BASE}/repos/{REPO}/issues").mock(
            return_value=httpx.Response(500, json={"message": "boom"})
        )
        assert cli.main(_args(tmp_path, "prod")) == 1
    assert capsys.readouterr().err.startswith("guard: error: ")


def test_cli_bad_store_and_unknown_stage_fail_closed(tmp_path, capsys):
    argv = ["guard", "--store", "bogus://x", "--repo", REPO, "--stage", "dev", "--sha", A]
    assert cli.main(argv) == 1
    assert capsys.readouterr().err.startswith("guard: error: ")
    assert cli.main(_args(tmp_path, "qa")) == 1
    assert "guard: error: " in capsys.readouterr().err


def test_cli_corrupt_ledger_fails_closed(tmp_path, capsys):
    root = tmp_path / "ledger"
    _put(FileStore(root), "dev", A)
    for f in root.rglob("*.json"):
        f.write_text("{not json")
    assert cli.main(_args(tmp_path, "uat")) == 1
    err = capsys.readouterr().err
    assert err.startswith("guard: error: ") and "Traceback" not in err


# ---- GitHub list_issues / GitHubIssueQuery --------------------------------------------


@respx.mock
def test_list_issues_paginates_and_excludes_prs():
    url = f"{BASE}/repos/o/n/issues"
    page2 = f"{url}?page=2"
    first = respx.get(url, params={"labels": "x,y", "state": "open", "per_page": "100"}).mock(
        return_value=httpx.Response(
            200,
            json=[{"number": 1}, {"number": 2, "pull_request": {}}],
            headers={"Link": f'<{page2}>; rel="next", <{page2}>; rel="last"'},
        )
    )
    second = respx.get(page2).mock(return_value=httpx.Response(200, json=[{"number": 3}]))
    got = GitHub(BASE, "t").list_issues("o/n", ["x", "y"])
    assert [i["number"] for i in got] == [1, 3]
    assert first.call_count == 1 and second.call_count == 1


@respx.mock
def test_issue_query_uses_labels_and_returns_numbers():
    route = respx.get(f"{BASE}/repos/o/n/issues").mock(
        return_value=httpx.Response(200, json=[{"number": 5}, {"number": 9, "pull_request": {}}])
    )
    assert GitHubIssueQuery(GitHub(BASE, "t")).open_stop_the_line("o/n", "prod") == [5]
    assert route.calls[0].request.url.params["labels"] == "ayasee:stop-the-line,env:prod"


@respx.mock
def test_list_issues_error_has_status():
    respx.get(f"{BASE}/repos/o/n/issues").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    with pytest.raises(GitHubError) as ei:
        GitHub(BASE, "t").list_issues("o/n", ["x"])
    assert ei.value.status == 404


# ---- fix round 1 ----------------------------------------------------------------------


@pytest.mark.parametrize("order", [["prod"], ["prod-plan", "prod"]])
def test_prod_without_recordable_predecessor_is_rejected(store, order):
    res = check(REPO, "prod", A, order, store, Issues())
    assert res.reasons == ("predecessor_missing",)
    assert check(REPO, "prod-plan", A, ["prod-plan", "prod"], store, Issues()).reasons == (
        "predecessor_missing",
    )


@pytest.mark.parametrize("order", [[], [""], ["dev", "dev", "uat"], ["dev", " dev"], ["dev", ""]])
def test_bad_order_raises(store, order):
    with pytest.raises(ValueError):
        check(REPO, "dev", A, order, store, Issues())


def test_order_entries_are_stripped(store):
    _put(store, "uat", A)
    assert check(REPO, "prod", A, ["dev", " uat", "prod-plan ", "prod"], store, Issues()).ok


def test_cli_order_prod_rejected(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    with respx.mock:
        respx.get(f"{BASE}/repos/{REPO}/issues").mock(return_value=httpx.Response(200, json=[]))
        assert cli.main(_args(tmp_path, "prod", A, "--order", "prod")) == 1
    assert capsys.readouterr().err.strip().endswith(": predecessor_missing")


def test_cli_order_whitespace_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    _put(FileStore(tmp_path / "ledger"), "uat", A)
    with respx.mock:
        respx.get(f"{BASE}/repos/{REPO}/issues").mock(return_value=httpx.Response(200, json=[]))
        argv = _args(tmp_path, "prod", A, "--order", "dev, uat,prod-plan,prod")
        assert cli.main(argv) == 0


@pytest.mark.parametrize("order", ["dev,dev,uat", "", "dev,,uat"])
def test_cli_bad_order_exits_1(tmp_path, capsys, order):
    assert cli.main(_args(tmp_path, "dev", A, "--order", order)) == 1
    assert capsys.readouterr().err.startswith("guard: error: ")


@respx.mock
def test_issue_query_non_int_number_is_error():
    respx.get(f"{BASE}/repos/o/n/issues").mock(
        return_value=httpx.Response(200, json=[{"number": "5"}])
    )
    with pytest.raises(GitHubError):
        GitHubIssueQuery(GitHub(BASE, "t")).open_stop_the_line("o/n", "prod")


@respx.mock
def test_list_issues_refuses_foreign_next_link():
    respx.get(f"{BASE}/repos/o/n/issues").mock(
        return_value=httpx.Response(
            200, json=[], headers={"Link": '<https://evil.example/x>; rel="next"'}
        )
    )
    evil = respx.get("https://evil.example/x").mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(GitHubError):
        GitHub(BASE, "t").list_issues("o/n", ["x"])
    assert evil.call_count == 0


def _chain(n, last_next):
    url = f"{BASE}/repos/o/n/issues"
    nxt = httpx.Response(200, json=[], headers={"Link": f'<{url}?p=2>; rel="next"'})
    end = httpx.Response(200, json=[{"number": 1}], headers=None)
    return [nxt] * (n - 1) + [nxt if last_next else end]


@respx.mock
def test_list_issues_page_cap():
    route = respx.get(f"{BASE}/repos/o/n/issues").mock(side_effect=_chain(MAX_PAGES + 5, True))
    with pytest.raises(GitHubError):
        GitHub(BASE, "t").list_issues("o/n", ["x"])
    assert route.call_count == MAX_PAGES


@respx.mock
def test_list_issues_exactly_cap_pages_ok():
    respx.get(f"{BASE}/repos/o/n/issues").mock(side_effect=_chain(MAX_PAGES, False))
    assert len(GitHub(BASE, "t").list_issues("o/n", ["x"])) == 1


# ---- two-stage layout [<pre-prod>, prod] (issue #1) ----

TWO = ["staging", "prod"]


def test_two_stages_pre_prod_needs_no_predecessor(store):
    issues = Issues(open_envs=TWO)
    assert check(REPO, "staging", A, TWO, store, issues) == GuardResult(True, ())
    assert issues.calls == []


def test_two_stages_prod_chains_from_the_stage_before_prod(store):
    assert check(REPO, "prod", A, TWO, store, Issues()).reasons == ("predecessor_missing",)
    _put(store, "staging", B)
    assert check(REPO, "prod", A, TWO, store, Issues()).reasons == ("superseded",)
    _put(store, "staging", A, converged=False, n=2)
    assert check(REPO, "prod", A, TWO, store, Issues()).reasons == ("predecessor_not_converged",)
    _put(store, "staging", A, n=3)
    assert check(REPO, "prod", A, TWO, store, Issues()) == GuardResult(True, ())


def test_two_stages_stop_the_line_covers_pre_prod_and_prod(store):
    _put(store, "staging", A)
    issues = Issues(open_envs={"staging"})
    assert check(REPO, "prod", A, TWO, store, issues).reasons == ("stop_the_line",)
    assert issues.calls[0] == (REPO, "staging")
    prod_only = Issues(open_envs={"prod"})
    assert check(REPO, "prod", A, TWO, store, prod_only).reasons == ("stop_the_line",)
    assert [env for _, env in prod_only.calls] == ["staging", "prod"]
