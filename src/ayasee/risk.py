"""Tiered risk classification of plan changes, with principal deltas for access types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any

import yaml

from ayasee.planjson import Action, ResourceChange

GENERIC_DESTROY_ID = "generic-destroy"


class RuleError(ValueError):
    """A rule file is malformed or tries to lower an existing rule's tier."""


class Tier(IntEnum):
    NONE = 0
    MEDIUM = 1
    HIGH = 2
    CRITICAL = 3


@dataclass(frozen=True)
class Rule:
    id: str
    tier: Tier
    types: tuple[str, ...]
    actions: frozenset[Action]
    attr_true: tuple[str, ...] = ()
    attr_changed: tuple[str, ...] = ()  # match only if before[attr] != after[attr]
    # match only if before[attr] or after[attr] is one of the listed strings
    attr_in: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class PrincipalDelta:
    gained: dict[str, tuple[str, ...]]
    lost: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class RiskItem:
    unit: str
    address: str
    actions: tuple[Action, ...]
    tier: Tier
    rule_ids: tuple[str, ...]
    principal_delta: PrincipalDelta | None


_ALLOWED_KEYS = {"id", "tier", "types", "actions", "attr_true", "attr_changed", "attr_in"}
_ANY_ACTIONS = frozenset(Action) - {Action.NOOP, Action.READ}


def _str_list(raw: dict, key: str, path: Path, required: bool = False) -> tuple[str, ...]:
    value = raw.get(key)
    if value is None:
        if required:
            raise RuleError(f"{path}: rule {raw.get('id')!r} is missing {key!r}")
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RuleError(f"{path}: rule {raw.get('id')!r}: {key!r} must be a list of strings")
    return tuple(value)


def _attr_in(raw: dict, path: Path) -> tuple[tuple[str, tuple[str, ...]], ...]:
    value = raw.get("attr_in")
    if value is None:
        return ()
    if not isinstance(value, dict) or not value:
        raise RuleError(f"{path}: rule {raw.get('id')!r}: 'attr_in' must be a non-empty mapping")
    out = []
    for attr, allowed in value.items():
        if (
            not isinstance(attr, str)
            or not isinstance(allowed, list)
            or not allowed
            or not all(isinstance(v, str) for v in allowed)
        ):
            raise RuleError(
                f"{path}: rule {raw.get('id')!r}: 'attr_in' maps an attribute to a non-empty "
                "list of strings"
            )
        out.append((attr, tuple(allowed)))
    return tuple(out)


def _parse_rule(raw: Any, path: Path) -> Rule:
    if not isinstance(raw, dict):
        raise RuleError(f"{path}: each rule must be a mapping")
    unknown = set(raw) - _ALLOWED_KEYS
    if unknown:
        raise RuleError(f"{path}: rule {raw.get('id')!r} has unknown keys {sorted(unknown)}")
    if not isinstance(raw.get("id"), str):
        raise RuleError(f"{path}: rule is missing a string 'id'")
    try:
        tier = Tier[str(raw.get("tier")).upper()]
    except KeyError:
        raise RuleError(
            f"{path}: rule {raw['id']!r} has invalid tier {raw.get('tier')!r}"
        ) from None
    if tier is Tier.NONE:
        raise RuleError(f"{path}: rule {raw['id']!r} tier must be critical, high or medium")
    actions: set[Action] = set()
    for name in _str_list(raw, "actions", path, required=True):
        if name == "any":
            actions |= _ANY_ACTIONS
            continue
        try:
            actions.add(Action(name))
        except ValueError:
            raise RuleError(f"{path}: rule {raw['id']!r} has unknown action {name!r}") from None
    return Rule(
        id=raw["id"],
        tier=tier,
        types=_str_list(raw, "types", path, required=True),
        actions=frozenset(actions),
        attr_true=_str_list(raw, "attr_true", path),
        attr_changed=_str_list(raw, "attr_changed", path),
        attr_in=_attr_in(raw, path),
    )


def load_rules(*paths: Path) -> list[Rule]:
    """Load rule files in order. Later files add rules or replace one at the same or higher tier."""
    rules: dict[str, Rule] = {}
    for path in paths:
        path = Path(path)
        try:
            doc = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise RuleError(f"{path}: invalid YAML: {exc}") from None
        if not isinstance(doc, list):
            raise RuleError(f"{path}: rule file must be a list of rules")
        for raw in doc:
            rule = _parse_rule(raw, path)
            existing = rules.get(rule.id)
            if existing is not None and rule.tier < existing.tier:
                raise RuleError(
                    f"{path}: rule {rule.id!r} lowers tier {existing.tier.name} to "
                    f"{rule.tier.name}; adopter rules may only raise a tier"
                )
            rules[rule.id] = rule
    return list(rules.values())


def _matches(rule: Rule, rc: ResourceChange) -> bool:
    if "*" not in rule.types and rc.type not in rule.types:
        return False
    if not rule.actions.intersection(rc.actions):
        return False
    before, after = rc.before or {}, rc.after or {}
    source = after if rc.after is not None else before
    if any(source.get(attr) is not True for attr in rule.attr_true):
        return False
    if any(
        before.get(attr) not in allowed and after.get(attr) not in allowed
        for attr, allowed in rule.attr_in
    ):
        return False
    return all(before.get(attr) != after.get(attr) for attr in rule.attr_changed)


def classify(unit: str, changes: list[ResourceChange], rules: list[Rule]) -> list[RiskItem]:
    items = []
    for rc in changes:
        if rc.mode == "data":
            continue
        matched = [r for r in rules if r.id != GENERIC_DESTROY_ID and _matches(r, rc)]
        if not matched:
            matched = [r for r in rules if r.id == GENERIC_DESTROY_ID and _matches(r, rc)]
        tier = max((r.tier for r in matched), default=Tier.NONE)
        items.append(
            RiskItem(
                unit=unit,
                address=rc.address,
                actions=rc.actions,
                tier=tier,
                rule_ids=tuple(sorted(r.id for r in matched)),
                principal_delta=principal_delta(rc),
            )
        )
    items.sort(key=lambda i: (-i.tier, i.address))
    return items


# --- principal deltas -------------------------------------------------------------------

# Cloudflare Access policies and groups: who is included, excluded or required (issue #1).
# cloudflare_access_* are the provider v4 names, cloudflare_zero_trust_access_* the v5 ones.
_ACCESS_TYPES = {
    "cloudflare_zero_trust_access_policy",
    "cloudflare_access_policy",
    "cloudflare_zero_trust_access_group",
    "cloudflare_access_group",
}
_ACCESS_SECTIONS = ("include", "exclude", "require")

_PRINCIPAL_TYPES = {
    "databricks_grants",
    "databricks_grant",
    "databricks_permissions",
    "azurerm_role_assignment",
} | _ACCESS_TYPES


def _flagged(marker: Any) -> bool:
    """True if a sensitive/unknown marker (bool, list or dict tree) marks anything."""
    if isinstance(marker, bool):
        return marker
    if isinstance(marker, dict):
        return any(_flagged(v) for v in marker.values())
    if isinstance(marker, list):
        return any(_flagged(v) for v in marker)
    return False


def _condition(kind: str, value: Any) -> list[str]:
    """Principal names of one Access rule condition, such as ``email:a@example.com``.

    v5 conditions are objects (``{email = {email = "..."}}``, ``{everyone = {}}``), v4 ones are
    lists or booleans (``email = ["..."]``, ``everyone = true``). Empty and false conditions
    name nobody.
    """
    if value is None or value is False or value == [] or value == "":
        return []
    if value is True:
        return [kind]
    if isinstance(value, str):
        return [f"{kind}:{value}"]
    if isinstance(value, list):
        return [p for item in value for p in _condition(kind, item)]
    if isinstance(value, dict):
        parts = sorted(
            str(v) for v in value.values() if isinstance(v, str | int) and not isinstance(v, bool)
        )
        return [f"{kind}:{'/'.join(parts)}" if parts else kind]
    return [f"{kind}:?"]


def _access_entries(values: dict) -> list[tuple[str, str]]:
    """(principal, "<decision>:<section>") pairs of an Access policy or group."""
    decision = values.get("decision") or "member"
    out = []
    for section in _ACCESS_SECTIONS:
        for rule in values.get(section) or []:
            if not isinstance(rule, dict):
                continue
            for kind, value in sorted(rule.items()):
                out += [(who, f"{decision}:{section}") for who in _condition(kind, value)]
    return out


def _entries(rc: ResourceChange, values: dict) -> list[tuple[str, str]]:
    """(principal, privilege/level/role) pairs for one side of the change."""
    if rc.type in _ACCESS_TYPES:
        return _access_entries(values)
    if rc.type == "databricks_grants":
        return [
            (g["principal"], p)
            for g in values.get("grant") or []
            for p in g.get("privileges") or []
        ]
    if rc.type == "databricks_grant":
        return [(values["principal"], p) for p in values.get("privileges") or []] if values else []
    if rc.type == "databricks_permissions":
        out = []
        for ac in values.get("access_control") or []:
            name = ac.get("user_name") or ac.get("group_name") or ac.get("service_principal_name")
            out.append((name, ac["permission_level"]))
        return out
    role = values.get("role_definition_name") or values.get("role_definition_id")
    return [(values["principal_id"], role)] if values else []


_BLOCK_KEYS = {
    "databricks_grants": ("grant",),
    "databricks_grant": ("principal", "privileges"),
    "databricks_permissions": ("access_control",),
    "azurerm_role_assignment": ("principal_id", "role_definition_name", "role_definition_id"),
    **{t: (*_ACCESS_SECTIONS, "decision") for t in _ACCESS_TYPES},
}


def _side_hidden(rc: ResourceChange, marker: dict | bool) -> bool:
    if isinstance(marker, bool):
        return marker
    return any(_flagged(marker.get(k)) for k in _BLOCK_KEYS[rc.type])


def principal_delta(rc: ResourceChange) -> PrincipalDelta | None:
    """Principals gained and lost by a change, or None if not applicable or not visible."""
    if rc.type not in _PRINCIPAL_TYPES:
        return None
    if (
        (rc.before is not None and _side_hidden(rc, rc.before_sensitive))
        or (rc.after is not None and _side_hidden(rc, rc.after_sensitive))
        or _side_hidden(rc, rc.after_unknown)
    ):
        return None
    before = set(_entries(rc, rc.before or {}))
    after = set(_entries(rc, rc.after or {}))

    def group(pairs: set[tuple[str, str]]) -> dict[str, tuple[str, ...]]:
        out: dict[str, list[str]] = {}
        for principal, what in pairs:
            out.setdefault(principal, []).append(what)
        return {p: tuple(sorted(v)) for p, v in sorted(out.items())}

    return PrincipalDelta(gained=group(after - before), lost=group(before - after))
