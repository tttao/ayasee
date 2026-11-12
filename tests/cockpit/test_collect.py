import base64
import json
from pathlib import Path

import httpx
import jsonschema
import pytest
import respx

from ayasee import cli
from ayasee.cockpit.collect import (
    RepoEntry,
    ReposFileError,
    collect,
    load_repos,
)
from ayasee.github import GitHub
from ayasee.ledger import FileStore, UnitResult, record_from_run

SCHEMA = json.loads((Path(__file__).parents[2] / "schema" / "snapshot.v1.json").read_text())
BASE = "https://ghes.example/api/v3"
REPO = "acme/infra"
MAIN, DEV, UAT, PROD = "a" * 40, "b" * 40, "c" * 40, "d" * 40


def _rec(env, sha, status="applied", converged=True, at="2026-11-06T10:00:00.000001Z"):
    return record_from_run(
        repo=REPO,
        env=env,
        sha=sha,
        run_id=1,
        run_attempt=1,
        units={"u": UnitResult(status, converged)},
        now=at,
    )


def _ledger(tmp_path, recs):
    store = FileStore(tmp_path / "ledger")
    for r in recs:
        store.append(r)
    return f"file://{tmp_path / 'ledger'}"


def _entry(url, stages=None):
    return RepoEntry(REPO, url, stages or ["dev", "uat", "prod"])


def _compare(base, head, ahead):
    return respx.get(f"{BASE}/repos/{REPO}/compare/{base}...{head}").mock(
        return_value=httpx.Response(
            200,
            json={
                "ahead_by": ahead,
                "html_url": f"https://ghes.example/{REPO}/compare/{base}...{head}",
            },
        )
    )


def _mock_basics(*, scanning=None, waiting=(), issues=None, config=None):
    """Everything but compare. ``issues``: {(label, env): [issue, ...]}. ``config``: the text of
    .ayasee.yaml at main (None: no such file), or an int HTTP status."""
    respx.get(f"{BASE}/repos/{REPO}/commits/main").mock(
        return_value=httpx.Response(200, json={"sha": MAIN})
    )
    if isinstance(config, int):
        cfg = httpx.Response(config, json={"message": "nope"})
    elif config is None:
        cfg = httpx.Response(404, json={"message": "Not Found"})
    else:
        cfg = httpx.Response(
            200,
            json={"encoding": "base64", "content": base64.b64encode(config.encode()).decode()},
        )
    respx.get(f"{BASE}/repos/{REPO}/contents/.ayasee.yaml", params={"ref": MAIN}).mock(
        return_value=cfg
    )
    respx.get(f"{BASE}/repos/{REPO}/actions/runs").mock(
        return_value=httpx.Response(200, json={"workflow_runs": [r for r, _ in waiting]})
    )
    for run, envs in waiting:
        respx.get(f"{BASE}/repos/{REPO}/actions/runs/{run['id']}/pending_deployments").mock(
            return_value=httpx.Response(
                200, json=[{"environment": {"name": e, "id": 1}} for e in envs]
            )
        )

    def issues_route(request):
        labels = request.url.params["labels"].split(",")
        found = (issues or {}).get((labels[0], labels[1].removeprefix("env:")), [])
        return httpx.Response(200, json=found)

    respx.get(f"{BASE}/repos/{REPO}/issues").mock(side_effect=issues_route)

    def alerts(request):
        if scanning is None:
            return httpx.Response(404, json={"message": "no analysis found"})
        n = scanning[request.url.params["severity"]]
        return httpx.Response(
            200,
            json=[
                {"number": i, "rule": {"name": "SECRET-RULE", "description": "SECRET-DESC"}}
                for i in range(n)
            ],
        )

    respx.get(f"{BASE}/repos/{REPO}/code-scanning/alerts").mock(side_effect=alerts)


def _gh():
    return GitHub(BASE, "tok-secret")


@respx.mock
def test_snapshot_validates_against_schema(tmp_path):
    url = _ledger(
        tmp_path,
        [_rec("dev", DEV), _rec("uat", UAT, "no_changes"), _rec("prod", PROD, "failed", False)],
    )
    _mock_basics(
        scanning={"critical": 1, "high": 2},
        waiting=[
            ({"id": 9, "created_at": "2026-11-06T09:00:00Z", "html_url": "https://r/9"}, ["prod"])
        ],
        issues={
            ("ayasee:drift", "uat"): [
                {
                    "created_at": "2026-11-05T00:00:00Z",
                    "html_url": "https://i/1",
                    "body": "Drifted resources:\n- `a.b`\n- `c.d`\n\nRun: x\n",
                }
            ],
            ("ayasee:stop-the-line", "prod"): [{"number": 3}],
        },
    )
    _compare(DEV, MAIN, 5)
    _compare(UAT, DEV, 2)
    _compare(PROD, UAT, 0)
    snap = collect([_entry(url)], _gh())
    jsonschema.Draft202012Validator(SCHEMA).validate(snap)
    envs = {e["env"]: e for e in snap["repos"][0]["envs"]}
    assert snap["schema"] == "ayasee.snapshot/v1"
    assert envs["uat"]["drift"] == {
        "open": True,
        "since": "2026-11-05T00:00:00Z",
        "issue_url": "https://i/1",
        "resources": 2,
    }
    assert envs["prod"]["health"] == {"last_result": "failed", "stop_the_line": True}
    assert envs["uat"]["health"]["last_result"] == "no_changes"
    assert envs["dev"]["health"]["last_result"] == "applied"
    assert envs["prod"]["converged"] is False
    assert snap["repos"][0]["findings"] == {
        "critical": 1,
        "high": 2,
        "url": "https://ghes.example/acme/infra/security/code-scanning",
    }


@respx.mock
def test_pending_counts_chain(tmp_path):
    url = _ledger(tmp_path, [_rec("dev", DEV), _rec("uat", UAT), _rec("prod", PROD)])
    _mock_basics()
    _compare(DEV, MAIN, 5)
    _compare(UAT, DEV, 2)
    _compare(PROD, UAT, 1)
    envs = collect([_entry(url)], _gh())["repos"][0]["envs"]
    assert [e["pending_commits"] for e in envs] == [5, 2, 1]
    assert envs[0]["pending_compare_url"] == f"https://ghes.example/{REPO}/compare/{DEV}...{MAIN}"


@respx.mock
def test_pending_chain_with_null_live_sha(tmp_path):
    url = _ledger(tmp_path, [_rec("dev", DEV), _rec("prod", PROD)])  # uat has no record
    _mock_basics()
    _compare(DEV, MAIN, 4)
    snap = collect([_entry(url)], _gh())
    envs = {e["env"]: e for e in snap["repos"][0]["envs"]}
    assert envs["dev"]["pending_commits"] == 4
    assert envs["uat"]["live_sha"] is None and envs["uat"]["pending_commits"] is None
    assert envs["prod"]["pending_commits"] is None and envs["prod"]["pending_compare_url"] is None
    jsonschema.Draft202012Validator(SCHEMA).validate(snap)


@respx.mock
def test_gate_waiting_from_pending_runs(tmp_path):
    url = _ledger(tmp_path, [])
    old = {"id": 1, "created_at": "2026-11-06T08:00:00Z", "html_url": "https://r/1"}
    new = {"id": 2, "created_at": "2026-11-06T09:00:00Z", "html_url": "https://r/2"}
    _mock_basics(waiting=[(new, ["prod"]), (old, ["prod", "uat"])])
    envs = {e["env"]: e for e in collect([_entry(url)], _gh())["repos"][0]["envs"]}
    assert envs["prod"]["gate"] == {
        "waiting": True,
        "since": "2026-11-06T08:00:00Z",
        "run_url": "https://r/1",
    }
    assert envs["uat"]["gate"]["run_url"] == "https://r/1"
    assert envs["dev"]["gate"] == {"waiting": False, "since": None, "run_url": None}


@respx.mock
def test_unknown_when_no_ledger(tmp_path):
    url = _ledger(tmp_path, [])
    _mock_basics()
    dev = collect([_entry(url)], _gh())["repos"][0]["envs"][0]
    assert dev["live_sha"] is None and dev["live_at"] is None and dev["converged"] is None
    assert dev["health"]["last_result"] == "unknown"
    assert dev["pending_commits"] is None


@respx.mock
def test_snapshot_has_no_finding_details(tmp_path):
    url = _ledger(tmp_path, [])
    _mock_basics(scanning={"critical": 2, "high": 1})
    snap = collect([_entry(url)], _gh())
    text = json.dumps(snap)
    assert "SECRET-RULE" not in text and "SECRET-DESC" not in text
    f = snap["repos"][0]["findings"]
    assert set(f) == {"critical", "high", "url"}
    assert f["critical"] == 2 and f["high"] == 1


@respx.mock
def test_code_scanning_unavailable_gives_nulls(tmp_path):
    url = _ledger(tmp_path, [])
    _mock_basics(scanning=None)
    snap = collect([_entry(url)], _gh())
    assert snap["repos"][0]["findings"] == {"critical": None, "high": None, "url": None}
    assert "error" not in snap["repos"][0]
    jsonschema.Draft202012Validator(SCHEMA).validate(snap)


@respx.mock
def test_ghes_base_url_used(tmp_path):
    url = _ledger(tmp_path, [])
    _mock_basics()
    collect([_entry(url)], _gh())
    assert respx.calls
    assert all(str(c.request.url).startswith(BASE + "/") for c in respx.calls)
    assert _gh().web_url == "https://ghes.example"
    assert GitHub("https://api.github.com", "t").web_url == "https://github.com"


@respx.mock
def test_repo_error_does_not_abort_run(tmp_path):
    url = _ledger(tmp_path, [])
    _mock_basics()
    respx.get(f"{BASE}/repos/other/broken/commits/main").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    entries = [RepoEntry("other/broken", url), _entry(url)]
    snap = collect(entries, _gh())
    bad, good = snap["repos"]
    assert bad["error"] == "github: HTTP 404: Not Found"
    assert [e["health"]["last_result"] for e in bad["envs"]] == ["unknown"] * 3
    assert "error" not in good
    assert "tok-secret" not in json.dumps(snap)
    jsonschema.Draft202012Validator(SCHEMA).validate(snap)


@respx.mock
def test_etag_304_reuses_cached_body(tmp_path):
    url = _ledger(tmp_path, [])
    _mock_basics()
    seen = []

    def main(request):
        seen.append(request.headers.get("if-none-match"))
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, json={"sha": MAIN}, headers={"etag": '"v1"'})

    respx.get(f"{BASE}/repos/{REPO}/commits/main").mock(side_effect=main)
    gh = _gh()
    gh.enable_etag_cache()
    snap = collect([_entry(url), _entry(url)], gh)
    assert seen == [None, '"v1"']
    assert [r["main_sha"] for r in snap["repos"]] == [MAIN, MAIN]


def test_cache_is_off_unless_enabled():
    gh = _gh()
    with respx.mock:
        route = respx.get(f"{BASE}/repos/o/n/commits/main").mock(
            return_value=httpx.Response(200, json={"sha": "a"}, headers={"etag": '"x"'})
        )
        gh.main_sha("o/n")
        gh.main_sha("o/n")
    assert all("if-none-match" not in c.request.headers for c in route.calls)


# ---- repos file and CLI ---------------------------------------------------------------


def test_load_repos_defaults_and_override(tmp_path):
    f = tmp_path / "r.yaml"
    f.write_text(
        "- repo: a/b\n  ledger: file:///x\n"
        "- repo: c/d\n  ledger: file:///y\n  stages: [dev, prod]\n"
    )
    a, c = load_repos(f)
    assert a.stages == ["dev", "uat", "prod"] and c.stages == ["dev", "prod"]


@pytest.mark.parametrize(
    "text",
    [
        "[]",
        "a: b",
        "- repo: a/b",
        "- repo: nope\n  ledger: file:///x",
        "- repo: a/b\n  ledger: file:///x\n  extra: 1",
        "- repo: a/b\n  ledger: file:///x\n  stages: [dev, prod-plan]",
        "- repo: a/b\n  ledger: file:///x\n  stages: []",
        "- repo: a/b\n  ledger: file:///x\n- repo: a/b\n  ledger: file:///x",
        "- repo: a/b\n  ledger: file:///x\n  workflows: []",
        "- repo: a/b\n  ledger: file:///x\n  workflows: [1]",
    ],
)
def test_load_repos_rejects(tmp_path, text):
    f = tmp_path / "r.yaml"
    f.write_text(text)
    with pytest.raises(ReposFileError):
        load_repos(f)


def _cli_setup(tmp_path, monkeypatch, repos_text):
    monkeypatch.setenv("GITHUB_TOKEN", "tok-secret")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    f = tmp_path / "r.yaml"
    f.write_text(repos_text)
    return ["cockpit", "collect", "--repos-file", str(f), "--out", str(tmp_path / "snap.json")]


@respx.mock
def test_cli_writes_snapshot(tmp_path, monkeypatch, capsys):
    url = _ledger(tmp_path, [])
    _mock_basics()
    argv = _cli_setup(tmp_path, monkeypatch, f"- repo: {REPO}\n  ledger: {url}\n")
    assert cli.main(argv) == 0
    snap = json.loads((tmp_path / "snap.json").read_text())
    jsonschema.Draft202012Validator(SCHEMA).validate(snap)
    assert "tok-secret" not in capsys.readouterr().err


@respx.mock
def test_cli_exit_1_only_when_all_repos_fail(tmp_path, monkeypatch):
    url = _ledger(tmp_path, [])
    _mock_basics()
    respx.get(f"{BASE}/repos/o/bad/commits/main").mock(return_value=httpx.Response(500, json={}))
    one_bad = f"- repo: o/bad\n  ledger: {url}\n- repo: {REPO}\n  ledger: {url}\n"
    assert cli.main(_cli_setup(tmp_path, monkeypatch, one_bad)) == 0
    all_bad = f"- repo: o/bad\n  ledger: {url}\n"
    assert cli.main(_cli_setup(tmp_path, monkeypatch, all_bad)) == 1


# ---- fix round 1 ----------------------------------------------------------------------


def _b64(raw: bytes) -> str:
    import base64

    return base64.b64encode(raw).decode()


@respx.mock
@pytest.mark.parametrize(
    "blob",
    [
        {"encoding": "base64", "content": "!!!not-base64!!!"},
        {"encoding": "base64", "content": _b64(b"\xff\xfe\x00")},
        {"encoding": "base64", "content": _b64(b"[1, 2]")},
        {"encoding": "base64", "content": _b64(b'{"repo": 1}')},
        {"encoding": "base64", "content": _b64(b"not json")},
    ],
)
def test_garbled_ledger_blob_is_a_per_repo_error(tmp_path, blob):
    good_url = _ledger(tmp_path, [])
    _mock_basics()
    respx.get(host="ghes.example", path__regex=r".*/repos/led/store/contents/.*").mock(
        return_value=httpx.Response(200, json=blob)
    )
    bad = RepoEntry(REPO, "github-branch://led/store?branch=ayasee-ledger")
    snap = collect([bad, _entry(good_url)], _gh())
    assert snap["repos"][0]["error"]
    assert "error" not in snap["repos"][1]
    jsonschema.Draft202012Validator(SCHEMA).validate(snap)


@respx.mock
def test_pending_deployments_403_makes_gate_unknown(tmp_path):
    url = _ledger(tmp_path, [])
    run = {"id": 9, "created_at": "2026-11-06T09:00:00Z", "html_url": "https://r/9"}
    _mock_basics(waiting=[(run, ["prod"])])
    respx.get(f"{BASE}/repos/{REPO}/actions/runs/9/pending_deployments").mock(
        return_value=httpx.Response(403, json={"message": "forbidden"})
    )
    snap = collect([_entry(url)], _gh())
    assert "error" not in snap["repos"][0]
    assert all(e["gate"]["waiting"] is None for e in snap["repos"][0]["envs"])
    jsonschema.Draft202012Validator(SCHEMA).validate(snap)


@respx.mock
@pytest.mark.parametrize("status", [404, 422])
def test_compare_failure_makes_pending_null(tmp_path, status):
    url = _ledger(tmp_path, [_rec("dev", DEV), _rec("uat", UAT)])
    _mock_basics()
    respx.get(f"{BASE}/repos/{REPO}/compare/{DEV}...{MAIN}").mock(
        return_value=httpx.Response(status, json={"message": "No common ancestor"})
    )
    _compare(UAT, DEV, 2)
    snap = collect([_entry(url)], _gh())
    envs = snap["repos"][0]["envs"]
    assert "error" not in snap["repos"][0]
    assert envs[0]["pending_commits"] is None and envs[0]["pending_compare_url"] is None
    assert envs[1]["pending_commits"] == 2


@respx.mock
def test_waiting_runs_filtered_by_workflow_path(tmp_path):
    url = _ledger(tmp_path, [])
    mine = {"id": 1, "created_at": "2026-11-06T08:00:00Z", "html_url": "https://r/1",
            "path": ".github/workflows/main.yml@refs/heads/main"}  # fmt: skip
    other = {"id": 2, "created_at": "2026-11-06T07:00:00Z", "html_url": "https://r/2",
             "path": ".github/workflows/other.yml"}  # fmt: skip
    _mock_basics(waiting=[(mine, ["prod"]), (other, ["prod"])])
    envs = {e["env"]: e for e in collect([_entry(url)], _gh())["repos"][0]["envs"]}
    assert envs["prod"]["gate"]["run_url"] == "https://r/1"
    custom = RepoEntry(REPO, url, workflows=[".github/workflows/other.yml"])
    envs = {e["env"]: e for e in collect([custom], _gh())["repos"][0]["envs"]}
    assert envs["prod"]["gate"]["run_url"] == "https://r/2"


def test_drift_body_format_is_shared():
    from ayasee.drift import drift_bullet

    body = "Drifted resources:\n" + drift_bullet("a.b") + drift_bullet("c.d") + "\nRun: x\n"
    import ayasee.cockpit.collect as c

    issue = {"created_at": "t", "html_url": "u", "body": body}
    with respx.mock:
        respx.get(f"{BASE}/repos/o/n/issues").mock(return_value=httpx.Response(200, json=[issue]))
        assert c._drift(_gh(), "o/n", "dev")["resources"] == 2


def test_cli_out_write_failure_exits_1(tmp_path, monkeypatch, capsys):
    url = _ledger(tmp_path, [])
    with respx.mock:
        _mock_basics()
        argv = _cli_setup(tmp_path, monkeypatch, f"- repo: {REPO}\n  ledger: {url}\n")
        argv[-1] = str(tmp_path / "missing-dir" / "snap.json")
        assert cli.main(argv) == 1
    err = capsys.readouterr().err
    assert "cannot write --out" in err and "tok-secret" not in err


def test_cli_etag_cache_persists_between_runs(tmp_path, monkeypatch):
    url = _ledger(tmp_path, [])
    cache = tmp_path / "etags.json"
    with respx.mock:
        _mock_basics()
        seen = []

        def main(request):
            seen.append(request.headers.get("if-none-match"))
            if request.headers.get("if-none-match") == '"v1"':
                return httpx.Response(304)
            return httpx.Response(200, json={"sha": MAIN}, headers={"etag": '"v1"'})

        respx.get(f"{BASE}/repos/{REPO}/commits/main").mock(side_effect=main)
        argv = _cli_setup(tmp_path, monkeypatch, f"- repo: {REPO}\n  ledger: {url}\n")
        argv += ["--etag-cache", str(cache)]
        assert cli.main(argv) == 0
        assert cache.is_file()
        assert "tok-secret" not in cache.read_text()
        assert "authorization" not in cache.read_text().lower()
        assert cli.main(argv) == 0
    assert seen == [None, '"v1"']
    assert json.loads((tmp_path / "snap.json").read_text())["repos"][0]["main_sha"] == MAIN


def test_etag_cache_size_cap_evicts_oldest(tmp_path, monkeypatch):
    import ayasee.github as g

    monkeypatch.setattr(g, "ETAG_MAX_ENTRIES", 2)
    gh = _gh()
    gh.enable_etag_cache()
    with respx.mock:
        for i in range(3):
            respx.get(f"{BASE}/repos/o/n{i}/commits/main").mock(
                return_value=httpx.Response(200, json={"sha": "a"}, headers={"etag": f'"{i}"'})
            )
            gh.main_sha(f"o/n{i}")
    path = tmp_path / "c.json"
    gh.save_etag_cache(path)
    keys = list(json.loads(path.read_text()))
    assert len(keys) == 2 and keys[0].endswith("/o/n1/commits/main")

    monkeypatch.setattr(g, "ETAG_MAX_ENTRIES", 2000)
    monkeypatch.setattr(g, "ETAG_MAX_BYTES", 20)  # each body is ~11 bytes: only one fits
    gh2 = _gh()
    gh2.enable_etag_cache(path)
    gh2.save_etag_cache(path)
    assert len(json.loads(path.read_text())) == 1


def test_etag_cache_ignores_corrupt_file(tmp_path):
    bad = tmp_path / "c.json"
    bad.write_text("{not json")
    gh = _gh()
    gh.enable_etag_cache(bad)  # starts empty, no error
    bad.write_text(json.dumps({"https://x": {"etag": 1}}))
    _gh().enable_etag_cache(bad)


# ---- persistent ETag cache --------------------------------------------------------------


def _etag_routes(seen):
    def main(request):
        seen.append(request.headers.get("if-none-match"))
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(
            200, json={"sha": MAIN}, headers={"etag": '"v1"', "content-type": "application/json"}
        )

    respx.get(f"{BASE}/repos/{REPO}/commits/main").mock(side_effect=main)


@respx.mock
def test_etag_cache_file_reused_across_runs(tmp_path, monkeypatch):
    url = _ledger(tmp_path, [])
    _mock_basics()
    seen = []
    _etag_routes(seen)
    cache = tmp_path / "etags.json"
    argv = _cli_setup(tmp_path, monkeypatch, f"- repo: {REPO}\n  ledger: {url}\n")
    argv += ["--etag-cache", str(cache)]
    assert cli.main(argv) == 0
    assert cli.main(argv) == 0
    assert seen == [None, '"v1"']
    snap = json.loads((tmp_path / "snap.json").read_text())
    assert snap["repos"][0]["main_sha"] == MAIN
    raw = cache.read_text()
    assert "tok-secret" not in raw and "uthorization" not in raw


@respx.mock
def test_no_etag_flag_means_no_cache_file(tmp_path, monkeypatch):
    url = _ledger(tmp_path, [])
    _mock_basics()
    seen = []
    _etag_routes(seen)
    argv = _cli_setup(tmp_path, monkeypatch, f"- repo: {REPO}\n  ledger: {url}\n")
    assert cli.main(argv) == 0 and cli.main(argv) == 0
    assert seen == [None, None]
    assert not list(tmp_path.glob("*etag*"))


# ---- bad ledger bytes, degraded envs, workflows, --out ----------------------------------


def test_load_repos_workflows_key(tmp_path):
    f = tmp_path / "r.yaml"
    f.write_text("- repo: a/b\n  ledger: file:///x\n  workflows: [.github/workflows/ci.yml]\n")
    assert load_repos(f)[0].workflows == [".github/workflows/ci.yml"]
    f.write_text("- repo: a/b\n  ledger: file:///x\n  workflows: []\n")
    with pytest.raises(ReposFileError):
        load_repos(f)
    f.write_text("- repo: a/b\n  ledger: file:///x\n")
    assert load_repos(f)[0].workflows == [".github/workflows/main.yml"]


def test_estate_repositories_includes_ledger_repos_and_checks_owner(tmp_path):
    from ayasee.cockpit.collect import ReposFileError, estate_repositories, load_repos

    f = tmp_path / "r.yaml"
    f.write_text(
        "- repo: acme/a\n  ledger: github-branch://acme/ledgers?branch=l\n"
        "- repo: acme/b\n  ledger: github-branch://acme/b\n"
    )
    entries = load_repos(f)
    assert estate_repositories(entries, "acme") == ["a", "b", "ledgers"]
    with pytest.raises(ReposFileError, match="shares one owner"):
        estate_repositories(entries, "other")
    f.write_text("- repo: acme/a\n  ledger: github-branch://evil/led\n")
    with pytest.raises(ReposFileError, match="evil/led"):
        estate_repositories(load_repos(f), "acme")


def test_estate_repositories_caps_at_500(tmp_path):
    from ayasee.cockpit.collect import ReposFileError, estate_repositories, load_repos

    f = tmp_path / "r.yaml"
    f.write_text(
        "".join(f"- repo: acme/r{i}\n  ledger: github-branch://acme/r{i}\n" for i in range(501))
    )
    with pytest.raises(ReposFileError, match="at most 500"):
        estate_repositories(load_repos(f), "acme")


@pytest.mark.parametrize(
    "ledger",
    [
        "azure-blob://acct.blob.core.windows.net/ledger",
        "git-branch:///repo",
        "file:///x",
    ],
)
def test_estate_repositories_refuses_ledgers_the_cockpit_cannot_read(tmp_path, ledger):
    """C1: cockpit-collect reads only github-branch:// ledgers (no Azure login in the collector)."""
    from ayasee.cockpit.collect import ReposFileError, estate_repositories, load_repos

    f = tmp_path / "r.yaml"
    f.write_text(f"- repo: acme/a\n  ledger: {ledger}\n")
    with pytest.raises(ReposFileError, match="github-branch://"):
        estate_repositories(load_repos(f), "acme")


@pytest.mark.parametrize(
    ("config", "want"),
    [
        (None, []),  # no configuration file: the compliant defaults
        ("stages: [dev, uat, prod]\n", []),
        (
            "solo_mode: true\nidentity: secrets\n",
            [
                {"kind": "non-compliant", "setting": "solo_mode"},
                {"kind": "deviation", "setting": "identity"},
            ],
        ),
        ("bogus: 1\n", "unknown"),  # invalid: unknown, never a repo error
        (403, "unknown"),  # no contents permission
    ],
)
@respx.mock
def test_labels_come_from_the_configuration_at_main(tmp_path, capsys, config, want):
    _mock_basics(config=config)
    for base, head in ((DEV, MAIN), (UAT, DEV), (PROD, UAT)):
        _compare(base, head, 0)
    url = _ledger(tmp_path, [_rec("dev", DEV), _rec("uat", UAT), _rec("prod", PROD)])
    snap = collect([_entry(url)], _gh())
    repo = snap["repos"][0]
    assert "error" not in repo
    if want == "unknown":
        assert "labels" not in repo
    else:
        assert repo["labels"] == want
    jsonschema.validate(snap, SCHEMA)
    assert "NON-COMPLIANT" not in capsys.readouterr().err  # reading another repo prints nothing


def test_load_repos_config_key(tmp_path):
    f = tmp_path / "repos.yaml"
    f.write_text(
        "- {repo: a/b, ledger: 'file:///l', config: cfg/ayasee.yaml}\n- {repo: a/c, ledger: 'file:///l'}\n"
    )
    a, c = load_repos(f)
    assert (a.config, c.config) == ("cfg/ayasee.yaml", ".ayasee.yaml")
    for bad in ("/abs.yaml", "../x.yaml", "''", "3"):
        f.write_text(f"- {{repo: a/b, ledger: 'file:///l', config: {bad}}}\n")
        with pytest.raises(ReposFileError, match="config"):
            load_repos(f)
