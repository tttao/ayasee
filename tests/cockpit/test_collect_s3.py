"""Issue #10: the cockpit reads s3:// ledgers, read-only, with its own key, against the fake S3 of
tests/test_ledger_s3.py (which checks every signature)."""

import json

import jsonschema
import pytest
import respx
from tests.cockpit.test_collect import (
    BASE,
    DEV,
    MAIN,
    PROD,
    REPO,
    SCHEMA,
    UAT,
    _compare,
    _gh,
    _mock_basics,
    _rec,
)
from tests.test_ledger_s3 import AK, SECRET_SENTINEL, SK, FakeS3, _store

from ayasee import cli
from ayasee.cockpit.collect import (
    ReadOnlyLedger,
    RepoEntry,
    ReposFileError,
    collect,
    estate_repositories,
    load_repos,
)
from ayasee.ledger import FileStore, LedgerError

ENDPOINT_HOST = "s3.example.test"
URL = f"s3://ledger-bkt?region=eu-west-1&endpoint=https://{ENDPOINT_HOST}"


@pytest.fixture
def fake(monkeypatch):
    fake = FakeS3(path_style=True, page_size=2)
    store = _store(fake)
    for rec in (_rec("dev", DEV), _rec("uat", UAT), _rec("prod", PROD, "failed", False)):
        store.append(rec)
    fake.puts.clear()
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", AK)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", SK)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    return fake


def _route(fake):
    respx.route(host=ENDPOINT_HOST).mock(side_effect=fake)


@respx.mock
def test_collect_reads_an_s3_ledger_and_never_writes(fake):
    _route(fake)
    _mock_basics()
    _compare(DEV, MAIN, 5)
    _compare(UAT, DEV, 2)
    _compare(PROD, UAT, 0)
    snap = collect([RepoEntry(REPO, URL)], _gh())
    jsonschema.Draft202012Validator(SCHEMA).validate(snap)  # the snapshot schema is unchanged
    repo = snap["repos"][0]
    assert "error" not in repo
    envs = {e["env"]: e for e in repo["envs"]}
    assert [envs[e]["live_sha"] for e in ("dev", "uat", "prod")] == [DEV, UAT, PROD]
    assert envs["prod"]["converged"] is False
    assert envs["prod"]["health"]["last_result"] == "failed"
    assert fake.puts == []  # read-only: not one PUT
    s3_calls = [c for c in respx.calls if c.request.url.host == ENDPOINT_HOST]
    assert s3_calls and all(c.request.method == "GET" for c in s3_calls)
    # the GitHub token never goes to the S3 endpoint
    assert all("tok-secret" not in str(c.request.headers) for c in s3_calls)


@respx.mock
def test_collect_without_the_cockpit_key_lists_the_repo_with_an_error(fake, monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", SECRET_SENTINEL)
    _route(fake)
    _mock_basics()
    snap = collect([RepoEntry(REPO, URL)], _gh())
    repo = snap["repos"][0]
    assert "AWS_ACCESS_KEY_ID" in repo["error"]
    assert SECRET_SENTINEL not in json.dumps(snap)
    jsonschema.Draft202012Validator(SCHEMA).validate(snap)


@respx.mock
def test_collect_s3_error_names_status_and_code_only(fake):
    fake.fail_status = 403
    _route(fake)
    _mock_basics()
    snap = collect([RepoEntry(REPO, URL)], _gh())
    error = snap["repos"][0]["error"]
    assert "HTTP 403 AccessDenied" in error
    assert SECRET_SENTINEL not in json.dumps(snap)  # the response body never reaches it


@respx.mock
def test_cli_collect_with_an_s3_ledger(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok-secret")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    _route(fake)
    _mock_basics()
    _compare(DEV, MAIN, 1)
    _compare(UAT, DEV, 1)
    _compare(PROD, UAT, 1)
    f = tmp_path / "repos.yaml"
    f.write_text(f"- repo: {REPO}\n  ledger: '{URL}'\n")
    out = tmp_path / "snap.json"
    assert cli.main(["cockpit", "collect", "--repos-file", str(f), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["repos"][0]["envs"][2]["live_sha"] == PROD
    assert fake.puts == []


def test_read_only_ledger_refuses_append(tmp_path):
    store = FileStore(tmp_path / "l")
    store.append(_rec("dev", DEV))
    ro = ReadOnlyLedger(store)
    assert ro.current(REPO, "dev").sha == DEV
    assert [r.sha for r in ro.history(REPO, "dev")] == [DEV]
    with pytest.raises(LedgerError, match="never writes"):
        ro.append(_rec("dev", UAT))
    assert store.current(REPO, "dev").sha == DEV


def test_collect_hands_the_collector_a_read_only_view(tmp_path, monkeypatch):
    """Whatever the store factory returns, _collect_repo only ever gets the read-only view."""
    import ayasee.cockpit.collect as mod

    def spy(entry, gh, store):
        assert isinstance(store, ReadOnlyLedger)
        raise LedgerError("stop here")

    monkeypatch.setattr(mod, "_collect_repo", spy)
    snap = collect(
        [RepoEntry(REPO, "file:///x")], None, store_factory=lambda url, gh=None: FileStore(tmp_path)
    )
    assert snap["repos"][0]["error"] == "stop here"


# ---- repos.yaml ----------------------------------------------------------------------


def _repos(tmp_path, ledger):
    f = tmp_path / "r.yaml"
    f.write_text(
        f"- repo: acme/a\n  ledger: '{ledger}'\n- repo: acme/b\n  ledger: github-branch://acme/b\n"
    )
    return load_repos(f)


@pytest.mark.parametrize(
    "ledger",
    [
        "s3://ledger-bkt?region=eu-west-1",
        "s3://ledger-bkt?region=auto&prefix=p&endpoint=https://acct.r2.cloudflarestorage.com",
    ],
)
def test_estate_accepts_s3_ledgers_and_mints_no_token_for_them(tmp_path, monkeypatch, ledger):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)  # no request is sent
    assert estate_repositories(_repos(tmp_path, ledger), "acme") == ["a", "b"]


@pytest.mark.parametrize(
    ("ledger", "match"),
    [
        ("s3://ledger-bkt", "needs \\?region="),
        ("s3://ledger-bkt?region=", "needs \\?region="),
        ("s3://ledger-bkt?region=eu-west-1&acl=public", "unknown parameter"),
        ("s3://ledger-bkt?region=eu-west-1&endpoint=http://minio:9000", "https"),
        ("s3://Bad_Bucket?region=eu-west-1", "bucket"),
    ],
)
def test_estate_refuses_a_bad_s3_ledger_before_a_token_is_minted(
    tmp_path, monkeypatch, ledger, match
):
    monkeypatch.setenv("AWS_REGION", "eu-west-1")  # the region must be in the URL anyway
    with pytest.raises(ReposFileError, match=match):
        estate_repositories(_repos(tmp_path, ledger), "acme")


def test_cockpit_repos_cli_with_an_s3_ledger(tmp_path, capsys):
    f = tmp_path / "r.yaml"
    f.write_text("- repo: acme/a\n  ledger: 's3://ledger-bkt?region=eu-west-1'\n")
    assert cli.main(["cockpit", "repos", "--repos-file", str(f), "--owner", "acme"]) == 0
    assert capsys.readouterr().out.strip() == "a"
