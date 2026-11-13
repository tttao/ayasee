"""The s3:// ledger store and its SigV4 client, against an in-process fake S3.

The fake checks every request's signature (recomputed from what arrived on the wire), keeps
ETags, and implements If-None-Match / If-Match on PutObject the way S3 documents them
(412 Precondition Failed). Hooks let a test inject a competing write between a read and a write.
"""

import gzip
import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import parse_qsl, unquote

import httpx
import pytest
import respx

from ayasee import cli
from ayasee.cockpit.collect import RepoEntry, ReposFileError, estate_repositories
from ayasee.ledger import LedgerError, S3Store, UnitResult, open_store, record_from_run
from ayasee.s3 import Credentials, S3Client, S3Error, canonical_query, sign

REPO = "acme/infra"
AK, SK = "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
SECRET_SENTINEL = "SENTINEL-secret-do-not-print"
NS = "http://s3.amazonaws.com/doc/2006-03-01/"


def _rec(sha, n, env="prod"):
    return record_from_run(
        repo=REPO,
        env=env,
        sha=sha,
        run_id=n,
        run_attempt=1,
        units={"a": UnitResult("applied", True)},
        now=f"2026-11-06T12:00:0{n}.000001Z",
    )


# ---- SigV4 vectors ---------------------------------------------------------------------
# Case 1 is the "GET Object" example of the AWS SigV4 S3 documentation; all three were also
# cross-checked against botocore 1.44.2 (S3SigV4Auth) with the same inputs.


@pytest.mark.parametrize(
    ("method", "host", "path", "query", "headers", "body", "token", "signed", "signature"),
    [
        (
            "GET",
            "examplebucket.s3.amazonaws.com",
            "/test.txt",
            {},
            {"range": "bytes=0-9"},
            b"",
            None,
            "host;range;x-amz-content-sha256;x-amz-date",
            "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41",
        ),
        (
            "PUT",
            "r2.example.com",
            "/bkt/ayasee-ledger/acme/infra/prod/current.json",
            {},
            {"if-match": '"abc123"', "content-type": "application/json"},
            b'{"x":1}\n',
            "tok/en+==",
            "content-type;host;if-match;x-amz-content-sha256;x-amz-date;x-amz-security-token",
            "00f092bac542364911bfcea8e8dec7e3b35fbbadbd054bf79bb1f483adce7f56",
        ),
        (
            "GET",
            "bkt.s3.eu-west-1.amazonaws.com",
            "/",
            {
                "list-type": "2",
                "prefix": "ayasee-ledger/acme/infra/prod/history/",
                "continuation-token": "1ab/c+d==",
            },
            {},
            b"",
            None,
            "host;x-amz-content-sha256;x-amz-date",
            "d39ab40a31b5f9e5ae996ee2e4fa4a49cf52346a05d41b7143100f2814545efd",
        ),
    ],
)
def test_sigv4_vectors(method, host, path, query, headers, body, token, signed, signature):
    amz = "20130524T000000Z"
    ph = hashlib.sha256(body).hexdigest()
    hdrs = {**headers, "x-amz-date": amz, "x-amz-content-sha256": ph}
    if token:
        hdrs["x-amz-security-token"] = token
    auth = sign(
        method=method,
        host=host,
        path=path,
        query=query,
        headers=hdrs,
        payload_hash=ph,
        creds=Credentials(AK, SK, token),
        region="us-east-1",
        amz_date=amz,
    )
    assert auth == (
        f"AWS4-HMAC-SHA256 Credential={AK}/20130524/us-east-1/s3/aws4_request, "
        f"SignedHeaders={signed}, Signature={signature}"
    )


def test_credentials_repr_hides_secret():
    assert SK not in repr(Credentials(AK, SK, "tok"))


# ---- the fake --------------------------------------------------------------------------


class FakeS3:
    def __init__(self, bucket="ledger-bkt", *, path_style=False, page_size=1000):
        self.bucket = bucket
        self.path_style = path_style
        self.page_size = page_size
        self.objects: dict[str, bytes] = {}
        self.etags: dict[str, str] = {}
        self.puts: list[tuple[str, dict]] = []
        self.conditional = True  # False: a store that silently ignores If-Match/If-None-Match
        self.not_implemented = False  # True: a store that answers 501 to a conditional PUT
        self.before_put = None  # hook(key, headers) run before a PUT is evaluated
        self.fail_status: int | None = None
        self._n = 0

    def _key(self, request: httpx.Request) -> str:
        path = unquote(request.url.raw_path.decode().split("?", 1)[0])
        if self.path_style:
            assert path.startswith(f"/{self.bucket}/") or path == f"/{self.bucket}/"
            return path[len(self.bucket) + 2 :]
        return path[1:]

    def _verify(self, request: httpx.Request) -> bool:
        auth = request.headers["authorization"]
        signed = auth.split("SignedHeaders=")[1].split(",")[0].split(";")
        assert {"host", "x-amz-date", "x-amz-content-sha256"} <= set(signed)
        assert (
            hashlib.sha256(request.content).hexdigest() == request.headers["x-amz-content-sha256"]
        )
        raw_path, _, raw_query = request.url.raw_path.decode().partition("?")
        query = dict(parse_qsl(raw_query, keep_blank_values=True))
        assert canonical_query(query) == raw_query  # what was sent is what was signed
        token = request.headers.get("x-amz-security-token")
        expected = sign(
            method=request.method,
            host=request.headers["host"],
            path=unquote(raw_path),
            query=query,
            headers={n: request.headers[n] for n in signed if n != "host"},
            payload_hash=request.headers["x-amz-content-sha256"],
            creds=Credentials(AK, SK, token),
            region="eu-west-1",
            amz_date=request.headers["x-amz-date"],
        )
        return auth == expected

    @staticmethod
    def _err(status, code):
        body = f"<Error><Code>{code}</Code><Message>{SECRET_SENTINEL}</Message></Error>"
        return httpx.Response(status, content=body.encode())

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if not self._verify(request):
            return self._err(403, "SignatureDoesNotMatch")
        if self.fail_status:
            return self._err(self.fail_status, "AccessDenied")
        key = self._key(request)
        if request.method == "GET" and request.url.params.get("list-type") == "2":
            return self._list(request)
        if request.method == "GET":
            if key not in self.objects:
                return self._err(404, "NoSuchKey")
            return httpx.Response(200, content=self.objects[key], headers={"ETag": self.etags[key]})
        if request.method == "PUT":
            hdrs = {k: v for k, v in request.headers.items() if k.startswith("if-")}
            self.puts.append((key, hdrs))
            if self.before_put:
                self.before_put(key, hdrs)
            if hdrs and self.not_implemented:
                return self._err(501, "NotImplemented")
            if self.conditional:
                if hdrs.get("if-none-match") == "*" and key in self.objects:
                    return self._err(412, "PreconditionFailed")
                if "if-match" in hdrs and self.etags.get(key) != hdrs["if-match"]:
                    return self._err(412, "PreconditionFailed")
            self.store(key, request.content)
            return httpx.Response(200, headers={"ETag": self.etags[key]})
        return self._err(405, "MethodNotAllowed")

    def store(self, key, data):
        self._n += 1
        self.objects[key] = data
        self.etags[key] = f'"{hashlib.md5(data + bytes([self._n])).hexdigest()}"'

    def _list(self, request):
        prefix = request.url.params.get("prefix", "")
        start = int(request.url.params.get("continuation-token", "0"))
        keys = sorted(k for k in self.objects if k.startswith(prefix))
        page = keys[start : start + self.page_size]
        more = start + self.page_size < len(keys)
        xml = f'<ListBucketResult xmlns="{NS}"><IsTruncated>{str(more).lower()}</IsTruncated>'
        xml += "".join(f"<Contents><Key>{k}</Key></Contents>" for k in page)
        if more:
            xml += f"<NextContinuationToken>{start + self.page_size}</NextContinuationToken>"
        return httpx.Response(200, content=(xml + "</ListBucketResult>").encode())


def _store(fake, token=None, prefix="ayasee-ledger"):
    client = S3Client(
        fake.bucket,
        "eu-west-1",
        "https://s3.example.test" if fake.path_style else None,
        creds=lambda: Credentials(AK, SK, token),
        transport=httpx.MockTransport(fake),
        clock=lambda: datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC),
    )
    return S3Store(client, prefix=prefix)


@pytest.fixture(params=["virtual-hosted", "path-style"])
def fake(request):
    return FakeS3(path_style=request.param == "path-style", page_size=2)


BASE = "ayasee-ledger/acme/infra/prod"
H1 = f"{BASE}/history/20261106T120001000001Z-aaaaaaaaaaaa.json"


# ---- store behaviour -------------------------------------------------------------------


def test_empty(fake):
    s = _store(fake)
    assert s.current(REPO, "prod") is None
    assert s.history(REPO, "prod") == []


def test_append_current_history_and_layout(fake):
    s = _store(fake)
    recs = [_rec(c * 40, i) for i, c in ((1, "a"), (2, "b"), (3, "c"))]
    for r in recs:
        s.append(r)
    assert s.current(REPO, "prod") == recs[2]
    assert s.history(REPO, "prod") == list(reversed(recs))  # page_size 2: paginated listing
    assert s.history(REPO, "prod", limit=2) == [recs[2], recs[1]]
    assert s.current(REPO, "dev") is None
    assert H1 in fake.objects and f"{BASE}/current.json" in fake.objects
    assert json.loads(fake.objects[f"{BASE}/current.json"])["sha"] == "c" * 40


def test_writes_are_conditional(fake):
    s = _store(fake)
    s.append(_rec("a" * 40, 1))
    s.append(_rec("b" * 40, 2))
    (k1, h1), (k2, h2), (k3, h3), (k4, h4) = fake.puts
    assert (k1, h1) == (H1, {"if-none-match": "*"})
    assert (k2, h2) == (f"{BASE}/current.json", {"if-none-match": "*"})  # first current
    assert k3.startswith(f"{BASE}/history/") and h3 == {"if-none-match": "*"}
    assert k4 == f"{BASE}/current.json" and set(h4) == {"if-match"}  # replace by ETag only


def test_retry_with_identical_bytes_is_idempotent(fake):
    s = _store(fake)
    rec = _rec("a" * 40, 1)
    s.append(rec)
    s.append(rec)
    assert s.history(REPO, "prod") == [rec]
    assert s.current(REPO, "prod") == rec


def test_history_conflict_with_different_content(fake):
    s = _store(fake)
    s.append(_rec("a" * 40, 1))
    clash = record_from_run(
        repo=REPO,
        env="prod",
        sha="a" * 40,
        run_id=99,
        run_attempt=1,
        units={"a": UnitResult("failed", False)},
        now="2026-11-06T12:00:01.000001Z",
    )
    with pytest.raises(LedgerError, match="different content"):
        s.append(clash)


def test_current_is_monotonic(fake):
    s = _store(fake)
    s.append(_rec("b" * 40, 2))
    s.append(_rec("a" * 40, 1))  # a late retry of an older run
    assert json.loads(fake.objects[f"{BASE}/current.json"])["sha"] == "b" * 40
    assert s.current(REPO, "prod").sha == "b" * 40
    assert len(s.history(REPO, "prod")) == 2


def test_crash_between_writes_is_healed_on_read(fake):
    s = _store(fake)
    s.append(_rec("a" * 40, 1))
    fake.store(f"{BASE}/history/20261106T120002000001Z-bbbbbbbbbbbb.json", _enc(_rec("b" * 40, 2)))
    assert s.current(REPO, "prod").sha == "b" * 40  # R19: newest of current.json and history


def _enc(rec):
    return (json.dumps(rec.to_dict(), indent=2, sort_keys=True) + "\n").encode()


def test_lost_race_on_current_rereads_and_keeps_newest(fake):
    s = _store(fake)
    s.append(_rec("a" * 40, 1))
    newer = _rec("c" * 40, 3)

    def compete(key, hdrs):
        if key.endswith("current.json") and "if-match" in hdrs and fake.before_put:
            fake.before_put = None
            fake.store(key, _enc(newer))  # a newer run wins between our GET and our PUT

    fake.before_put = compete
    s.append(_rec("b" * 40, 2))
    # Our If-Match lost (412); the re-read sees a newer record and leaves it in place.
    assert json.loads(fake.objects[f"{BASE}/current.json"])["sha"] == "c" * 40
    # One PUT for record a, one lost PUT for b; the re-read then writes nothing.
    assert sum(1 for k, _ in fake.puts if k.endswith("current.json")) == 2


def test_lost_race_on_first_current_retries(fake):
    s = _store(fake)
    older = _rec("a" * 40, 1)

    def compete(key, hdrs):
        if key.endswith("current.json") and hdrs.get("if-none-match") == "*":
            fake.before_put = None
            fake.store(key, _enc(older))  # an older record lands first

    fake.before_put = compete
    s.append(_rec("b" * 40, 2))
    assert json.loads(fake.objects[f"{BASE}/current.json"])["sha"] == "b" * 40  # replaced by ETag


def test_endless_race_fails_closed(fake):
    s = _store(fake)
    s.append(_rec("a" * 40, 1))
    n = {"i": 0}

    def compete(key, hdrs):
        if key.endswith("current.json"):
            n["i"] += 1
            fake.store(key, _enc(_rec("a" * 40, 1)))  # same record, new ETag every time

    fake.before_put = compete
    with pytest.raises(LedgerError, match="after 5 attempts"):
        s.append(_rec("b" * 40, 2))
    assert n["i"] == 5


def test_store_without_conditional_writes_fails_closed(fake):
    fake.not_implemented = True
    with pytest.raises(LedgerError, match="HTTP 501 NotImplemented"):
        _store(fake).append(_rec("a" * 40, 1))


def test_error_names_status_and_code_only(fake):
    fake.fail_status = 403
    with pytest.raises(LedgerError) as exc:
        _store(fake).current(REPO, "prod")
    assert "HTTP 403 AccessDenied" in str(exc.value)
    assert SECRET_SENTINEL not in str(exc.value)


def test_session_token_is_sent_and_signed(fake):
    seen = []
    inner = fake.__call__

    def spy(request):
        seen.append(request.headers.get("x-amz-security-token"))
        assert "x-amz-security-token" in request.headers["authorization"]
        return inner(request)

    client = S3Client(
        fake.bucket,
        "eu-west-1",
        "https://s3.example.test" if fake.path_style else None,
        creds=lambda: Credentials(AK, SK, "sess-token"),
        transport=httpx.MockTransport(spy),
    )
    S3Store(client).append(_rec("a" * 40, 1))
    assert seen and set(seen) == {"sess-token"}


def test_custom_prefix(fake):
    s = _store(fake, prefix="team/ledger")
    s.append(_rec("a" * 40, 1))
    assert all(k.startswith("team/ledger/acme/infra/prod/") for k in fake.objects)


def test_bad_names_rejected(fake):
    s = _store(fake)
    for repo, env in [("../x/y", "prod"), (REPO, ".."), ("noslash", "prod")]:
        with pytest.raises(LedgerError):
            s.current(repo, env)


def test_malformed_record_fails_closed(fake):
    fake.store(f"{BASE}/current.json", b"not json")
    with pytest.raises(LedgerError, match="not JSON"):
        _store(fake).current(REPO, "prod")


def test_list_with_dtd_is_refused():
    def handler(request):
        return httpx.Response(200, content=b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]>')

    client = S3Client(
        "bkt",
        "eu-west-1",
        creds=lambda: Credentials(AK, SK),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(S3Error, match="DTD"):
        client.list_keys("p/")


# ---- open_store ------------------------------------------------------------------------


def test_open_store_virtual_hosted(monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)
    s = open_store("s3://my-ledger?region=eu-west-1")
    assert isinstance(s, S3Store)
    assert s.client.base == "https://my-ledger.s3.eu-west-1.amazonaws.com"
    assert s.client.key_root == "/"
    assert s.prefix == "ayasee-ledger"


def test_open_store_endpoint_is_path_style():
    s = open_store(
        "s3://my.ledger?region=auto&prefix=p&endpoint=https://acct.r2.cloudflarestorage.com"
    )
    assert s.client.base == "https://acct.r2.cloudflarestorage.com"
    assert s.client.key_root == "/my.ledger/"
    assert s.prefix == "p"


def test_open_store_region_from_env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    assert open_store("s3://bkt").client.region == "us-east-2"


@pytest.mark.parametrize(
    ("url", "match"),
    [
        ("s3://bkt", "needs \\?region"),
        ("s3:///nobucket?region=eu-west-1", "s3://<bucket>"),
        ("s3://bkt/some/key?region=eu-west-1", "s3://<bucket>"),
        ("s3://bkt?region=eu-west-1&acl=public", "unknown parameter"),
        ("s3://Bad_Bucket?region=eu-west-1", "bucket"),
        ("s3://dotted.bucket?region=eu-west-1", "dots"),
        ("s3://bkt?region=EU WEST", "region"),
        ("s3://bkt?region=eu-west-1&endpoint=http://minio:9000", "https"),
        ("s3://bkt?region=eu-west-1&endpoint=https://h/path", "without a path"),
        ("s3://bkt?region=eu-west-1&endpoint=https://u:p@h", "https"),
        ("s3://bkt?region=eu-west-1&prefix=../x", "prefix"),
    ],
)
def test_open_store_rejects(monkeypatch, url, match):
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    with pytest.raises(LedgerError, match=match):
        open_store(url)


# ---- CLI -------------------------------------------------------------------------------


def _units(tmp_path):
    p = tmp_path / "units.json"
    p.write_text(json.dumps({"a": {"status": "applied", "converged": True}}))
    return str(p)


STORE_URL = "s3://ledger-bkt?region=eu-west-1"


@respx.mock
def test_cli_write_and_show_round_trip(tmp_path, monkeypatch, capsys):
    fake = FakeS3()
    respx.route(host="ledger-bkt.s3.eu-west-1.amazonaws.com").mock(side_effect=fake)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", AK)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", SK)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    sha = "f" * 40
    common = ["--store", STORE_URL, "--repo", REPO, "--env", "dev"]
    assert (
        cli.main(
            ["ledger", "write", *common, "--sha", sha, "--run-id", "7", "--run-attempt", "1"]
            + ["--units-json", _units(tmp_path)]
        )
        == 0
    )
    assert cli.main(["ledger", "show", *common]) == 0
    assert json.loads(capsys.readouterr().out)["sha"] == sha


def test_cli_without_credentials_fails_closed(monkeypatch, capsys):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", SECRET_SENTINEL)
    assert cli.main(["ledger", "show", "--store", STORE_URL, "--repo", REPO, "--env", "dev"]) == 1
    err = capsys.readouterr().err
    assert "AWS_ACCESS_KEY_ID" in err
    assert SECRET_SENTINEL not in err


# ---- cockpit ---------------------------------------------------------------------------


def test_cockpit_collector_refuses_s3_ledgers():
    """The collector has no cloud login; an s3:// ledger is refused before a token is minted."""
    entry = RepoEntry("acme/infra", "s3://ledger-bkt?region=eu-west-1", [], [], None)
    with pytest.raises(ReposFileError, match="github-branch"):
        estate_repositories([entry], "acme")


def test_fake_rejects_a_bad_signature(fake):
    """The fake really checks signatures: a wrong secret is refused (so passing tests mean the
    requests on the wire are signed as sent)."""
    client = S3Client(
        fake.bucket,
        "eu-west-1",
        "https://s3.example.test" if fake.path_style else None,
        creds=lambda: Credentials(AK, "wrong-secret"),
        transport=httpx.MockTransport(fake),
    )
    with pytest.raises(LedgerError, match="SignatureDoesNotMatch"):
        S3Store(client).current(REPO, "prod")


# ---- review follow-ups -----------------------------------------------------------------


def _client(handler, endpoint=None):
    return S3Client(
        "bkt",
        "eu-west-1",
        endpoint,
        creds=lambda: Credentials(AK, SK),
        transport=httpx.MockTransport(handler),
    )


BOMB = (
    '<?xml version="1.0" encoding="ENC"?>'
    '<!DOCTYPE r [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
    f'<ListBucketResult xmlns="{NS}"><Contents><Key>&b;</Key></Contents></ListBucketResult>'
)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-16-le", "utf-16-be"])
def test_list_with_dtd_is_refused_in_any_encoding(encoding):
    """A byte search for "<!DOCTYPE" misses UTF-16; the expat pre-scan does not."""
    doc = BOMB.replace("ENC", "UTF-8" if encoding == "utf-8" else "UTF-16")
    if encoding in ("utf-16-le", "utf-16-be"):  # explicit byte order, with a BOM
        doc = "﻿" + doc
    data = doc.encode(encoding)
    if encoding != "utf-8":
        assert b"<!DOCTYPE" not in data  # the old byte search would have let it through
    with pytest.raises(S3Error, match="DTD"):
        _client(lambda r: httpx.Response(200, content=data)).list_keys("p/")


def test_list_in_utf16_without_dtd_parses():
    doc = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        f'<ListBucketResult xmlns="{NS}"><IsTruncated>false</IsTruncated>'
        "<Contents><Key>p/x.json</Key></Contents></ListBucketResult>"
    )
    client = _client(lambda r: httpx.Response(200, content=doc.encode("utf-16")))
    assert client.list_keys("p/") == ["p/x.json"]


@pytest.mark.parametrize(
    ("endpoint", "base"),
    [
        ("https://MinIO.Example.TEST", "https://minio.example.test"),
        ("https://minio.example.test:443", "https://minio.example.test"),
        ("https://minio.example.test:9000/", "https://minio.example.test:9000"),
        ("https://[::1]:9000", "https://[::1]:9000"),
    ],
)
def test_endpoint_is_normalised_like_the_host_header(endpoint, base):
    seen = []

    def handler(request):
        seen.append(request.headers["host"])
        return httpx.Response(404)

    client = _client(handler, endpoint)
    assert client.base == base
    assert client.get("k") is None
    host = base.removeprefix("https://")
    assert client.host == host
    assert seen == [host]  # the Host header httpx sent is the host that was signed


def test_endpoint_with_bad_port_is_refused():
    with pytest.raises(S3Error, match="port"):
        _client(lambda r: httpx.Response(404), "https://h:99999")


@pytest.mark.parametrize("var", ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"])
def test_non_ascii_credential_names_the_variable_only(monkeypatch, var):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", AK)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", SK)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    monkeypatch.setenv(var, "sécret-" + SECRET_SENTINEL)
    with pytest.raises(S3Error) as exc:
        Credentials.from_env()
    assert str(exc.value) == f"{var} must be printable ASCII"


def test_cli_non_ascii_credential_fails_cleanly(monkeypatch, capsys):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", AK)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "sécret-" + SECRET_SENTINEL)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    assert cli.main(["ledger", "show", "--store", STORE_URL, "--repo", REPO, "--env", "dev"]) == 1
    err = capsys.readouterr().err
    assert "AWS_SECRET_ACCESS_KEY must be printable ASCII" in err
    assert SECRET_SENTINEL not in err and "Traceback" not in err


def test_oversized_object_is_refused():
    big = b"x" * ((1 << 20) + 1)
    client = _client(lambda r: httpx.Response(200, content=big, headers={"ETag": '"e"'}))
    with pytest.raises(S3Error, match="larger than"):
        client.get("k")


def test_oversized_list_page_is_refused():
    big = b"<a>" + b"x" * (16 << 20) + b"</a>"
    with pytest.raises(S3Error, match="larger than"):
        _client(lambda r: httpx.Response(200, content=big)).list_keys("p/")


def test_store_ignoring_conditional_headers_still_reads_newest(fake):
    """A store that silently ignores If-Match: an older writer lands last in current.json, but
    current() still returns the newer record (R19: newest of current.json and history)."""
    fake.conditional = False
    s = _store(fake)
    s.append(_rec("a" * 40, 1))
    older, newer = _rec("b" * 40, 2), _rec("c" * 40, 3)

    def compete(key, hdrs):
        # The older writer has read current (record a) and is about to PUT; the newer writer
        # lands both its writes first. Without If-Match the older PUT then overwrites current.
        if key.endswith("current.json") and "if-match" in hdrs and fake.before_put:
            fake.before_put = None
            fake.store(f"{BASE}/history/20261106T120003000001Z-cccccccccccc.json", _enc(newer))
            fake.store(key, _enc(newer))

    fake.before_put = compete
    s.append(older)
    assert json.loads(fake.objects[f"{BASE}/current.json"])["sha"] == "b" * 40  # stale on disk
    assert s.current(REPO, "prod") == newer  # but the read is right


# ---- compressed responses --------------------------------------------------------------


def _gz(data, status=200, **headers):
    return httpx.Response(
        status, content=gzip.compress(data), headers={"Content-Encoding": "gzip", **headers}
    )


def test_gzip_get_is_decoded_once():
    body = b'{"x": 1}\n'
    client = _client(lambda r: _gz(body, ETag='"e1"'))
    assert client.get("k") == (body, '"e1"')


def test_gzip_list_parses():
    doc = (
        f'<ListBucketResult xmlns="{NS}"><IsTruncated>false</IsTruncated>'
        "<Contents><Key>p/x.json</Key></Contents></ListBucketResult>"
    ).encode()
    assert _client(lambda r: _gz(doc)).list_keys("p/") == ["p/x.json"]


def test_gzip_error_body_still_gives_the_code():
    body = b"<Error><Code>AccessDenied</Code></Error>"
    with pytest.raises(S3Error, match="HTTP 403 AccessDenied"):
        _client(lambda r: _gz(body, status=403)).get("k")


def test_gzip_bomb_is_capped_on_the_decoded_size():
    bomb = b"\0" * (5 << 20)  # 5 MiB decoded, a few KiB on the wire
    assert len(gzip.compress(bomb)) < (1 << 20)
    with pytest.raises(S3Error, match="larger than"):
        _client(lambda r: _gz(bomb, ETag='"e"')).get("k")


def test_corrupt_gzip_is_an_s3_error():
    def handler(request):
        return httpx.Response(200, content=b"not gzip", headers={"Content-Encoding": "gzip"})

    with pytest.raises(S3Error, match="DecodingError"):
        _client(handler).get("k")
