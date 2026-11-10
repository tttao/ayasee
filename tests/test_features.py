import json

import httpx
import pytest
import respx

from ayasee import cli, features
from ayasee.features import Features, detect
from ayasee.github import GitHub, GitHubError

DOTCOM = "https://api.github.com"
GHES = "https://ghes.example.com/api/v3"


def _gh(base: str = DOTCOM) -> GitHub:
    return GitHub(base, "tok")


@respx.mock
def test_ghes_issuer_format():
    respx.get(f"{GHES}/meta").mock(
        return_value=httpx.Response(200, json={"installed_version": "3.14.2"})
    )
    f = detect(_gh(GHES))
    assert f.platform == "ghes"
    assert f.oidc_issuer == "https://ghes.example.com/_services/token"


@respx.mock
def test_dotcom_defaults():
    respx.get(f"{DOTCOM}/meta").mock(return_value=httpx.Response(200, json={"hooks": []}))
    assert detect(_gh()) == Features(
        platform="dotcom",
        ghes_version=None,
        queue_max=True,
        deployment_false=True,
        lockfiles=True,
        private_pages=False,
        oidc_issuer="https://token.actions.githubusercontent.com",
    )


@respx.mock
def test_unknown_feature_defaults_false():
    respx.get(f"{GHES}/meta").mock(
        return_value=httpx.Response(200, json={"installed_version": "3.99.0"})
    )
    f = detect(_gh(GHES))
    assert (f.queue_max, f.deployment_false, f.lockfiles) == (False, False, False)
    assert f.ghes_version == "3.99.0"


@respx.mock
def test_bare_host_and_api_v3_give_the_same_issuer():
    for base in ("https://ghes.example.com", "https://ghes.example.com/api/v3/"):
        respx.get(f"{base.rstrip('/')}/meta").mock(
            return_value=httpx.Response(200, json={"installed_version": "3.14.0"})
        )
        assert detect(_gh(base)).oidc_issuer == "https://ghes.example.com/_services/token"


@respx.mock
def test_issuer_keeps_the_port():
    base = "https://ghes.example.com:8443/api/v3"
    respx.get(f"{base}/meta").mock(
        return_value=httpx.Response(200, json={"installed_version": "3.14.0"})
    )
    assert detect(_gh(base)).oidc_issuer == "https://ghes.example.com:8443/_services/token"


@respx.mock
def test_private_pages_is_the_override_only():
    respx.get(f"{DOTCOM}/meta").mock(return_value=httpx.Response(200, json={}))
    assert detect(_gh()).private_pages is False
    assert detect(_gh(), private_pages=True).private_pages is True
    assert detect(_gh(), private_pages=False).private_pages is False


@respx.mock
def test_meta_error_raises():
    respx.get(f"{GHES}/meta").mock(return_value=httpx.Response(500, json={"message": "boom"}))
    with pytest.raises(GitHubError) as exc:
        detect(_gh(GHES))
    assert exc.value.status == 500


@respx.mock
def test_meta_that_is_not_an_object_raises():
    respx.get(f"{DOTCOM}/meta").mock(return_value=httpx.Response(200, json=[1]))
    with pytest.raises(GitHubError):
        detect(_gh())


@pytest.mark.parametrize(
    ("raw", "parsed"),
    [("3.14.2", (3, 14, 2)), ("3.9", (3, 9, 0)), ("3.15.0-rc1", (3, 15, 0))],
)
def test_ghes_version_parsing(raw, parsed):
    assert features.parse_version(raw) == parsed


@pytest.mark.parametrize("raw", ["", "abc", "3", "x.y.z"])
def test_unparseable_ghes_version_is_unknown(raw):
    assert features.parse_version(raw) is None


@respx.mock
def test_version_table_lookup(monkeypatch):
    monkeypatch.setattr(features, "GHES_FEATURE_MIN_VERSIONS", {"queue_max": (3, 15, 0)})
    respx.get(f"{GHES}/meta").mock(
        side_effect=[
            httpx.Response(200, json={"installed_version": "3.14.9"}),
            httpx.Response(200, json={"installed_version": "3.15.0"}),
            httpx.Response(200, json={"installed_version": "not-a-version"}),
        ]
    )
    assert detect(_gh(GHES)).queue_max is False
    newer = detect(_gh(GHES))
    assert newer.queue_max is True
    assert newer.deployment_false is False  # no table entry: unknown means false
    assert detect(_gh(GHES)).queue_max is False


@respx.mock
def test_cli_prints_json(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    respx.get(f"{DOTCOM}/meta").mock(return_value=httpx.Response(200, json={}))
    assert cli.main(["features", "--json", "--private-pages", "true"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["platform"] == "dotcom" and out["private_pages"] is True
    assert set(out) == {
        "platform",
        "ghes_version",
        "queue_max",
        "deployment_false",
        "lockfiles",
        "private_pages",
        "oidc_issuer",
    }


@respx.mock
def test_cli_error_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    respx.get(f"{DOTCOM}/meta").mock(return_value=httpx.Response(503, json={"message": "down"}))
    assert cli.main(["features", "--json"]) == 1
    assert "features: error" in capsys.readouterr().err


@respx.mock
def test_ghec_data_residency_platform_and_issuer():
    base = "https://api.acme.ghe.com"
    respx.get(f"{base}/meta").mock(return_value=httpx.Response(200, json={"hooks": []}))
    f = detect(_gh(base))
    assert f.platform == "ghec-dr" and f.ghes_version is None
    assert f.oidc_issuer == "https://token.actions.acme.ghe.com"
    assert (f.queue_max, f.deployment_false, f.lockfiles) == (False, False, False)


@respx.mock
def test_unknown_host_without_installed_version_raises():
    base = "https://git.example.com/api/v3"
    respx.get(f"{base}/meta").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(GitHubError, match="unknown platform"):
        detect(_gh(base))
