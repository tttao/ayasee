import json

from ayasee import cli
from ayasee.cascade import cascades


def test_direct_and_transitive_cascades():
    edges = {"grants": ["base"], "c": ["b"], "b": ["a"]}
    assert cascades(edges, {"base", "grants"}) == [("grants", "base")]
    # b itself does not change; c still reads outputs that depend on a
    assert cascades(edges, {"a", "c"}) == [("c", "a")]
    assert cascades(edges, {"grants"}) == []
    assert cascades(edges, set()) == []


def test_cycle_does_not_loop():
    assert cascades({"a": ["b"], "b": ["a"]}, {"a"}) == []


def _plans(tmp_path, **units):
    d = tmp_path / "json"
    d.mkdir()
    for name, actions in units.items():
        (d / f"{name}.json").write_text(
            json.dumps({"resource_changes": [{"change": {"actions": actions}}]})
        )
    return d


def test_cli_refuses_with_the_message(tmp_path, capsys):
    d = _plans(tmp_path, base=["create"], grants=["update"])
    edges = tmp_path / "e.json"
    edges.write_text(json.dumps({"grants": ["base"]}))
    argv = ["cascade", "check", "--edges", str(edges), "--plan-json", str(d)]
    assert cli.main(argv) == 1
    assert (
        "cascading change: grants depends on base, which also changes; land base first"
        in capsys.readouterr().err
    )


def test_cli_passes_when_the_upstream_is_unchanged(tmp_path):
    d = _plans(tmp_path, base=["no-op"], grants=["update"])
    edges = tmp_path / "e.json"
    edges.write_text(json.dumps({"grants": ["base"]}))
    assert cli.main(["cascade", "check", "--edges", str(edges), "--plan-json", str(d)]) == 0


def test_cli_fails_closed_on_unreadable_input(tmp_path):
    d = _plans(tmp_path, base=["create"])
    assert (
        cli.main(["cascade", "check", "--edges", str(tmp_path / "no"), "--plan-json", str(d)]) == 1
    )


def test_output_only_change_counts_as_changed_upstream_and_dependent(tmp_path):
    """R72: a unit whose only change is an output is changed, on either side of the edge."""
    from ayasee.cascade import changed_units

    d = tmp_path / "json"
    d.mkdir()
    out_only = {"resource_changes": [], "output_changes": {"id": {"actions": ["update"]}}}
    quiet = {
        "resource_changes": [{"change": {"actions": ["no-op"]}}],
        "output_changes": {"id": {"actions": ["no-op"]}},
    }
    (d / "base.json").write_text(json.dumps(out_only))
    (d / "grants.json").write_text(
        json.dumps({"resource_changes": [{"change": {"actions": ["update"]}}]})
    )
    (d / "quiet.json").write_text(json.dumps(quiet))
    assert changed_units(d) == {"base", "grants"}
    assert cascades({"grants": ["base"]}, changed_units(d)) == [("grants", "base")]
    # the dependent side: an output-only change downstream of a changed upstream
    (d / "base.json").write_text(
        json.dumps({"resource_changes": [{"change": {"actions": ["create"]}}]})
    )
    (d / "grants.json").write_text(json.dumps(out_only))
    assert cascades({"grants": ["base"]}, changed_units(d)) == [("grants", "base")]
