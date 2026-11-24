"""Can this repository take a SARIF upload (issue #55)? Code scanning is free on a public
repository; a private one needs GitHub Code Security. Anything not established is UNKNOWN,
never AVAILABLE."""

import httpx
import pytest
import respx

from ayasee import codescanning as cs
from ayasee.github import GitHub

API = "https://api.github.com"
REPO = "acme/estate"
ALERTS = f"{API}/repos/{REPO}/code-scanning/alerts"


@pytest.fixture
def gh():
    client = GitHub(API, "tok")
    yield client
    client.close()


def _repo(visibility="private", security=None, status=200):
    body = {"visibility": visibility, "owner": {"login": "acme", "type": "Organization"}}
    if security is not None:
        body["security_and_analysis"] = security
    return respx.get(f"{API}/repos/{REPO}").mock(return_value=httpx.Response(status, json=body))


def _alerts(status=200, message=""):
    body = [] if status == 200 else {"message": message}
    return respx.get(ALERTS).mock(return_value=httpx.Response(status, json=body))


@respx.mock
def test_a_public_repository_has_code_scanning_without_a_probe(gh):
    _repo("public")
    probe = _alerts(403, "Code scanning is not enabled for this repository")
    got = cs.detect(gh, REPO)
    assert got.verdict == cs.AVAILABLE and got.visibility == "public"
    assert not probe.called


@respx.mock
@pytest.mark.parametrize("key", ["code_security", "advanced_security"])
def test_code_security_enabled_is_available(gh, key):
    _repo(security={key: {"status": "enabled"}})
    probe = _alerts()
    assert cs.detect(gh, REPO).verdict == cs.AVAILABLE
    assert not probe.called


@respx.mock
def test_code_security_disabled_is_unavailable(gh):
    _repo(
        security={
            "code_security": {"status": "disabled"},
            "secret_scanning": {"status": "enabled"},
        }
    )
    probe = _alerts()
    got = cs.detect(gh, REPO)
    assert got.verdict == cs.UNAVAILABLE and "GitHub Code Security" in "; ".join(got.evidence)
    assert not probe.called


@respx.mock
@pytest.mark.parametrize(
    "message",
    [
        "Code scanning is not enabled for this repository",
        "Advanced Security must be enabled for this repository to use code scanning.",
        "Code Security must be enabled for this repository to use code scanning.",
    ],
)
def test_the_api_saying_not_enabled_is_unavailable(gh, message):
    _repo()  # an administrator's token would show security_and_analysis; this one does not
    _alerts(403, message)
    got = cs.detect(gh, REPO)
    assert got.verdict == cs.UNAVAILABLE and got.visibility == "private"


@respx.mock
def test_the_api_answering_is_available(gh):
    _repo()
    _alerts()
    assert cs.detect(gh, REPO).verdict == cs.AVAILABLE


@respx.mock
def test_enabled_with_no_analysis_yet_is_available(gh):
    _repo()
    _alerts(404, "no analysis found")
    assert cs.detect(gh, REPO).verdict == cs.AVAILABLE


@respx.mock
@pytest.mark.parametrize(
    "status, message",
    [
        (403, "Resource not accessible by personal access token"),  # a missing scope
        (404, "Not Found"),  # cannot see the repository
        (500, "boom"),
    ],
)
def test_anything_else_is_unknown_never_available(gh, status, message):
    _repo()
    _alerts(status, message)
    got = cs.detect(gh, REPO)
    assert got.verdict == cs.UNKNOWN and got.evidence


@respx.mock
def test_an_unreadable_repository_is_unknown(gh):
    _repo(status=404)
    got = cs.detect(gh, REPO)
    assert got.verdict == cs.UNKNOWN and got.visibility is None


@respx.mock
def test_an_unreachable_api_is_unknown(gh):
    respx.get(f"{API}/repos/{REPO}").mock(side_effect=httpx.ConnectError("down"))
    assert cs.detect(gh, REPO).verdict == cs.UNKNOWN
