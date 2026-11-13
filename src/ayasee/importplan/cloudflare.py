"""Cloudflare lookups for ``ayasee import-plan`` (provider ``cloudflare/cloudflare`` v5).

Read-only: the client below has no method other than GET. The token is read from
``CLOUDFLARE_API_TOKEN``, the variable the provider reads, and is never part of a message; the
read permissions of a plan token are enough (Zone DNS Read, Cloudflare Tunnel Read, Access: Apps
and Policies Read). ``CLOUDFLARE_API_BASE_URL`` overrides the API URL (https only).

What identifies a live object, and the import ID written for it (formats of provider v5):

| Resource | Matched on | Import ID |
|---|---|---|
| ``cloudflare_dns_record`` | zone, exact name (FQDN, case-insensitive) and type | ``<zone_id>/<record_id>`` |
| ``cloudflare_zero_trust_tunnel_cloudflared_config`` | account and tunnel (one config per tunnel) | ``<account_id>/<tunnel_id>`` |
| ``cloudflare_zero_trust_access_application`` | account, domain (case-insensitive) and type | ``accounts/<account_id>/<app_id>`` |
| ``cloudflare_zero_trust_access_policy`` | account and exact name (reusable policies) | ``<account_id>/<policy_id>`` |
"""  # noqa: E501

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from ayasee.importplan import ImportPlanError, Planned, Unmatchable, check_id

DEFAULT_API_URL = "https://api.cloudflare.com/client/v4"
PER_PAGE = 100
MAX_PAGES = 50


class Cloudflare:
    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0) -> None:
        if urlparse(base_url).scheme != "https":
            raise ImportPlanError("CLOUDFLARE_API_BASE_URL must be https")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            follow_redirects=False,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )

    @classmethod
    def from_env(cls) -> Cloudflare:
        token = os.environ.get("CLOUDFLARE_API_TOKEN")
        if not token:
            raise ImportPlanError("CLOUDFLARE_API_TOKEN is not set")
        return cls(os.environ.get("CLOUDFLARE_API_BASE_URL") or DEFAULT_API_URL, token)

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            resp = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise ImportPlanError(f"cloudflare: request failed: {type(exc).__name__}") from None
        if resp.status_code != 200:
            # The status only: an error message can echo request values.
            raise ImportPlanError(f"cloudflare: HTTP {resp.status_code} on a lookup")
        try:
            body = resp.json()
        except ValueError:
            raise ImportPlanError("cloudflare: response is not JSON") from None
        if not isinstance(body, dict) or body.get("success") is not True:
            raise ImportPlanError("cloudflare: unsuccessful response")
        return body

    def get_one(self, path: str) -> dict[str, Any]:
        result = self._get(path).get("result")
        if not isinstance(result, dict):
            raise ImportPlanError("cloudflare: unexpected response shape")
        return result

    def get_all(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            body = self._get(path, {**(params or {}), "page": page, "per_page": PER_PAGE})
            result = body.get("result")
            if not isinstance(result, list) or not all(isinstance(r, dict) for r in result):
                raise ImportPlanError("cloudflare: unexpected response shape")
            items.extend(result)
            info = body.get("result_info")
            total = info.get("total_pages") if isinstance(info, dict) else None
            if not isinstance(total, int) or page >= total or not result:
                return items
        raise ImportPlanError(f"cloudflare: more than {MAX_PAGES} pages of results")

    def find(self, planned: Planned) -> list[str]:
        return _FINDERS[planned.type](self, planned)


def _ident(planned: Planned, attr: str) -> str:
    """A planned ID used in a request path: refused unless it is a plain identifier."""
    return check_id(planned.known_str(attr))


def _ids(items: list[dict[str, Any]]) -> list[str]:
    out = []
    for item in items:
        value = item.get("id")
        if not isinstance(value, str):
            raise ImportPlanError("cloudflare: a listed object has no id")
        out.append(check_id(value))
    return out


def _host(value: str) -> str:
    return value.strip().rstrip(".").lower()


def _dns_record(cf: Cloudflare, p: Planned) -> list[str]:
    name = p.known_str("name")
    rtype = p.known_str("type")
    if name == "@" or "." not in name:
        raise Unmatchable("name is not a fully qualified hostname; write this block by hand")
    zone = _ident(p, "zone_id")
    listed = cf.get_all(f"/zones/{zone}/dns_records", {"type": rtype, "name.exact": name})
    hits = [
        r
        for r in listed
        if isinstance(r.get("name"), str)
        and _host(r["name"]) == _host(name)
        and r.get("type") == rtype
    ]
    return [f"{zone}/{i}" for i in _ids(hits)]


def _tunnel_config(cf: Cloudflare, p: Planned) -> list[str]:
    account = _ident(p, "account_id")
    tunnel = _ident(p, "tunnel_id")
    # A tunnel has exactly one configuration; reading it proves the tunnel exists and is
    # readable, and an error stops the run instead of writing an import for nothing.
    result = cf.get_one(f"/accounts/{account}/cfd_tunnel/{tunnel}/configurations")
    if result.get("tunnel_id") not in (None, tunnel):
        raise ImportPlanError("cloudflare: the tunnel configuration names another tunnel")
    return [f"{account}/{tunnel}"]


def _access_application(cf: Cloudflare, p: Planned) -> list[str]:
    if p.after.get("zone_id") and not p.after.get("account_id"):
        raise Unmatchable("zone-scoped applications have no lookup; write this block by hand")
    account = _ident(p, "account_id")
    domain = p.known_str("domain")
    app_type = p.known_str("type")
    listed = cf.get_all(f"/accounts/{account}/access/apps", {"domain": domain})
    hits = [
        a
        for a in listed
        if isinstance(a.get("domain"), str)
        and _host(a["domain"]) == _host(domain)
        and a.get("type") == app_type
    ]
    return [f"accounts/{account}/{i}" for i in _ids(hits)]


def _access_policy(cf: Cloudflare, p: Planned) -> list[str]:
    account = _ident(p, "account_id")
    name = p.known_str("name")
    listed = cf.get_all(f"/accounts/{account}/access/policies")
    return [f"{account}/{i}" for i in _ids([x for x in listed if x.get("name") == name])]


_FINDERS: dict[str, Callable[[Cloudflare, Planned], list[str]]] = {
    "cloudflare_dns_record": _dns_record,
    "cloudflare_zero_trust_tunnel_cloudflared_config": _tunnel_config,
    "cloudflare_zero_trust_access_application": _access_application,
    "cloudflare_zero_trust_access_policy": _access_policy,
}
TYPES = frozenset(_FINDERS)
