import json
from pathlib import Path

import pytest

from ayasee.planjson import Action, PlanFormatError, load_plan, normalize

FIXTURES = Path(__file__).parent / "fixtures" / "plans"


def test_replace_both_orders():
    assert normalize(["delete", "create"], False, None) == (Action.REPLACE,)
    assert normalize(["create", "delete"], False, None) == (Action.REPLACE,)


def test_import_with_update():
    assert normalize(["update"], True, None) == (Action.IMPORT, Action.UPDATE)


def test_import_noop():
    assert normalize(["no-op"], True, None) == (Action.IMPORT,)


def test_moved_noop():
    assert normalize(["no-op"], False, "a.old") == (Action.MOVE,)


def test_moved_with_update():
    assert normalize(["update"], False, "a.old") == (Action.MOVE, Action.UPDATE)


def test_plain_noop():
    assert normalize(["no-op"], False, None) == (Action.NOOP,)


def test_forget():
    assert normalize(["forget"], False, None) == (Action.FORGET,)


def test_unknown_action_raises():
    with pytest.raises(PlanFormatError):
        normalize(["explode"], False, None)


@pytest.mark.parametrize(
    ("name", "actions"),
    [
        ("create", (Action.CREATE,)),
        ("update", (Action.UPDATE,)),
        ("delete", (Action.DELETE,)),
        ("replace_delete_create", (Action.REPLACE,)),
        ("replace_create_delete", (Action.REPLACE,)),
        ("noop", (Action.NOOP,)),
        ("import", (Action.IMPORT,)),
        ("moved", (Action.MOVE,)),
        ("forget", (Action.FORGET,)),
    ],
)
def test_load_plan_actions(name, actions):
    (change,) = load_plan(FIXTURES / f"{name}.json")
    assert change.actions == actions
    assert change.provider == "registry.opentofu.org/databricks/databricks"
    assert change.mode == "managed"


def test_load_plan_keeps_data_sources_with_mode():
    changes = load_plan(FIXTURES / "read.json")
    assert len(changes) == 2
    by_mode = {c.mode: c for c in changes}
    assert by_mode["data"].actions == (Action.READ,)
    assert by_mode["managed"].actions == (Action.CREATE,)


def test_load_plan_moved_keeps_previous_address():
    (change,) = load_plan(FIXTURES / "moved.json")
    assert change.previous_address == "databricks_catalog.old"


def test_load_plan_update_fields():
    (change,) = load_plan(FIXTURES / "update.json")
    assert change.address == "databricks_catalog.main"
    assert change.type == "databricks_catalog"
    assert change.before == {"name": "main", "comment": "old"}
    assert change.after == {"name": "main", "comment": "new"}
    assert change.previous_address is None


def test_load_plan_create_has_no_before():
    (change,) = load_plan(FIXTURES / "create.json")
    assert change.before is None
    assert change.after == {"name": "main"}


def test_load_plan_sensitive_and_unknown_preserved():
    (change,) = load_plan(FIXTURES / "sensitive.json")
    assert change.after_sensitive == {"token": True}
    assert change.before_sensitive == {"token": True}
    (change,) = load_plan(FIXTURES / "unknown.json")
    assert change.after_unknown == {"id": True}


def test_unknown_format_version_raises():
    with pytest.raises(PlanFormatError):
        load_plan(FIXTURES / "bad_format_version.json")


def test_missing_format_version_raises(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"resource_changes": []}))
    with pytest.raises(PlanFormatError):
        load_plan(p)


def test_non_json_raises(tmp_path):
    p = tmp_path / "p.json"
    p.write_text("not json")
    with pytest.raises(PlanFormatError):
        load_plan(p)


def test_no_resource_changes_is_empty(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"format_version": "1.2"}))
    assert load_plan(p) == []


def _plan_with_change(tmp_path, change):
    doc = {
        "format_version": "1.2",
        "resource_changes": [
            {
                "address": "a.b",
                "mode": "managed",
                "type": "a",
                "provider_name": "p",
                "change": {"actions": ["update"], **change},
            }
        ],
    }
    path = tmp_path / "p.json"
    path.write_text(json.dumps(doc))
    return path


def test_values_not_in_repr(tmp_path):
    path = _plan_with_change(
        tmp_path,
        {
            "before": {"k": "old-VALUE"},
            "after": {"k": "new-VALUE"},
            "after_unknown": {"k": "u-VALUE"},
        },
    )
    text = repr(load_plan(path))
    assert "old-VALUE" not in text
    assert "new-VALUE" not in text
    assert "u-VALUE" not in text


def test_missing_sensitive_markers_fail_closed(tmp_path):
    path = _plan_with_change(tmp_path, {"before": {"k": 1}, "after": {"k": 2}})
    (rc,) = load_plan(path)
    assert rc.before_sensitive is True
    assert rc.after_sensitive is True


def test_present_sensitive_markers_kept(tmp_path):
    path = _plan_with_change(tmp_path, {"before_sensitive": {"k": True}, "after_sensitive": False})
    (rc,) = load_plan(path)
    assert rc.before_sensitive == {"k": True}
    assert rc.after_sensitive is False


# ---- R72: output changes ------------------------------------------------------------------


def test_output_changes_are_a_separate_list_without_values(tmp_path):
    from ayasee.planjson import OutputChange, load_output_changes

    p = tmp_path / "u.json"
    p.write_text(
        json.dumps(
            {
                "format_version": "1.2",
                "resource_changes": [],
                "output_changes": {
                    "b": {"actions": ["create"], "after": "SECRET-B", "after_sensitive": True},
                    "a": {"actions": ["delete", "create"], "before": "SECRET-A"},
                },
            }
        )
    )
    assert load_plan(p) == []
    outs = load_output_changes(p)
    assert outs == [OutputChange("a", (Action.REPLACE,)), OutputChange("b", (Action.CREATE,))]
    assert "SECRET" not in repr(outs)
    assert not hasattr(outs[0], "after") and not hasattr(outs[0], "before")


def test_missing_output_changes_is_empty(tmp_path):
    from ayasee.planjson import load_output_changes

    p = tmp_path / "u.json"
    p.write_text(json.dumps({"format_version": "1.2"}))
    assert load_output_changes(p) == []


@pytest.mark.parametrize("bad", [[], {"x": {"actions": "update"}}, {"x": None}])
def test_malformed_output_changes_fail(tmp_path, bad):
    from ayasee.planjson import load_output_changes

    p = tmp_path / "u.json"
    p.write_text(json.dumps({"format_version": "1.2", "output_changes": bad}))
    with pytest.raises(PlanFormatError):
        load_output_changes(p)
