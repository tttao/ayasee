"""A minimal S3 client for the ledger: GetObject, conditional PutObject, ListObjectsV2.

Signed with AWS Signature Version 4 using only the standard library and ``httpx`` (already a
dependency), so the ``s3://`` ledger store adds nothing to the hash-pinned install. It covers
exactly what the ledger needs and nothing else: no multipart upload, no retries beyond what the
caller does, no credential chain beyond environment variables.

Credentials come from ``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY`` and, optionally,
``AWS_SESSION_TOKEN`` (temporary credentials, for example from an OIDC role assumption done by an
earlier step). Error messages carry the HTTP status and the S3 error code only: never a header, a
credential or a response body.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urlparse
from xml.parsers import expat

import httpx

ALGORITHM = "AWS4-HMAC-SHA256"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_CODE = re.compile(r"<Code>([A-Za-z0-9.]{1,64})</Code>")
_REGION = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
MAX_LIST_PAGES = 100  # 100 000 keys; a ledger directory that large is a bug, not a ledger
MAX_OBJECT_BYTES = 1 << 20  # a ledger record is a few hundred bytes
MAX_LIST_BYTES = 16 << 20  # one ListObjectsV2 page of 1000 keys is well under 1 MiB


class S3Error(Exception):
    """An S3 request failed. ``status`` is the HTTP status (0 for a transport error)."""

    def __init__(self, message: str, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class PreconditionFailed(S3Error):
    """A conditional write lost: 412 Precondition Failed, or 409 ConditionalRequestConflict
    (a concurrent write to the same key). Both mean: re-read and decide again."""


@dataclass(frozen=True)
class Credentials:
    access_key: str
    secret_key: str
    session_token: str | None = None

    def __repr__(self) -> str:  # never print a secret, even in a traceback
        return "Credentials(***)"

    @classmethod
    def from_env(cls) -> Credentials:
        ak = os.environ.get("AWS_ACCESS_KEY_ID", "")
        sk = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
        if not ak or not sk:
            raise S3Error("S3 needs AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in the environment")
        token = os.environ.get("AWS_SESSION_TOKEN") or None
        for name, value in (
            ("AWS_ACCESS_KEY_ID", ak),
            ("AWS_SECRET_ACCESS_KEY", sk),
            ("AWS_SESSION_TOKEN", token or ""),
        ):
            # They go into HTTP headers and HMAC keys; name the variable, never the value.
            if not (value.isascii() and value.isprintable()):
                raise S3Error(f"{name} must be printable ASCII")
        return cls(ak, sk, token)


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _uri_encode(s: str, *, slash: bool) -> str:
    return quote(s, safe="-_.~/" if slash else "-_.~")


def canonical_query(query: dict[str, str]) -> str:
    """The query string, encoded once and sorted. It is both signed and sent as is."""
    return "&".join(
        f"{_uri_encode(k, slash=False)}={_uri_encode(v, slash=False)}"
        for k, v in sorted(query.items())
    )


def sign(
    *,
    method: str,
    host: str,
    path: str,
    query: dict[str, str],
    headers: dict[str, str],
    payload_hash: str,
    creds: Credentials,
    region: str,
    amz_date: str,
    service: str = "s3",
) -> str:
    """The ``Authorization`` header value for a request (SigV4, header-based).

    ``headers`` must already hold every header to sign except ``host`` (added here); it must
    include ``x-amz-date`` equal to ``amz_date``. ``path`` is the raw (unencoded) object path;
    S3 encodes it once, keeping ``/``."""
    signed = {k.lower(): " ".join(v.strip().split()) for k, v in headers.items()}
    signed["host"] = host
    names = sorted(signed)
    canonical = "\n".join(
        [
            method,
            _uri_encode(path, slash=True),
            canonical_query(query),
            "".join(f"{n}:{signed[n]}\n" for n in names),
            ";".join(names),
            payload_hash,
        ]
    )
    day = amz_date[:8]
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(
        [ALGORITHM, amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()]
    )
    key = _hmac(("AWS4" + creds.secret_key).encode(), day)
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return (
        f"{ALGORITHM} Credential={creds.access_key}/{scope}, "
        f"SignedHeaders={';'.join(names)}, Signature={signature}"
    )


def check_endpoint(endpoint: str) -> str:
    """Validate an S3-compatible endpoint: ``https://host[:port]``, nothing else.

    Returned normalised the way httpx sends the ``Host`` header (lower-case host, no default
    port), because the signature covers that header."""
    u = urlparse(endpoint)
    if u.scheme != "https" or not u.hostname or u.username or u.password:
        raise S3Error("S3 endpoint must be https://host[:port]")
    if u.path not in ("", "/") or u.query or u.fragment:
        raise S3Error("S3 endpoint must be https://host[:port], without a path or query")
    try:
        port = u.port
    except ValueError:
        raise S3Error("S3 endpoint has an invalid port") from None
    host = u.hostname.lower()
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    return f"https://{host}" + ("" if port in (None, 443) else f":{port}")


@dataclass(frozen=True)
class _Reply:
    """What a request returns: the status, the ETag header and the decoded, capped body."""

    status_code: int
    etag: str | None
    content: bytes


class S3Client:
    """One bucket. Without ``endpoint`` it talks to AWS with virtual-hosted addressing
    (``https://<bucket>.s3.<region>.amazonaws.com``); with ``endpoint`` (an S3-compatible
    store) it uses path-style addressing (``<endpoint>/<bucket>/<key>``)."""

    def __init__(
        self,
        bucket: str,
        region: str,
        endpoint: str | None = None,
        *,
        creds: Callable[[], Credentials] = Credentials.from_env,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not _BUCKET.match(bucket) or ".." in bucket:
            raise S3Error("invalid S3 bucket name")
        if not _REGION.match(region):
            raise S3Error("invalid S3 region")
        if endpoint is None:
            if "." in bucket:  # the *.s3 wildcard certificate does not match dotted names
                raise S3Error("a bucket name with dots needs an explicit endpoint (path-style)")
            self.base = f"https://{bucket}.s3.{region}.amazonaws.com"
            self.key_root = "/"
        else:
            self.base = check_endpoint(endpoint)
            self.key_root = f"/{bucket}/"
        self.host = urlparse(self.base).netloc
        self.region = region
        self._creds_factory = creds
        self._creds: Credentials | None = None
        self._clock = clock
        self._http = httpx.Client(
            transport=transport, timeout=30.0, follow_redirects=False, trust_env=False
        )

    def close(self) -> None:
        self._http.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        limit: int = MAX_OBJECT_BYTES,
    ) -> _Reply:
        """Send a signed request. The body is decoded (any Content-Encoding) and read up to
        ``limit`` decoded bytes; a longer body fails the request instead of being held in
        memory, so a compressed bomb is stopped too."""
        if self._creds is None:
            self._creds = self._creds_factory()
        query = query or {}
        amz_date = self._clock().strftime("%Y%m%dT%H%M%SZ")
        payload_hash = hashlib.sha256(body).hexdigest()
        hdrs = {
            **(headers or {}),
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
        }
        if self._creds.session_token:
            hdrs["x-amz-security-token"] = self._creds.session_token
        hdrs["authorization"] = sign(
            method=method,
            host=self.host,
            path=path,
            query=query,
            headers=hdrs,
            payload_hash=payload_hash,
            creds=self._creds,
            region=self.region,
            amz_date=amz_date,
        )
        url = self.base + _uri_encode(path, slash=True)
        if query:
            url += "?" + canonical_query(query)
        try:
            req = self._http.build_request(method, url, content=body, headers=hdrs)
            resp = self._http.send(req, stream=True)
            try:
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf += chunk
                    if len(buf) > limit:
                        raise S3Error(f"S3 {method} response is larger than {limit} bytes")
            finally:
                resp.close()
        except httpx.HTTPError as exc:
            raise S3Error(f"S3 {method} failed: {type(exc).__name__}") from None
        # Not an httpx.Response: its headers would still name the Content-Encoding of a body
        # that is already decoded, and reading .content would decode it a second time.
        return _Reply(resp.status_code, resp.headers.get("etag"), bytes(buf))

    @staticmethod
    def _fail(method: str, resp: _Reply) -> S3Error:
        m = _CODE.search(resp.content[:4096].decode("utf-8", "replace"))
        code = m.group(1) if m else ""
        cls = PreconditionFailed if resp.status_code in (409, 412) else S3Error
        return cls(
            f"S3 {method} failed: HTTP {resp.status_code}" + (f" {code}" if code else ""),
            resp.status_code,
            code,
        )

    def get(self, key: str) -> tuple[bytes, str] | None:
        """``(body, etag)``, or None if the key does not exist."""
        resp = self._request("GET", self.key_root + key)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise self._fail("GET", resp)
        etag = resp.etag
        if not etag:
            raise S3Error("S3 GET returned no ETag")
        return resp.content, etag

    def put(
        self, key: str, data: bytes, *, if_match: str | None = None, if_none_match: bool = False
    ) -> None:
        """PutObject. ``if_none_match`` creates only; ``if_match`` replaces only that ETag.
        A lost condition raises :class:`PreconditionFailed`."""
        if if_match is not None and if_none_match:
            raise ValueError("if_match and if_none_match are exclusive")
        headers = {"content-type": "application/json"}
        if if_none_match:
            headers["if-none-match"] = "*"
        if if_match is not None:
            headers["if-match"] = if_match
        resp = self._request("PUT", self.key_root + key, body=data, headers=headers)
        if resp.status_code != 200:
            raise self._fail("PUT", resp)

    def list_keys(self, prefix: str) -> list[str]:
        """Every key under ``prefix`` (ListObjectsV2, all pages)."""
        keys: list[str] = []
        token: str | None = None
        for _ in range(MAX_LIST_PAGES):
            query = {"list-type": "2", "prefix": prefix}
            if token:
                query["continuation-token"] = token
            resp = self._request("GET", self.key_root, query=query, limit=MAX_LIST_BYTES)
            if resp.status_code != 200:
                raise self._fail("LIST", resp)
            root = _parse_xml(resp.content)
            keys.extend((el.text or "") for el in root.iterfind(f"{_NS}Contents/{_NS}Key"))
            truncated = (root.findtext(f"{_NS}IsTruncated") or "").strip() == "true"
            token = root.findtext(f"{_NS}NextContinuationToken")
            if not truncated:
                return keys
            if not token:
                raise S3Error("S3 LIST is truncated but has no continuation token")
        raise S3Error(f"S3 LIST returned more than {MAX_LIST_PAGES} pages")


class _DtdRefused(Exception):
    pass


def _refuse_dtd(*_args: object) -> None:
    raise _DtdRefused


def _parse_xml(data: bytes) -> ET.Element:
    # S3 never sends a DTD. A first pass with a bare expat parser (which decodes the document
    # itself, UTF-8 or UTF-16, so no byte search can be bypassed by the encoding) stops at the
    # DOCTYPE or any entity declaration, before an entity can be expanded. Only a document
    # without either reaches ElementTree.
    pre = expat.ParserCreate()
    pre.StartDoctypeDeclHandler = _refuse_dtd
    pre.EntityDeclHandler = _refuse_dtd
    try:
        pre.Parse(data, True)
        return ET.fromstring(data)
    except _DtdRefused:
        raise S3Error("S3 LIST response carries a DTD; refused") from None
    except (expat.ExpatError, ET.ParseError):
        raise S3Error("S3 LIST response is not valid XML") from None
