import json
from pathlib import Path

import pytest

from ayasee import binding, cli
from ayasee.binding import BindingError, Manifest, make_manifest, verify, verify_replan


def plan_doc(comment="hello", secret="s3cret", actions=("update",), extra=None):
    after = {"comment": comment, "password": secret, "nested": {"tok": secret, "ok": 1}}
    return {
        "format_version": "1.2",
        "resource_changes": [
            {
                "address": "databricks_schema.a",
                "type": "databricks_schema",
                "provider_name": "x/databricks",
                "mode": "managed",
                "change": {
                    "actions": list(actions),
                    "before": None,
                    "after": after,
                    "after_unknown": {"id": True},
                    "before_sensitive": {},
                    "after_sensitive": {"password": True, "nested": {"tok": True}},
                },
            },
            {
                "address": "data.x.y",
                "type": "x",
                "provider_name": "x/databricks",
                "mode": "data",
                "change": {
                    "actions": ["read"],
                    "after": {"v": comment},
                    "after_unknown": {},
                    "after_sensitive": {},
                    "before_sensitive": {},
                },
            },
        ]
        + (extra or []),
    }


def write_json(path: Path, doc) -> Path:
    path.write_text(json.dumps(doc))
    return path


@pytest.fixture
def env(tmp_path):
    d = {k: tmp_path / k for k in ("plans", "locks", "pj")}
    for p in d.values():
        p.mkdir()
    for u in ("a", "b"):
        (d["plans"] / f"{u}.tfplan").write_bytes(b"plan-" + u.encode())
        (d["locks"] / f"{u}.lock.hcl").write_text(f"lock {u}")
        write_json(d["pj"] / f"{u}.json", plan_doc())
    return d


def files(d, suffix):
    return {p.name[: -len(suffix)]: p for p in sorted(d.glob(f"*{suffix}"))}


RP = {"sha": "sha1", "engine_version": "1.10.2"}  # the replan inputs matching build()


def build(env, with_json=False):
    return make_manifest(
        "sha1",
        "prod",
        "opentofu",
        "1.10.2",
        files(env["plans"], ".tfplan"),
        files(env["locks"], ".lock.hcl"),
        files(env["pj"], ".json") if with_json else None,
    )


def check(env, m, **over):
    kw = dict(
        sha="sha1",
        engine_version="1.10.2",
        plan_files=files(env["plans"], ".tfplan"),
        lock_files=files(env["locks"], ".lock.hcl"),
    )
    kw.update(over)
    verify(m, **kw)


def reason(env, m, **over):
    with pytest.raises(BindingError) as e:
        check(env, m, **over)
    return e.value


def test_roundtrip_ok(env):
    m = build(env, with_json=True)
    assert Manifest.from_json(m.to_json()) == m
    assert json.loads(m.to_json())["schema"] == "ayasee.manifest/v1"
    check(env, m)


def test_from_json_rejects_other_schema(env):
    doc = json.loads(build(env).to_json())
    doc["schema"] = "ayasee.manifest/v2"
    with pytest.raises(ValueError, match="schema"):
        Manifest.from_json(json.dumps(doc))


def test_plan_byte_change_fails(env):
    m = build(env)
    (env["plans"] / "b.tfplan").write_bytes(b"plan-c")
    e = reason(env, m)
    assert (e.reason, e.unit) == ("plan_hash", "b")


def test_engine_drift_fails(env):
    assert reason(env, build(env), engine_version="1.10.3").reason == "engine"


def test_sha_mismatch_fails(env):
    assert reason(env, build(env), sha="other").reason == "sha"


def test_lock_change_fails(env):
    m = build(env)
    (env["locks"] / "a.lock.hcl").write_text("changed")
    e = reason(env, m)
    assert (e.reason, e.unit) == ("lock_hash", "a")


def test_missing_unit_fails(env):
    m = build(env)
    (env["plans"] / "b.tfplan").unlink()
    assert reason(env, m).reason == "unit_set"


def test_extra_unit_fails(env):
    m = build(env)
    (env["locks"] / "c.lock.hcl").write_text("x")
    assert reason(env, m).reason == "unit_set"


def replan(env, **kw):
    d = env["pj"]
    for u in ("a", "b"):
        write_json(d / f"{u}.json", plan_doc(**kw))
    return files(d, ".json")


def test_replan_same_ok(env):
    verify_replan(build(env, True), replan(env), **RP)


def test_replan_value_change_fails(env):
    m = build(env, True)
    with pytest.raises(BindingError) as e:
        verify_replan(m, replan(env, comment="different"), **RP)
    assert (e.value.reason, e.value.unit) == ("values", "a")


def test_replan_action_change_fails(env):
    m = build(env, True)
    with pytest.raises(BindingError) as e:
        verify_replan(m, replan(env, actions=("delete", "create")), **RP)
    assert (e.value.reason, e.value.unit) == ("values", "a")


def test_replan_sensitive_change_ignored(env):
    m = build(env, True)
    verify_replan(m, replan(env, secret="rotated"), **RP)


def test_replan_unit_set_mismatch(env):
    m = build(env, True)
    r = replan(env)
    del r["b"]
    with pytest.raises(BindingError) as e:
        verify_replan(m, r, **RP)
    assert e.value.reason == "unit_set"


def test_replan_without_values_hash_fails(env):
    with pytest.raises(BindingError) as e:
        verify_replan(build(env, False), replan(env), **RP)
    assert e.value.reason == "values"


def one(after, sens=None, unk=None, actions=("update",)):
    doc = {
        "format_version": "1.2",
        "resource_changes": [
            {
                "address": "r.x",
                "type": "r",
                "provider_name": "p",
                "mode": "managed",
                "change": {
                    "actions": list(actions),
                    "after": after,
                    "after_sensitive": sens if sens is not None else {},
                    "after_unknown": unk if unk is not None else {},
                    "before_sensitive": {},
                },
            }
        ],
    }
    return doc


def vsha(tmp_path, *args, **kw):
    p = write_json(tmp_path / "v.json", one(*args, **kw))
    return binding._values_sha(binding.load_plan(p))


def test_list_reorder_with_secret_differs(tmp_path):
    a = vsha(tmp_path, {"l": ["x", "S"]}, {"l": [False, True]})
    b = vsha(tmp_path, {"l": ["S", "x"]}, {"l": [True, False]})
    assert a != b


def test_unknown_element_insertion_differs(tmp_path):
    a = vsha(tmp_path, {"l": ["a", "b"]})
    b = vsha(tmp_path, {"l": ["a", None, "b"]}, unk={"l": [False, True, False]})
    assert a != b


def test_sensitive_key_present_vs_absent_differs(tmp_path):
    a = vsha(tmp_path, {"k": 1, "pw": "s"}, {"pw": True})
    b = vsha(tmp_path, {"k": 1})
    assert a != b


def test_redacted_vs_real_null_differs(tmp_path):
    a = vsha(tmp_path, {"k": "s"}, {"k": True})
    b = vsha(tmp_path, {"k": None})
    assert a != b


def test_secret_rotation_keeps_hash(tmp_path):
    a = vsha(tmp_path, {"k": 1, "l": ["x", "old"]}, {"l": [False, True]})
    b = vsha(tmp_path, {"k": 1, "l": ["x", "new"]}, {"l": [False, True]})
    assert a == b


def test_unknown_path_known_value_ignored(tmp_path):
    a = vsha(tmp_path, {"k": 1, "n": "one"}, unk={"n": True})
    b = vsha(tmp_path, {"k": 1, "n": "two"}, unk={"n": True})
    assert a == b


def test_sensitive_true_drops_all_after(env):
    doc = plan_doc()
    doc["resource_changes"][0]["change"]["after_sensitive"] = True
    p = write_json(env["pj"] / "a.json", doc)
    doc2 = plan_doc(comment="zzz")
    doc2["resource_changes"][0]["change"]["after_sensitive"] = True
    q = write_json(env["pj"] / "q.json", doc2)
    assert binding._values_sha(binding.load_plan(p)) == binding._values_sha(binding.load_plan(q))


def test_noop_and_data_changes_ignored(env):
    m = build(env, True)
    doc = plan_doc()
    doc["resource_changes"][1]["change"]["after"] = {"v": "other"}
    doc["resource_changes"].append(
        {
            "address": "x.n",
            "type": "x",
            "provider_name": "p",
            "mode": "managed",
            "change": {"actions": ["no-op"], "after": {"q": 1}, "after_unknown": {}},
        }
    )
    for u in ("a", "b"):
        write_json(env["pj"] / f"{u}.json", doc)
    verify_replan(m, files(env["pj"], ".json"), **RP)


# --- CLI ---


def run(*argv):
    return cli.main(["binding", *map(str, argv)])


RP_ARGS = ("--sha", "sha1", "--engine-version", "1.10.2")


def cli_make(env, tmp_path, with_json=True):
    out = tmp_path / "m.json"
    args = ["make", "--sha", "sha1", "--env", "prod", "--engine", "opentofu",
            "--engine-version", "1.10.2", "--plans", env["plans"], "--locks", env["locks"],
            "--out", out]  # fmt: skip
    if with_json:
        args += ["--plan-json", env["pj"]]
    assert run(*args) == 0
    return out


def cli_verify(env, out, sha="sha1", ev="1.10.2"):
    return run("verify", "--manifest", out, "--sha", sha, "--engine-version", ev,
               "--plans", env["plans"], "--locks", env["locks"])  # fmt: skip


def test_cli_make_verify_ok(env, tmp_path):
    out = cli_make(env, tmp_path)
    assert cli_verify(env, out) == 0
    assert run("verify-replan", "--manifest", out, *RP_ARGS, "--plan-json", env["pj"]) == 0


def test_cli_verify_mismatch_exit_3(env, tmp_path, capsys):
    out = cli_make(env, tmp_path)
    (env["plans"] / "a.tfplan").write_bytes(b"x")
    assert cli_verify(env, out) == 3
    assert capsys.readouterr().err == "binding: plan_hash unit=a\n"
    assert cli_verify(env, out, ev="9") == 3
    assert capsys.readouterr().err == "binding: engine\n"


def test_cli_replan_mismatch_exit_3(env, tmp_path, capsys):
    out = cli_make(env, tmp_path)
    replan(env, comment="changed")
    assert run("verify-replan", "--manifest", out, *RP_ARGS, "--plan-json", env["pj"]) == 3
    err = capsys.readouterr().err
    assert err == "binding: values unit=a\n"
    assert "changed" not in err


def test_cli_missing_dir_exit_1(env, tmp_path, capsys):
    out = cli_make(env, tmp_path)
    env["plans"] = tmp_path / "nope"
    assert cli_verify(env, out) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_cli_empty_dir_exit_1(env, tmp_path):
    out = cli_make(env, tmp_path)
    (env["locks"] / "a.lock.hcl").unlink()
    (env["locks"] / "b.lock.hcl").unlink()
    assert cli_verify(env, out) == 1


def test_cli_unexpected_error_exit_1(env, tmp_path, capsys, monkeypatch):
    out = cli_make(env, tmp_path)

    def boom(*a, **k):
        raise RecursionError("s3cret")

    monkeypatch.setattr(binding, "load_plan", boom)
    assert run("verify-replan", "--manifest", out, *RP_ARGS, "--plan-json", env["pj"]) == 1
    err = capsys.readouterr().err
    assert "s3cret" not in err and "Traceback" not in err


def test_empty_unit_sets_rejected(env):
    with pytest.raises(ValueError):
        make_manifest("s", "prod", "opentofu", "1", {}, {})
    m = build(env)
    with pytest.raises(ValueError):
        check(env, m, plan_files={}, lock_files={})
    with pytest.raises(ValueError):
        verify_replan(m, {}, **RP)


def test_cli_bad_manifest_exit_1(env, tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema": "other"}')
    assert cli_verify(env, bad) == 1
    assert (
        run("verify-replan", "--manifest", tmp_path / "missing", *RP_ARGS, "--plan-json", env["pj"])
        == 1
    )
    assert "Traceback" not in capsys.readouterr().err


def test_cli_bad_plan_json_exit_1_no_values(env, tmp_path, capsys):
    out = cli_make(env, tmp_path)
    (env["pj"] / "a.json").write_text(
        '{"format_version": "1.2", "resource_changes": [{"x": "s3cret"}]}'
    )
    assert run("verify-replan", "--manifest", out, *RP_ARGS, "--plan-json", env["pj"]) == 1
    assert "s3cret" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("sha", "engine_version", "why"), [("sha2", "1.10.2", "sha"), ("sha1", "1.11.0", "engine")]
)
def test_replan_checks_sha_and_engine_first(env, sha, engine_version, why):
    """M4: a re-plan of another commit or with another engine never matches."""
    with pytest.raises(BindingError) as e:
        verify_replan(build(env, True), replan(env), sha=sha, engine_version=engine_version)
    assert e.value.reason == why


def test_cli_replan_sha_and_engine_mismatch_exit_3(env, tmp_path, capsys):
    out = cli_make(env, tmp_path)
    base = ("verify-replan", "--manifest", out, "--plan-json", env["pj"])
    assert run(*base, "--sha", "other", "--engine-version", "1.10.2") == 3
    assert capsys.readouterr().err == "binding: sha\n"
    assert run(*base, "--sha", "sha1", "--engine-version", "9.9.9") == 3
    assert capsys.readouterr().err == "binding: engine\n"
