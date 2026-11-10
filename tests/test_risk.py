from pathlib import Path

import pytest

from ayasee.planjson import Action, ResourceChange, load_plan
from ayasee.risk import PrincipalDelta, RuleError, Tier, classify, load_rules, principal_delta

FIX = Path(__file__).parent / "fixtures" / "plans"
RULES = Path(__file__).parents[1] / "rules" / "databricks-azure.yaml"


@pytest.fixture
def rules():
    return load_rules(RULES)


def one(name, rules):
    items = classify("u", load_plan(FIX / name), rules)
    assert len(items) == 1
    return items[0]


def test_workspace_replace_is_critical(rules):
    items = classify("ws", load_plan(FIX / "workspace_replace.json"), rules)
    assert items[0].tier == Tier.CRITICAL
    assert "dbx-workspace-destroy" in items[0].rule_ids
    assert items[0].unit == "ws"


def test_grants_update_lists_lost_principal(rules):
    item = one("grants_update.json", rules)
    assert item.tier == Tier.HIGH
    assert item.principal_delta.lost == {"analysts": ("SELECT",)}
    assert item.principal_delta.gained == {}


def test_permissions_level_change(rules):
    item = one("permissions_level.json", rules)
    assert item.principal_delta.lost == {"ops": ("CAN_VIEW",)}
    assert item.principal_delta.gained == {"ops": ("CAN_MANAGE",)}


def test_role_assignment_create_gained(rules):
    item = one("role_assignment_create.json", rules)
    assert item.principal_delta.gained == {"<principal_id>": ("Contributor",)}
    assert item.principal_delta.lost == {}


def test_forget_metastore_is_critical(rules):
    item = one("forget_metastore.json", rules)
    assert item.tier == Tier.CRITICAL
    assert item.rule_ids == ("dbx-workspace-destroy",)


def test_moved_only_is_none(rules):
    item = one("moved.json", rules)
    assert item.actions == (Action.MOVE,)
    assert item.tier == Tier.NONE
    assert item.rule_ids == ()


def test_adopter_rules_cannot_lower_tier(tmp_path):
    extra = tmp_path / "x.yaml"
    extra.write_text(
        "- {id: dbx-access, tier: medium, types: [databricks_grants], actions: [any]}\n"
    )
    with pytest.raises(RuleError):
        load_rules(RULES, extra)


def test_adopter_rules_can_raise_and_add(tmp_path):
    extra = tmp_path / "x.yaml"
    extra.write_text(
        "- {id: dbx-compute-policy, tier: critical, types: [databricks_cluster_policy],"
        " actions: [any]}\n"
        "- {id: mine, tier: high, types: ['*'], actions: [create]}\n"
    )
    by_id = {r.id: r for r in load_rules(RULES, extra)}
    assert by_id["dbx-compute-policy"].tier == Tier.CRITICAL
    assert by_id["mine"].tier == Tier.HIGH


def test_sensitive_principal_omitted(rules):
    item = one("grants_sensitive.json", rules)
    assert item.principal_delta is None
    assert item.tier == Tier.HIGH
    assert "dbx-access" in item.rule_ids


def test_unknown_rule_key_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- {id: a, tier: high, types: [x], actions: [create], oops: 1}\n")
    with pytest.raises(RuleError):
        load_rules(bad)


def _rc(typ, actions, before, after, mode="managed", **kw):
    return ResourceChange(
        address=f"{typ}.a",
        type=typ,
        provider="p",
        mode=mode,
        actions=tuple(actions),
        before=before,
        after=after,
        after_unknown=kw.get("after_unknown", {}),
        before_sensitive={},
        after_sensitive={},
        previous_address=None,
    )


def test_force_destroy_and_generic_destroy(rules):
    (a,) = classify(
        "u", [_rc("azurerm_storage_account", [Action.DELETE], {"force_destroy": True}, None)], rules
    )
    assert a.tier == Tier.CRITICAL and a.rule_ids == ("force-destroy",)
    (b,) = classify("u", [_rc("random_pet", [Action.DELETE], {}, None)], rules)
    assert b.tier == Tier.MEDIUM and b.rule_ids == ("generic-destroy",)
    (c,) = classify("u", [_rc("random_pet", [Action.CREATE], None, {})], rules)
    assert c.tier == Tier.NONE


def test_generic_destroy_only_when_nothing_else_matched(rules):
    (a,) = classify("u", [_rc("databricks_grants", [Action.DELETE], {"grant": []}, None)], rules)
    assert a.rule_ids == ("dbx-access",)


def test_attr_changed_and_skip_validation(rules):
    def cat(b, a):
        return _rc(
            "databricks_catalog", [Action.UPDATE], {"isolation_mode": b}, {"isolation_mode": a}
        )

    assert classify("u", [cat("OPEN", "ISOLATED")], rules)[0].tier == Tier.HIGH
    assert classify("u", [cat("OPEN", "OPEN")], rules)[0].tier == Tier.NONE
    (s,) = classify(
        "u", [_rc("databricks_x", [Action.UPDATE], {}, {"skip_validation": True})], rules
    )
    assert s.tier == Tier.MEDIUM and s.rule_ids == ("dbx-unsafe-skip-validation",)


def test_data_sources_skipped_and_sorting(rules):
    changes = [
        _rc("databricks_grants", [Action.UPDATE], {"grant": []}, {"grant": []}),
        _rc("databricks_metastore", [Action.DELETE], {}, None),
        _rc("random_pet", [Action.CREATE], None, {}),
        _rc("azurerm_role_assignment", [Action.UPDATE], {}, {}, mode="data"),
        _rc("azurerm_role_assignment", [Action.UPDATE], {}, {}),
    ]
    items = classify("u", changes, rules)
    assert [i.tier for i in items] == [Tier.CRITICAL, Tier.HIGH, Tier.HIGH, Tier.NONE]
    assert [i.address for i in items][1:3] == ["azurerm_role_assignment.a", "databricks_grants.a"]


def test_principal_delta_other_types_and_unknown():
    assert principal_delta(_rc("random_pet", [Action.CREATE], None, {})) is None
    rc = _rc(
        "azurerm_role_assignment",
        [Action.CREATE],
        None,
        {"role_definition_name": "Reader"},
        after_unknown={"principal_id": True},
    )
    assert principal_delta(rc) is None


def test_databricks_grant_single_principal():
    rc = _rc(
        "databricks_grant",
        [Action.UPDATE],
        {"principal": "p", "privileges": ["SELECT", "MODIFY"]},
        {"principal": "p", "privileges": ["SELECT", "USE_SCHEMA"]},
    )
    assert principal_delta(rc) == PrincipalDelta(
        gained={"p": ("USE_SCHEMA",)}, lost={"p": ("MODIFY",)}
    )
