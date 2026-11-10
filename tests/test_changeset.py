import json
import shutil
from pathlib import Path

from ayasee import changeset, cli
from ayasee.changeset import build_changeset, render_summary
from ayasee.planjson import Action, OutputChange, load_output_changes, load_plan
from ayasee.risk import PrincipalDelta, RiskItem, Tier, classify, load_rules


def item(addr, tier, actions=(Action.UPDATE,), delta=None, unit="u"):
    rules = ("r1",) if tier is not Tier.NONE else ()
    return RiskItem(unit, addr, tuple(actions), tier, rules, delta)


def sample():
    return [
        item("a.n1", Tier.NONE),
        item("a.h1", Tier.HIGH, delta=PrincipalDelta({"g": ("USE",)}, {"p": ("MODIFY",)})),
        item("a.n2", Tier.NONE, (Action.CREATE,)),
        item("a.c1", Tier.CRITICAL, (Action.REPLACE,)),
        item("a.h2", Tier.HIGH, (Action.MOVE, Action.UPDATE)),
        item("a.n3", Tier.NONE, (Action.NOOP,)),
        item("a.n4", Tier.NONE, (Action.DELETE,)),
        item("a.n5", Tier.NONE, (Action.IMPORT,)),
    ]


def test_counts_and_order():
    cs = build_changeset("abc", "prod", sample())
    assert cs["schema"] == "ayasee.changeset/v1"
    assert cs["items"][0]["tier"] == "critical"
    assert [i["tier"] for i in cs["items"]][:3] == ["critical", "high", "high"]
    assert cs["risk"] == {"critical": 1, "high": 2, "medium": 0}
    assert cs["counts"] == {
        "create": 1,
        "update": 3,
        "delete": 1,
        "replace": 1,
        "import": 1,
        "move": 1,
        "forget": 0,
    }
    assert len(cs["items"]) == 7  # the pure no-op is not a change
    high = next(i for i in cs["items"] if i["address"] == "a.h1")
    assert high["principals"] == {"gained": {"g": ["USE"]}, "lost": {"p": ["MODIFY"]}}
    assert cs["items"][0]["principals"] is None


def test_summary_header():
    items = [item("c", Tier.CRITICAL)] + [item(f"h{i}", Tier.HIGH) for i in range(2)]
    items += [item(f"n{i}", Tier.NONE) for i in range(3)]
    first = render_summary(build_changeset("s", "prod", items)).splitlines()[0]
    assert first == "### Prod gate: 1 critical · 2 high · 0 medium · 6 changes"


def test_summary_sections_and_details():
    text = render_summary(build_changeset("s", "prod", sample()))
    assert text.index("#### Critical") < text.index("#### High") < text.index("<details>")
    assert "lost: `p` MODIFY" in text
    assert "a.n2" in text.split("<details>")[1]


def _classified(d):
    rules = load_rules(RULES)
    items = []
    for plan in sorted(d.glob("*.json")):
        items += classify(plan.stem, load_plan(plan), rules)
    return items


LEAK_SECRETS = (
    "s3cr3t",
    "plain-VALUE",
    "old-plain",
    "unknown-VALUE",
    "old-out-VALUE",
    "plain-out-VALUE",
)


def _leaky_plan(tmp_path):
    doc = {
        "format_version": "1.2",
        "resource_changes": [
            {
                "address": "databricks_token.t",
                "mode": "managed",
                "type": "databricks_token",
                "provider_name": "registry.opentofu.org/databricks/databricks",
                "change": {
                    "actions": ["update"],
                    "before": {"token": "old-s3cr3t", "comment": "old-plain"},
                    "after": {"token": "s3cr3t-VALUE", "comment": "plain-VALUE", "u": None},
                    "after_unknown": {"u": True, "x": "unknown-VALUE"},
                    "before_sensitive": {"token": True},
                    "after_sensitive": {"token": True},
                },
            }
        ],
        # R72: output values, sensitive or not, must not leak either
        "output_changes": {
            "conn": {
                "actions": ["update"],
                "before": "old-out-VALUE",
                "after": "out-s3cr3t",
                "after_unknown": False,
                "before_sensitive": True,
                "after_sensitive": True,
            },
            "url": {
                "actions": ["create"],
                "before": None,
                "after": {"host": "plain-out-VALUE"},
                "after_unknown": False,
                "before_sensitive": False,
                "after_sensitive": False,
            },
        },
    }
    d = tmp_path / "plans"
    d.mkdir()
    (d / "core.json").write_text(json.dumps(doc))
    return d


def test_no_values_leak(tmp_path, monkeypatch):
    d = _leaky_plan(tmp_path)
    out, summary = tmp_path / "cs.json", tmp_path / "summary.md"
    summary.write_text("PRE\n")
    monkeypatch.setattr(cli, "_COMMANDS", {"changeset": changeset._setup})
    code = cli.main(
        [
            "changeset",
            "--plan-json",
            str(d),
            "--sha",
            "abc",
            "--env",
            "prod",
            "--out",
            str(out),
            "--summary",
            str(summary),
        ]
    )
    assert code == 0
    outputs = [("core", oc) for oc in load_output_changes(d / "core.json")]
    blobs = [
        out.read_text(),
        summary.read_text(),
        json.dumps(build_changeset("abc", "prod", _classified(d), outputs)),
        repr(load_plan(d / "core.json")),
        repr(load_output_changes(d / "core.json")),
    ]
    for blob in blobs:
        for secret in LEAK_SECRETS:
            assert secret not in blob
    assert "output.conn" in out.read_text() and "output.url" in out.read_text()
    assert summary.read_text().startswith("PRE\n")  # appended, not overwritten
    assert json.loads(out.read_text())["items"][0]["address"] == "databricks_token.t"


RULES = Path(__file__).parents[1] / "rules" / "databricks-azure.yaml"
FIX = Path(__file__).parent / "fixtures" / "plans"


def _run_cli(monkeypatch, plan_dir, out):
    monkeypatch.setattr(cli, "_COMMANDS", {"changeset": changeset._setup})
    return cli.main(
        [
            "changeset",
            "--plan-json",
            str(plan_dir),
            "--sha",
            "s",
            "--env",
            "prod",
            "--out",
            str(out),
        ]
    )


def test_missing_dir_fails(tmp_path, monkeypatch, capsys):
    out = tmp_path / "cs.json"
    assert _run_cli(monkeypatch, tmp_path / "nope", out) != 0
    assert "no <unit>.json plans" in capsys.readouterr().err
    assert not out.exists()


def test_empty_dir_fails(tmp_path, monkeypatch):
    (tmp_path / "plans").mkdir()
    out = tmp_path / "cs.json"
    assert _run_cli(monkeypatch, tmp_path / "plans", out) != 0
    assert not out.exists()


def test_malformed_plan_fails_cleanly(tmp_path, monkeypatch, capsys):
    d = tmp_path / "plans"
    d.mkdir()
    (d / "u.json").write_text("{not json")
    out = tmp_path / "cs.json"
    assert _run_cli(monkeypatch, d, out) != 0
    assert "changeset failed" in capsys.readouterr().err
    assert not out.exists()


def test_empty_resource_changes_is_legit(tmp_path, monkeypatch):
    d = tmp_path / "plans"
    d.mkdir()
    (d / "u.json").write_text('{"format_version": "1.2", "resource_changes": []}')
    out = tmp_path / "cs.json"
    assert _run_cli(monkeypatch, d, out) == 0
    cs = json.loads(out.read_text())
    assert cs["items"] == []
    assert render_summary(cs).splitlines()[0].endswith("0 changes")


def test_sensitive_grant_block_emits_null_principals(tmp_path, monkeypatch):
    d = tmp_path / "plans"
    d.mkdir()
    shutil.copy(FIX / "grants_sensitive.json", d / "u.json")
    out = tmp_path / "cs.json"
    assert _run_cli(monkeypatch, d, out) == 0
    text = out.read_text()
    (it,) = json.loads(text)["items"]
    assert it["principals"] is None
    assert "analysts" not in text
    assert "data_engineers" not in text


def test_summary_neutralises_markup_in_addresses_and_principals():
    evil = "a.b`\n<img src=x>[l](http://e)`"
    delta = PrincipalDelta({"g`\nx": ("USE",)}, {})
    text = render_summary(build_changeset("s", "dev", [item(evil, Tier.HIGH, delta=delta)]))
    assert "`\n" not in text and "<img" not in text
    assert "&lt;img src=x&gt;" in text
    body = [line for line in text.splitlines() if line.startswith("- ")]
    assert len(body) == 1 and body[0].count("`") == 4
    assert "gained: `g' x`" in text


# ---- R72: output-only changes ------------------------------------------------------------


def _output_only_plan(d, actions=("update",)):
    d.mkdir(exist_ok=True)
    (d / "u.json").write_text(
        json.dumps(
            {
                "format_version": "1.2",
                "resource_changes": [
                    {
                        "address": "terraform_data.x",
                        "mode": "managed",
                        "type": "terraform_data",
                        "provider_name": "terraform.io/builtin/terraform",
                        "change": {"actions": ["no-op"], "before": {}, "after": {}},
                    }
                ],
                "output_changes": {
                    "id": {"actions": list(actions), "before": "a", "after": "b"},
                    "same": {"actions": ["no-op"], "before": "c", "after": "c"},
                },
            }
        )
    )
    return d


def test_output_only_change_is_an_item_counted_in_the_total(tmp_path, monkeypatch):
    d = _output_only_plan(tmp_path / "plans")
    out = tmp_path / "cs.json"
    assert _run_cli(monkeypatch, d, out) == 0
    cs = json.loads(out.read_text())
    assert cs["items"] == [
        {
            "unit": "u",
            "kind": "output",
            "address": "output.id",
            "actions": ["update"],
            "tier": "none",
            "rules": [],
            "principals": None,
        }
    ]
    # resource counts stay resource counts; the output still makes the total non-zero
    assert set(cs["counts"].values()) == {0}
    assert cs["risk"] == {"critical": 0, "high": 0, "medium": 0}
    assert render_summary(cs).splitlines()[0].endswith("· 1 changes")
    assert "`output.id` (update)" in render_summary(cs)


def test_no_op_outputs_are_dropped():
    cs = build_changeset("s", "prod", [], [("u", OutputChange("x", (Action.NOOP,)))])
    assert cs["items"] == []


def test_resource_items_carry_kind_resource():
    cs = build_changeset("s", "prod", [item("a.b", Tier.NONE)])
    assert cs["items"][0]["kind"] == "resource"
