import json

import httpx
import pytest
import respx

from ayasee import cli
from ayasee.drift import DriftError, DriftResult, evaluate, sync_issue
from ayasee.github import GitHubError
from ayasee.planjson import Action, ResourceChange

BASE = "https://ghe.example/api/v3"
RUN = "https://x/runs/1"
SHA = "a1b2c3d4e5f6a7b8c9d0"


def rc(address, type_, actions, before=None, after=None, mode="managed"):
    return ResourceChange(
        address=address,
        type=type_,
        provider="p",
        mode=mode,
        actions=tuple(actions),
        before=before,
        after=after,
        after_unknown={},
        before_sensitive={},
        after_sensitive={},
        previous_address=None,
    )


def perms(before, after, address="databricks_permissions.a"):
    return rc(
        address,
        "databricks_permissions",
        [Action.UPDATE],
        {"access_control": before, "object_id": "x"},
        {"access_control": after, "object_id": "x"},
    )


NOISE = {"databricks_permissions": ["access_control"]}


def test_exit_0_no_drift():
    res = evaluate("dev", 0, [rc("a.b", "t", [Action.CREATE])], {})
    assert res == DriftResult(drifted=False, addresses=())


@pytest.mark.parametrize("code", [1, 3, -1, 127])
def test_error_exit_codes_raise(code):
    with pytest.raises(DriftError):
        evaluate("dev", code, [], {})


def test_noise_only_is_no_drift():
    change = perms([{"permission_level": "CAN_USE"}], [{"permission_level": "CAN_MANAGE"}])
    assert evaluate("dev", 2, [change], NOISE) == DriftResult(False, ())


def test_nested_noise_path_covers_children_but_not_siblings():
    nested = {"databricks_permissions": ["access_control.0.permission_level"]}
    change = perms(
        [{"permission_level": "A", "user": "u"}], [{"permission_level": "B", "user": "u"}]
    )
    assert not evaluate("dev", 2, [change], nested).drifted
    other = perms(
        [{"permission_level": "A", "user": "u"}], [{"permission_level": "A", "user": "v"}]
    )
    assert evaluate("dev", 2, [other], nested).drifted


def test_noise_is_per_type_and_needs_a_path_boundary():
    change = perms([{"p": 1}], [{"p": 2}])
    assert evaluate("dev", 2, [change], {"other_type": ["access_control"]}).drifted
    assert evaluate("dev", 2, [change], {"databricks_permissions": ["access"]}).drifted


def test_update_with_a_non_noise_attribute_is_drift():
    change = rc(
        "databricks_permissions.a",
        "databricks_permissions",
        [Action.UPDATE],
        {"access_control": [], "object_id": "x"},
        {"access_control": [1], "object_id": "y"},
    )
    assert evaluate("dev", 2, [change], NOISE).addresses == ("databricks_permissions.a",)


def test_update_without_provable_paths_is_drift():
    change = rc("a.b", "databricks_permissions", [Action.UPDATE], {"a": 1}, {"a": 1})
    assert evaluate("dev", 2, [change], NOISE).drifted
    assert evaluate("dev", 2, [rc("a.c", "databricks_permissions", [Action.UPDATE])], NOISE).drifted


@pytest.mark.parametrize(
    "actions",
    [[Action.CREATE], [Action.DELETE], [Action.REPLACE], [Action.IMPORT], [Action.FORGET]],
)
def test_create_delete_replace_import_forget_are_never_noise(actions):
    change = rc(
        "databricks_permissions.a",
        "databricks_permissions",
        actions,
        {"access_control": []},
        {"access_control": [1]},
    )
    assert evaluate("dev", 2, [change], NOISE).drifted


def test_ignores_noop_read_and_data_and_sorts_addresses():
    changes = [
        rc("z.z", "t", [Action.DELETE]),
        rc("a.a", "t", [Action.NOOP]),
        rc("d.d", "t", [Action.READ]),
        rc("data.x", "t", [Action.CREATE], mode="data"),
        rc("b.b", "t", [Action.CREATE]),
    ]
    assert evaluate("dev", 2, changes, {}) == DriftResult(True, ("b.b", "z.z"))


class FakeGH:
    def __init__(self, open_issues=()):
        self.issues = {i["number"]: dict(i) for i in open_issues}
        self.calls = []
        self.next = 100

    def list_issues(self, repo, labels, state="open"):
        self.calls.append(("list", tuple(labels)))
        return [
            i
            for i in self.issues.values()
            if set(labels) <= set(i["labels"]) and i["state"] == state
        ]

    def create_issue(self, repo, title, body, labels):
        self.next += 1
        self.issues[self.next] = {
            "number": self.next,
            "title": title,
            "body": body,
            "labels": labels,
            "state": "open",
        }
        self.calls.append(("create", self.next))
        return self.next

    def create_issue_comment(self, repo, number, body):
        self.calls.append(("comment", number, body))
        return 1

    def update_issue(self, repo, number, state=None, labels=None):
        self.calls.append(("update", number, state, labels))
        if state:
            self.issues[number]["state"] = state
        if labels is not None:
            self.issues[number]["labels"] = labels


DRIFTED = DriftResult(True, ("a.b", "c.d"))


def test_sync_opens_issue_with_addresses_and_run_link():
    gh = FakeGH()
    n = sync_issue(gh, "o/n", "dev", DRIFTED, RUN, sha=SHA)
    issue = gh.issues[n]
    assert issue["title"] == "drift: dev"
    assert issue["labels"] == ["ayasee:drift", "env:dev"]
    assert "a.b" in issue["body"] and "c.d" in issue["body"] and RUN in issue["body"]


def test_prod_drift_adds_stop_the_line_label():
    gh = FakeGH()
    n = sync_issue(gh, "o/n", "prod", DRIFTED, RUN)
    assert gh.issues[n]["labels"] == ["ayasee:drift", "env:prod", "ayasee:stop-the-line"]


def test_second_night_comments_instead_of_opening():
    gh = FakeGH()
    first = sync_issue(gh, "o/n", "dev", DRIFTED, RUN)
    second = sync_issue(gh, "o/n", "dev", DRIFTED, RUN)
    assert first == second and len(gh.issues) == 1
    assert [c[0] for c in gh.calls if c[0] in ("create", "comment")] == ["create", "comment"]


def test_cleared_closes_issue_with_comment():
    gh = FakeGH([{"number": 7, "labels": ["ayasee:drift", "env:dev"], "state": "open"}])
    assert sync_issue(gh, "o/n", "dev", DriftResult(False, ()), RUN, sha=SHA) is None
    assert ("comment", 7, "drift cleared at a1b2c3d4e5f6") in gh.calls
    assert gh.issues[7]["state"] == "closed"


def test_cleared_leaves_other_envs_alone_and_is_quiet_without_issue():
    gh = FakeGH([{"number": 7, "labels": ["ayasee:drift", "env:uat"], "state": "open"}])
    assert sync_issue(gh, "o/n", "dev", DriftResult(False, ()), RUN, sha=SHA) is None
    assert gh.issues[7]["state"] == "open"


def _cli_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)


def test_cli_evaluate_aggregates_units(tmp_path, capsys, monkeypatch):
    plans = tmp_path / "json"
    plans.mkdir()
    doc = lambda rcs: json.dumps({"format_version": "1.2", "resource_changes": rcs})  # noqa: E731
    change = {
        "address": "r.x",
        "type": "r",
        "provider_name": "p",
        "mode": "managed",
        "change": {"actions": ["create"], "before": None, "after": {"v": "SECRET"}},
    }
    (plans / "a.json").write_text(doc([]))
    (plans / "b.json").write_text(doc([change]))
    codes = tmp_path / "codes.json"
    codes.write_text(json.dumps({"a": 0, "b": 2}))
    out = tmp_path / "result.json"
    argv = ["drift", "evaluate", "--plan-json", str(plans), "--exit-codes", str(codes)]
    argv += ["--env", "dev", "--config", str(tmp_path / "none.yaml"), "--out", str(out)]
    assert cli.main(argv) == 0
    res = json.loads(out.read_text())
    assert res == {"env": "dev", "drifted": True, "addresses": ["b: r.x"]}
    assert "SECRET" not in out.read_text()
    codes.write_text(json.dumps({"a": 0, "b": 1}))
    assert cli.main(argv) == 1
    assert "exit code 1" in capsys.readouterr().err


def test_cli_evaluate_missing_exit_code_fails(tmp_path, capsys):
    plans = tmp_path / "json"
    plans.mkdir()
    (plans / "a.json").write_text('{"format_version": "1.2"}')
    codes = tmp_path / "codes.json"
    codes.write_text("{}")
    argv = ["drift", "evaluate", "--plan-json", str(plans), "--exit-codes", str(codes)]
    assert cli.main([*argv, "--env", "dev", "--out", str(tmp_path / "o.json")]) == 1


@respx.mock
def test_cli_sync_opens_and_writes_change_output(tmp_path, monkeypatch, capsys):
    _cli_env(monkeypatch)
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    res = tmp_path / "result.json"
    res.write_text(json.dumps({"env": "prod", "drifted": True, "addresses": ["a.b"]}))
    respx.get(f"{BASE}/repos/o/n/issues").mock(return_value=httpx.Response(200, json=[]))
    create = respx.post(f"{BASE}/repos/o/n/issues").mock(
        return_value=httpx.Response(201, json={"number": 9})
    )
    argv = ["drift", "sync", "--repo", "o/n", "--env", "prod", "--result", str(res)]
    assert cli.main([*argv, "--sha", "a" * 40, "--run-url", RUN]) == 0
    body = json.loads(create.calls[0].request.content)
    assert "ayasee:stop-the-line" in body["labels"]
    assert "change=opened" in out.read_text()


def test_prod_comment_path_restores_a_missing_stop_the_line_label():
    gh = FakeGH([{"number": 7, "labels": ["ayasee:drift", "env:prod"], "state": "open"}])
    assert sync_issue(gh, "o/n", "prod", DRIFTED, RUN) == 7
    assert gh.issues[7]["labels"] == ["ayasee:drift", "env:prod", "ayasee:stop-the-line"]
    gh.calls.clear()
    sync_issue(gh, "o/n", "prod", DRIFTED, RUN)
    assert not [c for c in gh.calls if c[0] == "update"]  # already labelled: no second update


def test_non_prod_comment_path_never_adds_the_label():
    gh = FakeGH([{"number": 7, "labels": ["ayasee:drift", "env:dev"], "state": "open"}])
    sync_issue(gh, "o/n", "dev", DRIFTED, RUN)
    assert not [c for c in gh.calls if c[0] == "update"]


@pytest.mark.parametrize("number", [None, "7", True])
def test_issue_number_must_be_an_int(number):
    gh = FakeGH([{"number": number, "labels": ["ayasee:drift", "env:dev"], "state": "open"}])
    with pytest.raises(GitHubError):
        sync_issue(gh, "o/n", "dev", DRIFTED, RUN)
    with pytest.raises(GitHubError):
        sync_issue(gh, "o/n", "dev", DriftResult(False, ()), RUN, sha=SHA)


def test_after_unknown_paths_count_as_changed_paths():
    def with_unknown(unknown):
        change = perms([{"p": 1}], [{"p": 1}])
        return ResourceChange(**{**change.__dict__, "after_unknown": unknown})

    # nothing differs in before/after, so only the unknown path can prove noise
    assert not evaluate("dev", 2, [with_unknown({"access_control": [{"p": True}]})], NOISE).drifted
    assert evaluate("dev", 2, [with_unknown({"object_id": True})], NOISE).drifted


def test_move_with_update_is_drift_even_under_a_noise_path():
    change = perms([{"p": 1}], [{"p": 2}])
    moved = ResourceChange(**{**change.__dict__, "actions": (Action.MOVE, Action.UPDATE)})
    assert evaluate("dev", 2, [moved], NOISE).drifted


@pytest.mark.parametrize(
    "doc",
    [
        {"env": "dev", "addresses": []},
        {"env": "dev", "drifted": "false", "addresses": []},
        {"env": "dev", "drifted": 0, "addresses": []},
        {"env": "dev", "drifted": True},
        {"env": "dev", "drifted": True, "addresses": "a.b"},
        {"env": "dev", "drifted": True, "addresses": [1]},
    ],
)
def test_cli_sync_rejects_malformed_results(tmp_path, monkeypatch, capsys, doc):
    _cli_env(monkeypatch)
    res = tmp_path / "result.json"
    res.write_text(json.dumps(doc))
    argv = ["drift", "sync", "--repo", "o/n", "--env", "dev", "--result", str(res)]
    assert cli.main([*argv, "--sha", "a" * 40, "--run-url", RUN]) == 1
    assert "drift: error" in capsys.readouterr().err


# ---- R72: output changes are drift --------------------------------------------------------


def test_output_change_is_drift():
    from ayasee.planjson import OutputChange

    res = evaluate("dev", 2, [], {}, [OutputChange("id", (Action.UPDATE,))])
    assert res == DriftResult(True, ("output.id",))
    quiet = evaluate("dev", 2, [], {}, [OutputChange("id", (Action.NOOP,))])
    assert quiet == DriftResult(False, ())


def test_cli_evaluate_reports_output_only_drift_without_values(tmp_path, monkeypatch):
    plans = tmp_path / "json"
    plans.mkdir()
    (plans / "u.json").write_text(
        json.dumps(
            {
                "format_version": "1.2",
                "resource_changes": [],
                "output_changes": {
                    "conn": {"actions": ["update"], "before": "OLD-SECRET", "after": "NEW-SECRET"}
                },
            }
        )
    )
    codes = tmp_path / "codes.json"
    codes.write_text(json.dumps({"u": 2}))
    out = tmp_path / "result.json"
    argv = ["drift", "evaluate", "--plan-json", str(plans), "--exit-codes", str(codes)]
    argv += ["--env", "prod", "--config", str(tmp_path / "none.yaml"), "--out", str(out)]
    assert cli.main(argv) == 0
    assert json.loads(out.read_text()) == {
        "env": "prod",
        "drifted": True,
        "addresses": ["u: output.conn"],
    }
    assert "SECRET" not in out.read_text()
