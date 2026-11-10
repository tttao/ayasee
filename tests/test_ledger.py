import json
import subprocess
import sys
import types

import httpx
import pytest
import respx

from ayasee import cli
from ayasee.ledger import (
    AzureBlobStore,
    FileStore,
    GitBranchStore,
    LedgerError,
    UnitResult,
    open_store,
    record_from_run,
)

REPO = "acme/infra"


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture(params=["file", "git", "git-remote"])
def store(request, tmp_path):
    if request.param == "file":
        return FileStore(tmp_path / "root")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    if request.param == "git":
        return GitBranchStore(repo)
    bare = tmp_path / "remote.git"
    _git("init", "-q", "--bare", str(bare), cwd=tmp_path)
    _git("remote", "add", "origin", str(bare), cwd=repo)
    return GitBranchStore(repo, remote="origin")


def _rec(sha, n, units=None, env="prod", **kw):
    return record_from_run(
        repo=REPO,
        env=env,
        sha=sha,
        run_id=n,
        run_attempt=1,
        units=units or {"a": UnitResult("applied", True)},
        now=f"2026-11-06T12:00:0{n}.000001Z",
        **kw,
    )


def test_current_none_when_absent(store):
    assert store.current(REPO, "prod") is None
    assert store.history(REPO, "prod") == []


def test_append_then_current(store):
    rec = _rec("a" * 40, 1, approver="alice")
    store.append(rec)
    assert store.current(REPO, "prod") == rec
    assert store.current(REPO, "dev") is None


def test_history_newest_first_and_limit(store):
    recs = [_rec(f"{i}" * 40, i) for i in (1, 2, 3)]
    for r in recs:
        store.append(r)
    assert store.history(REPO, "prod") == list(reversed(recs))
    assert store.history(REPO, "prod", limit=2) == [recs[2], recs[1]]
    assert store.current(REPO, "prod") == recs[2]


def test_empty_plan_records_in_sync(store):
    rec = _rec("b" * 40, 1, units={"a": UnitResult("no_changes", True)})
    assert rec.converged is True
    store.append(rec)
    assert store.current(REPO, "prod").sha == "b" * 40


def test_failed_unit_not_converged(store):
    rec = _rec(
        "c" * 40,
        1,
        units={"a": UnitResult("applied", True), "b": UnitResult("failed", False)},
    )
    assert rec.converged is False
    store.append(rec)
    assert store.current(REPO, "prod").converged is False


def test_file_layout(tmp_path):
    s = FileStore(tmp_path)
    s.append(_rec("d" * 40, 1))
    base = tmp_path / "ayasee-ledger" / "acme" / "infra" / "prod"
    assert (base / "current.json").is_file()
    names = [p.name for p in (base / "history").iterdir()]
    assert names == ["20261106T120001000001Z-dddddddddddd.json"]


def test_bad_names_rejected(tmp_path):
    s = FileStore(tmp_path)
    for repo, env in [("../x/y", "prod"), ("acme/infra", ".."), ("noslash", "prod")]:
        with pytest.raises(LedgerError):
            s.current(repo, env)


def test_git_does_not_touch_worktree_or_index(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    (repo / "f.txt").write_text("x")
    _git("add", "f.txt", cwd=repo)
    before = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
    ).stdout
    GitBranchStore(repo).append(_rec("e" * 40, 1))
    after = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert before == after == "A  f.txt\n"
    assert not (repo / "ayasee-ledger").exists()
    log = subprocess.run(
        ["git", "log", "-1", "--format=%an <%ae>|%s", "ayasee-ledger"],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert log == "ayasee <ayasee@users.noreply.github.com>|ledger: acme/infra prod eeeeeeeeeeee"


def test_git_remote_push_and_retry_on_race(tmp_path):
    bare = tmp_path / "remote.git"
    _git("init", "-q", "--bare", str(bare), cwd=tmp_path)
    stores = []
    for name in ("one", "two"):
        d = tmp_path / name
        d.mkdir()
        _git("init", "-q", cwd=d)
        _git("remote", "add", "origin", str(bare), cwd=d)
        stores.append(GitBranchStore(d, remote="origin"))
    one, two = stores
    one.append(_rec("1" * 40, 1))
    # `two` has never fetched; its append must fetch, rebase onto one's commit, and push.
    two.append(_rec("2" * 40, 2))
    assert [r.sha[0] for r in one.history(REPO, "prod")] == ["2", "1"]
    assert one.current(REPO, "prod").sha == "2" * 40


def test_git_non_fast_forward_is_retried(tmp_path, monkeypatch):
    bare = tmp_path / "remote.git"
    _git("init", "-q", "--bare", str(bare), cwd=tmp_path)
    a = tmp_path / "a"
    a.mkdir()
    _git("init", "-q", cwd=a)
    _git("remote", "add", "origin", str(bare), cwd=a)
    store_a = GitBranchStore(a, remote="origin")
    store_a.append(_rec("1" * 40, 1))
    other = tmp_path / "other"
    other.mkdir()
    _git("init", "-q", cwd=other)
    _git("remote", "add", "origin", str(bare), cwd=other)
    store_other = GitBranchStore(other, remote="origin")

    real_fetch = GitBranchStore._fetch
    calls = {"n": 0}

    def racing_fetch(self):
        real_fetch(self)
        calls["n"] += 1
        if calls["n"] == 1:  # a competitor lands a commit right after our first fetch
            store_other.append(_rec("3" * 40, 3))

    monkeypatch.setattr(GitBranchStore, "_fetch", racing_fetch)
    store_a.append(_rec("2" * 40, 2))
    assert calls["n"] >= 2
    monkeypatch.setattr(GitBranchStore, "_fetch", real_fetch)
    hist = store_a.history(REPO, "prod")
    assert [r.sha[0] for r in hist] == ["3", "2", "1"]  # ordered by recorded_at, all kept
    assert store_a.current(REPO, "prod") == hist[0]


def test_git_not_a_repo(tmp_path):
    with pytest.raises(LedgerError):
        GitBranchStore(tmp_path / "nope").current(REPO, "prod")


# ---- Azure (SDK mocked) ----------------------------------------------------------------


class _NotFound(Exception):
    pass


class FakeContainer:
    def __init__(self):
        self.blobs: dict[str, bytes] = {}
        self.tags: dict[str, dict] = {}

    def upload_blob(self, name, data, overwrite=False, tags=None, **kw):
        self.blobs[name] = data if isinstance(data, bytes) else data.encode()
        self.tags[name] = tags

    def download_blob(self, name):
        if name not in self.blobs:
            raise _NotFound(name)
        return types.SimpleNamespace(readall=lambda: self.blobs[name])

    def list_blobs(self, name_starts_with=""):
        return [
            types.SimpleNamespace(name=n)
            for n in sorted(self.blobs)
            if n.startswith(name_starts_with)
        ]


@pytest.fixture
def azure(monkeypatch):
    container = FakeContainer()
    seen = {}

    class BlobServiceClient:
        def __init__(self, account_url, credential=None):
            seen["account_url"] = account_url
            seen["credential"] = credential

        def get_container_client(self, name):
            seen["container"] = name
            return container

    blob = types.ModuleType("azure.storage.blob")
    blob.BlobServiceClient = BlobServiceClient
    ident = types.ModuleType("azure.identity")
    ident.DefaultAzureCredential = lambda: "cred"
    exc = types.ModuleType("azure.core.exceptions")
    exc.ResourceNotFoundError = _NotFound
    for name, mod in {
        "azure": types.ModuleType("azure"),
        "azure.storage": types.ModuleType("azure.storage"),
        "azure.storage.blob": blob,
        "azure.identity": ident,
        "azure.core": types.ModuleType("azure.core"),
        "azure.core.exceptions": exc,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return container, seen


def test_azure_blob_layout(azure):
    container, seen = azure
    s = AzureBlobStore("https://acct.blob.core.windows.net", "ledger")
    r1, r2 = _rec("a" * 40, 1), _rec("b" * 40, 2)
    s.append(r1)
    s.append(r2)
    assert seen == {
        "account_url": "https://acct.blob.core.windows.net",
        "credential": "cred",
        "container": "ledger",
    }
    base = "ayasee-ledger/acme/infra/prod"
    assert sorted(container.blobs) == [
        f"{base}/current.json",
        f"{base}/history/20261106T120001000001Z-aaaaaaaaaaaa.json",
        f"{base}/history/20261106T120002000001Z-bbbbbbbbbbbb.json",
    ]
    assert container.tags[f"{base}/current.json"] == {"ayasee-sha": "b" * 40}
    assert container.tags[f"{base}/history/20261106T120001000001Z-aaaaaaaaaaaa.json"] == {
        "ayasee-sha": "a" * 40
    }
    assert s.current(REPO, "prod") == r2
    assert s.history(REPO, "prod") == [r2, r1]
    assert s.current(REPO, "dev") is None


def test_azure_missing_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "azure.storage.blob", None)
    with pytest.raises(LedgerError, match=r"ayasee\[azure\]"):
        AzureBlobStore("https://a.blob.core.windows.net", "c").current(REPO, "prod")


AZ_SHOW = ["ledger", "show", "--store", "azure-blob://acct.blob.core.windows.net/c"]


def test_cli_show_azure_gets_past_the_import_guard(azure, capsys):
    """C1: with the extra installed (SDK modules present), `ledger show` reaches the store."""
    _container, seen = azure
    assert cli.main([*AZ_SHOW, "--repo", REPO, "--env", "prod"]) == 0
    out = capsys.readouterr()
    assert "ayasee[azure]" not in out.err
    assert json.loads(out.out) is None
    assert seen["account_url"] == "https://acct.blob.core.windows.net"
    assert seen["container"] == "c"


def test_cli_show_azure_without_extra_names_the_extra(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "azure.storage.blob", None)
    assert cli.main([*AZ_SHOW, "--repo", REPO, "--env", "prod"]) == 1
    assert "ayasee[azure]" in capsys.readouterr().err


# ---- open_store ------------------------------------------------------------------------


def test_open_store(tmp_path, azure):
    assert isinstance(open_store(f"file://{tmp_path}"), FileStore)
    g = open_store(f"git-branch://{tmp_path}?remote=origin")
    assert isinstance(g, GitBranchStore)
    assert (g.branch, g.remote) == ("ayasee-ledger", "origin")
    g = open_store(f"git-branch://{tmp_path}?branch=x")
    assert (g.branch, g.remote) == ("x", None)
    a = open_store("azure-blob://acct.blob.core.windows.net/cont?prefix=pp")
    assert isinstance(a, AzureBlobStore)
    assert (a.account_url, a.container, a.prefix) == (
        "https://acct.blob.core.windows.net",
        "cont",
        "pp",
    )
    for bad in [
        "http://x",
        "file://relative/path",
        "azure-blob://a.blob.core.windows.net/",
        "nonsense",
    ]:
        with pytest.raises(LedgerError):
            open_store(bad)


# ---- CLI -------------------------------------------------------------------------------


@pytest.fixture
def units_file(tmp_path):
    p = tmp_path / "units.json"
    p.write_text(json.dumps({"a": {"status": "applied", "converged": True}}))
    return p


def _write_args(tmp_path, units_file, *extra, env="prod"):
    return [
        "ledger",
        "write",
        "--store",
        f"file://{tmp_path / 'st'}",
        "--repo",
        REPO,
        "--env",
        env,
        "--sha",
        "f" * 40,
        "--run-id",
        "9",
        "--run-attempt",
        "2",
        "--units-json",
        str(units_file),
        *extra,
    ]


def test_cli_write_then_show(tmp_path, units_file, capsys):
    import ayasee.ledger  # noqa: F401

    assert cli.main(_write_args(tmp_path, units_file, "--approver", "bob")) == 0
    capsys.readouterr()
    store = f"file://{tmp_path / 'st'}"
    assert cli.main(["ledger", "show", "--store", store, "--repo", REPO, "--env", "prod"]) == 0
    cur = json.loads(capsys.readouterr().out)
    assert cur["sha"] == "f" * 40 and cur["approver"] == "bob" and cur["converged"] is True
    assert cur["units"] == {"a": {"status": "applied", "converged": True}}
    assert (
        cli.main(
            ["ledger", "show", "--store", store, "--repo", REPO, "--env", "prod", "--history", "5"]
        )
        == 0
    )
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert cli.main(["ledger", "show", "--store", store, "--repo", REPO, "--env", "dev"]) == 0
    assert json.loads(capsys.readouterr().out) is None


@pytest.mark.parametrize(
    "units",
    [
        {"a": {"status": "bogus", "converged": True}},
        {"a": {"status": "applied", "converged": "yes"}},
        {"a": {"status": "applied"}},
        ["a"],
    ],
)
def test_cli_invalid_units_exit_1(tmp_path, capsys, units):
    p = tmp_path / "u.json"
    p.write_text(json.dumps(units))
    assert cli.main(_write_args(tmp_path, p)) == 1
    err = capsys.readouterr().err
    assert err.startswith("ayasee: ledger failed") and "Traceback" not in err


def test_cli_bad_store_exit_1(tmp_path, units_file, capsys):
    args = _write_args(tmp_path, units_file)
    args[3] = "ftp://nope"
    assert cli.main(args) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_mirror_refuses_off_main(tmp_path, units_file, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_REF", "refs/heads/feature")
    assert cli.main(_write_args(tmp_path, units_file, "--mirror-deployments")) == 1
    assert "main" in capsys.readouterr().err
    assert not (tmp_path / "st").exists()  # refused before anything was written


@respx.mock
@pytest.mark.parametrize("converged,state", [(True, "success"), (False, "failure")])
def test_mirror_posts_deployment(tmp_path, monkeypatch, converged, state):
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", "https://api.example")
    p = tmp_path / "u.json"
    p.write_text(json.dumps({"a": {"status": "applied", "converged": converged}}))
    dep = respx.post("https://api.example/repos/acme/infra/deployments").mock(
        return_value=httpx.Response(201, json={"id": 5})
    )
    st = respx.post("https://api.example/repos/acme/infra/deployments/5/statuses").mock(
        return_value=httpx.Response(201, json={})
    )
    assert cli.main(_write_args(tmp_path, p, "--mirror-deployments")) == 0
    body = json.loads(dep.calls[0].request.content)
    assert body["environment"] == "prod" and body["production_environment"] is True
    assert body["ref"] == "f" * 40 and body["payload"]["branch"] == "main"
    assert json.loads(st.calls[0].request.content)["state"] == state


@respx.mock
def test_mirror_non_prod_and_allow_ref(tmp_path, units_file, monkeypatch):
    monkeypatch.delenv("GITHUB_REF", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    dep = respx.post("https://api.github.com/repos/acme/infra/deployments").mock(
        return_value=httpx.Response(201, json={"id": 6})
    )
    respx.post("https://api.github.com/repos/acme/infra/deployments/6/statuses").mock(
        return_value=httpx.Response(201, json={})
    )
    args = _write_args(tmp_path, units_file, "--mirror-deployments", "--allow-ref", env="dev")
    assert cli.main(args) == 0
    assert json.loads(dep.calls[0].request.content)["production_environment"] is False


@respx.mock
def test_mirror_failure_exit_1(tmp_path, units_file, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_TOKEN", "SECRET")
    respx.post("https://api.github.com/repos/acme/infra/deployments").mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )
    assert cli.main(_write_args(tmp_path, units_file, "--mirror-deployments")) == 1
    err = capsys.readouterr().err
    assert "boom" in err and "SECRET" not in err


# ---- R19: monotonic current, heal on read, idempotent retries -------------------------


def test_current_monotonic_older_append(store):
    newer, older = _rec("2" * 40, 2), _rec("1" * 40, 1)
    store.append(newer)
    store.append(older)
    assert store.current(REPO, "prod") == newer
    assert store.history(REPO, "prod") == [newer, older]


def test_current_failure_heals_on_read_and_retry_succeeds(tmp_path, monkeypatch):
    s = FileStore(tmp_path)
    old, new = _rec("1" * 40, 1), _rec("2" * 40, 2)
    s.append(old)
    real = FileStore._write

    def flaky(self, path, data):
        if path.name == "current.json":
            raise OSError("disk full")
        real(self, path, data)

    monkeypatch.setattr(FileStore, "_write", flaky)
    with pytest.raises(OSError):
        s.append(new)
    monkeypatch.setattr(FileStore, "_write", real)
    assert s.current(REPO, "prod") == new  # history[0] is newer than current.json
    s.append(new)  # retry is idempotent
    assert s.current(REPO, "prod") == new
    assert s.history(REPO, "prod") == [new, old]


def test_azure_current_failure_heals_and_retry_idempotent(azure):
    container, _ = azure
    s = AzureBlobStore("https://acct.blob.core.windows.net", "ledger")
    old, new = _rec("1" * 40, 1), _rec("2" * 40, 2)
    s.append(old)
    real = container.upload_blob

    def flaky(name, *a, **kw):
        if name.endswith("current.json"):
            raise RuntimeError("boom")
        return real(name, *a, **kw)

    container.upload_blob = flaky
    with pytest.raises(RuntimeError):
        s.append(new)
    container.upload_blob = real
    assert s.current(REPO, "prod") == new
    s.append(new)  # same history blob already present with identical content: no failure
    assert s.current(REPO, "prod") == new
    assert s.history(REPO, "prod") == [new, old]


def test_azure_monotonic(azure):
    s = AzureBlobStore("https://acct.blob.core.windows.net", "ledger")
    newer, older = _rec("2" * 40, 2), _rec("1" * 40, 1)
    s.append(newer)
    s.append(older)
    assert s.current(REPO, "prod") == newer
    assert s.history(REPO, "prod") == [newer, older]


def test_azure_conflicting_history_rejected(azure):
    s = AzureBlobStore("https://acct.blob.core.windows.net", "ledger")
    s.append(_rec("1" * 40, 1))
    other = _rec("1" * 40, 1, approver="mallory")
    with pytest.raises(LedgerError):
        s.append(other)


# ---- git hardening ---------------------------------------------------------------------


def test_git_credentials_redacted_in_errors(tmp_path):
    bare = tmp_path / "remote.git"
    _git("init", "-q", "--bare", str(bare), cwd=tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("remote", "add", "origin", str(bare), cwd=repo)
    _git("config", "remote.origin.pushurl", "https://user:TOKEN@127.0.0.1:1/x.git", cwd=repo)
    with pytest.raises(LedgerError) as ei:
        GitBranchStore(repo, remote="origin").append(_rec("1" * 40, 1))
    assert "TOKEN" not in str(ei.value)
    # and for the fetch path
    _git("remote", "set-url", "origin", "https://user:TOKEN@127.0.0.1:1/x.git", cwd=repo)
    with pytest.raises(LedgerError) as ei:
        GitBranchStore(repo, remote="origin").current(REPO, "prod")
    assert "TOKEN" not in str(ei.value)


def test_redact_before_truncating():
    from ayasee.ledger import _redact

    text = "x" * 190 + " https://user:SECRETTOKEN@host/x"
    assert "SECRETTOKEN" not in _redact(text)[-200:]


def test_git_refuses_checked_out_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "ayasee-ledger", cwd=repo)
    with pytest.raises(LedgerError, match="checked out"):
        GitBranchStore(repo).append(_rec("1" * 40, 1))
    with pytest.raises(LedgerError, match="checked out"):
        GitBranchStore(repo).current(REPO, "prod")


def test_git_update_ref_non_race_error_not_retried(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    s = GitBranchStore(repo, branch="bad..name")
    with pytest.raises(LedgerError):
        s.append(_rec("1" * 40, 1))


# ---- fail closed on empty units, flags, mirror -------------------------------------------


def test_record_from_run_empty_units_raises():
    with pytest.raises(ValueError):
        record_from_run(repo=REPO, env="prod", sha="a" * 40, run_id=1, run_attempt=1, units={})


def test_cli_empty_units_exit_1(tmp_path, capsys):
    p = tmp_path / "u.json"
    p.write_text("{}")
    assert cli.main(_write_args(tmp_path, p)) == 1
    assert "empty" in capsys.readouterr().err
    assert not (tmp_path / "st").exists()


def test_cli_history_must_be_positive(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(
            [
                "ledger",
                "show",
                "--store",
                f"file://{tmp_path}",
                "--repo",
                REPO,
                "--env",
                "prod",
                "--history",
                "0",
            ]
        )


def test_mirror_missing_token_reports_ledger_written(tmp_path, units_file, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert cli.main(_write_args(tmp_path, units_file, "--mirror-deployments")) == 1
    assert "ledger written, but deployment mirror failed" in capsys.readouterr().err


def test_git_cas_race_retried_under_foreign_locale(tmp_path, monkeypatch):
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    ours, theirs = GitBranchStore(repo), GitBranchStore(repo)
    ours.append(_rec("1" * 40, 1))
    real_commit = GitBranchStore._commit
    state = {"ours": 0, "racing": False}

    def racing_commit(self, base, rec):
        new = real_commit(self, base, rec)
        if self is ours:
            state["ours"] += 1
            if state["ours"] == 1:  # a competitor moves the ref after we computed our commit
                theirs.append(_rec("3" * 40, 3))
        return new

    monkeypatch.setattr(GitBranchStore, "_commit", racing_commit)
    ours.append(_rec("2" * 40, 2))
    assert state["ours"] == 2  # the CAS mismatch was recognised and retried
    assert [r.sha[0] for r in ours.history(REPO, "prod")] == ["3", "2", "1"]


# ---- github-branch (read-only) store ---------------------------------------------------

GH_BASE = "https://ghe.example/api/v3"


def _contents(path, rec):
    import base64

    body = base64.b64encode(json.dumps(rec.to_dict()).encode()).decode()
    respx.get(f"{GH_BASE}/repos/led/store/contents/{path}").mock(
        return_value=httpx.Response(200, json={"encoding": "base64", "content": body})
    )


def _ghstore():
    from ayasee.github import GitHub

    return open_store("github-branch://led/store?branch=ayasee-ledger", gh=GitHub(GH_BASE, "tok"))


def _rec_at(at, sha="a" * 40):
    return record_from_run(
        repo=REPO,
        env="dev",
        sha=sha,
        run_id=1,
        run_attempt=1,
        units={"u": UnitResult("applied", True)},
        now=at,
    )


@respx.mock
def test_github_branch_store_reads_current_and_history():
    from ayasee.ledger import GitHubBranchStore

    store = _ghstore()
    assert isinstance(store, GitHubBranchStore)
    old, new = (
        _rec_at("2026-11-06T10:00:00.000001Z"),
        _rec_at("2026-11-06T11:00:00.000001Z", "b" * 40),
    )
    base = "ayasee-ledger/acme/infra/dev"
    names = ["20261106T100000000001Z-aaaaaaaaaaaa.json", "20261106T110000000001Z-bbbbbbbbbbbb.json"]
    respx.get(f"{GH_BASE}/repos/led/store/contents/{base}/history").mock(
        return_value=httpx.Response(200, json=[{"name": n} for n in names])
    )
    _contents(f"{base}/current.json", new)
    _contents(f"{base}/history/{names[0]}", old)
    _contents(f"{base}/history/{names[1]}", new)
    cur = respx.calls  # noqa: F841
    assert store.current(REPO, "dev") == new
    assert [r.sha for r in store.history(REPO, "dev")] == ["b" * 40, "a" * 40]
    assert all(c.request.url.params["ref"] == "ayasee-ledger" for c in respx.calls)


@respx.mock
def test_github_branch_store_current_prefers_newer_history_entry():
    store = _ghstore()
    stale, fresh = (
        _rec_at("2026-11-06T10:00:00.000001Z"),
        _rec_at("2026-11-06T11:00:00.000001Z", "b" * 40),
    )
    base = "ayasee-ledger/acme/infra/dev"
    name = "20261106T110000000001Z-bbbbbbbbbbbb.json"
    respx.get(f"{GH_BASE}/repos/led/store/contents/{base}/history").mock(
        return_value=httpx.Response(200, json=[{"name": name}])
    )
    _contents(f"{base}/current.json", stale)
    _contents(f"{base}/history/{name}", fresh)
    assert store.current(REPO, "dev") == fresh


@respx.mock
def test_github_branch_store_missing_is_none_and_empty():
    store = _ghstore()
    respx.get(host="ghe.example").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    assert store.current(REPO, "dev") is None
    assert store.history(REPO, "dev") == []


def test_github_branch_store_is_read_only():
    with pytest.raises(LedgerError, match="read-only"):
        _ghstore().append(_rec_at("2026-11-06T10:00:00.000001Z"))


@respx.mock
def test_github_branch_store_wraps_api_errors():
    store = _ghstore()
    respx.get(host="ghe.example").mock(return_value=httpx.Response(500, json={}))
    with pytest.raises(LedgerError):
        store.current(REPO, "dev")


@pytest.mark.parametrize(
    "url", ["github-branch://owner", "github-branch://o/r/extra", "github-branch:///r"]
)
def test_github_branch_url_validated(url):
    from ayasee.github import GitHub

    with pytest.raises(LedgerError):
        open_store(url, gh=GitHub(GH_BASE, "tok"))


def test_from_dict_wrong_shape_is_ledger_error():
    from ayasee.ledger import LedgerRecord

    for bad in ([], "x", None, {"repo": 1}):
        with pytest.raises(LedgerError):
            LedgerRecord.from_dict(bad)


def test_record_from_dict_wrong_shape_is_ledger_error():
    from ayasee.ledger import LedgerRecord

    for bad in ([1, 2], "x", None, {"repo": 1}):
        with pytest.raises(LedgerError):
            LedgerRecord.from_dict(bad)
