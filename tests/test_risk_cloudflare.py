"""The Cloudflare risk rules and Access principal deltas (issue #1)."""

from pathlib import Path

import pytest

from ayasee.changeset import PACKAGED_RULES, _default_rules
from ayasee.planjson import Action, ResourceChange
from ayasee.risk import RuleError, Tier, classify, load_rules, principal_delta

ROOT = Path(__file__).parents[1]
RULES = [ROOT / "rules" / name for name in PACKAGED_RULES]


@pytest.fixture(scope="module")
def rules():
    return load_rules(*RULES)


def rc(type_, actions, before=None, after=None, **kw):
    return ResourceChange(
        address=f"{type_}.x",
        type=type_,
        provider="registry.opentofu.org/cloudflare/cloudflare",
        mode="managed",
        actions=tuple(Action(a) for a in actions),
        before=before,
        after=after,
        after_unknown=kw.get("after_unknown", {}),
        before_sensitive=kw.get("before_sensitive", {}),
        after_sensitive=kw.get("after_sensitive", {}),
        previous_address=None,
    )


def item(change, rules):
    (out,) = classify("u", [change], rules)
    return out


def test_packaged_rules_are_both_files_in_order():
    assert PACKAGED_RULES == ("databricks-azure.yaml", "cloudflare.yaml")
    assert [p.name for p in _default_rules()] == list(PACKAGED_RULES)
    assert all(p.exists() for p in _default_rules())


def test_rule_ids_do_not_collide(rules):
    ids = [r.id for r in rules]
    assert len(ids) == len(set(ids))
    cf = [r.id for r in load_rules(RULES[1])]
    assert all(i.startswith("cf-") for i in cf)


@pytest.mark.parametrize(
    "type_", ["cloudflare_zero_trust_access_application", "cloudflare_access_application"]
)
@pytest.mark.parametrize("action", ["delete", "forget"])
def test_access_application_removal_is_critical(rules, type_, action):
    got = item(rc(type_, [action], before={"domain": "app.example.com"}), rules)
    assert got.tier == Tier.CRITICAL and "cf-access-destroy" in got.rule_ids


def test_access_policy_change_is_high_with_principal_delta(rules):
    before = {
        "decision": "allow",
        "include": [
            {"email": {"email": "alice@example.com"}, "group": None},
            {"group": {"id": "g-ops"}},
        ],
        "exclude": [],
        "require": None,
    }
    after = {
        "decision": "allow",
        "include": [{"email": {"email": "alice@example.com"}}, {"everyone": {}}],
        "exclude": [{"ip": {"ip": "192.0.2.0/24"}}],
    }
    got = item(rc("cloudflare_zero_trust_access_policy", ["update"], before, after), rules)
    assert got.tier == Tier.HIGH and got.rule_ids == ("cf-access",)
    assert got.principal_delta.gained == {
        "everyone": ("allow:include",),
        "ip:192.0.2.0/24": ("allow:exclude",),
    }
    assert got.principal_delta.lost == {"group:g-ops": ("allow:include",)}


def test_v4_access_policy_lists_and_booleans():
    before = {"decision": "allow", "include": [{"email": ["a@example.com"], "everyone": False}]}
    after = {
        "decision": "allow",
        "include": [{"email": ["a@example.com", "b@example.com"], "everyone": True}],
        "require": [{"github": [{"name": "org", "teams": ["t"]}]}],
    }
    delta = principal_delta(rc("cloudflare_access_policy", ["update"], before, after))
    assert delta.gained == {
        "email:b@example.com": ("allow:include",),
        "everyone": ("allow:include",),
        "github:org": ("allow:require",),
    }
    assert delta.lost == {}


def test_access_group_members_use_member_as_decision():
    after = {"include": [{"email_domain": {"domain": "example.com"}}]}
    delta = principal_delta(rc("cloudflare_zero_trust_access_group", ["create"], None, after))
    assert delta.gained == {"email_domain:example.com": ("member:include",)}


def test_sensitive_or_unknown_access_rules_hide_the_delta():
    after = {"decision": "allow", "include": [{"email": {"email": "a@example.com"}}]}
    hidden = rc(
        "cloudflare_zero_trust_access_policy",
        ["create"],
        None,
        after,
        after_sensitive={"include": [{"email": {"email": True}}]},
    )
    assert principal_delta(hidden) is None
    unknown = rc(
        "cloudflare_zero_trust_access_policy",
        ["create"],
        None,
        after,
        after_unknown={"include": True},
    )
    assert principal_delta(unknown) is None


@pytest.mark.parametrize(
    "type_", ["cloudflare_zero_trust_tunnel_cloudflared_config", "cloudflare_tunnel_config"]
)
def test_tunnel_ingress_change_is_high(rules, type_):
    got = item(rc(type_, ["update"], {"config": {}}, {"config": {}}), rules)
    assert got.tier == Tier.HIGH and got.rule_ids == ("cf-tunnel-ingress",)


@pytest.mark.parametrize("type_", ["cloudflare_ruleset", "cloudflare_firewall_rule"])
def test_firewall_removal_is_critical_and_change_high(rules, type_):
    assert item(rc(type_, ["delete"], {"x": 1}), rules).tier == Tier.CRITICAL
    assert item(rc(type_, ["update"], {"x": 1}, {"x": 2}), rules).tier == Tier.HIGH


@pytest.mark.parametrize("type_", ["cloudflare_dns_record", "cloudflare_record"])
def test_dns_rules(rules, type_):
    def dns(before, after, actions=("update",)):
        return item(rc(type_, list(actions), before, after), rules)

    a = {"name": "www", "type": "A", "proxied": True}
    off = dns(a, {**a, "proxied": False})
    assert off.tier == Tier.HIGH and off.rule_ids == ("cf-dns-proxied",)
    assert dns(a, {**a, "ttl": 60}).tier == Tier.NONE
    mx = {"name": "@", "type": "MX", "proxied": False}
    got = dns(mx, {**mx, "priority": 20})
    assert got.tier == Tier.HIGH and set(got.rule_ids) == {"cf-dns-mx", "cf-dns-apex"}
    # a deleted MX record matches on its before value
    gone = dns({"name": "mail", "type": "MX"}, None, ("delete",))
    assert "cf-dns-mx" in gone.rule_ids and gone.tier == Tier.HIGH
    assert dns(None, {"name": "@", "type": "A"}, ("create",)).rule_ids == ("cf-dns-apex",)


def test_databricks_rules_unchanged_by_the_second_pack(rules):
    got = item(rc("azurerm_databricks_workspace", ["delete"], {"name": "w"}), rules)
    assert got.tier == Tier.CRITICAL and got.rule_ids == ("dbx-workspace-destroy",)


@pytest.mark.parametrize(
    "text",
    [
        "attr_in: {type: MX}",
        "attr_in: {type: []}",
        "attr_in: {type: [1]}",
        "attr_in: []",
        "attr_in: {}",
    ],
)
def test_attr_in_is_validated(tmp_path, text):
    p = tmp_path / "r.yaml"
    p.write_text(f"- id: x\n  tier: high\n  types: ['*']\n  actions: [any]\n  {text}\n")
    with pytest.raises(RuleError, match="attr_in"):
        load_rules(p)
