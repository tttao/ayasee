"""`ayasee approve` (issue #40, D15): the prod gate that ayasee enforces itself with
`gate_mechanism: workflow`. Every check refuses on anything it cannot read."""

import hashlib
import subprocess

import httpx
import pytest
import respx

from ayasee import approve, cli
from ayasee.approve import Refused
from ayasee.binding import Manifest, UnitBinding
from ayasee.config import parse_config_text
from ayasee.github import GitHub
from ayasee.ledger import FileStore, UnitResult, record_from_run

API = "https://api.github.com"
REPO = "acme/estate"
SHA = "a" * 40
DIGEST = "d" * 64
WORKFLOW = "gate_mechanism: workflow\nreviewers: {prod: [Rita, ron]}\n"
UPGRADE = "Upgrade to GitHub Pro or make this repository public to enable this feature."


@pytest.fixture
def gh():
    client = GitHub(API, "tok")
    yield client
    client.close()


def _ok(**kw):
    base = dict(
        sha=SHA, digest=DIGEST, ref="refs/heads/main", event="workflow_dispatch", actor="rita"
    )
    base.update(kw)
    base.setdefault("triggering_actor", base["actor"])
    return base


# ---- the request -------------------------------------------------------------------------------


def test_a_well_formed_dispatch_on_main_passes():
    approve.check_request(**_ok())
    approve.check_request(**_ok(actor="Rita", triggering_actor="rita"))  # logins ignore case


@pytest.mark.parametrize(
    "kw, message",
    [
        ({"ref": "refs/heads/feature"}, "on refs/heads/main only"),
        ({"ref": "refs/tags/v1"}, "on refs/heads/main only"),
        ({"event": "push"}, "workflow_dispatch only"),
        ({"sha": "A" * 40}, "40-character lowercase"),
        ({"sha": "a" * 39}, "40-character lowercase"),
        ({"digest": "d" * 63}, "64-character"),
        ({"digest": "d" * 64 + "\n"}, "64-character"),
        ({"triggering_actor": "mallory"}, "not re-run by another person"),
        ({"actor": "", "triggering_actor": ""}, "no actor"),
    ],
)
def test_the_request_is_refused(kw, message):
    with pytest.raises(Refused, match=message):
        approve.check_request(**_ok(**kw))


# ---- the reviewer ------------------------------------------------------------------------------


def test_the_reviewer_comes_from_the_configuration_of_main():
    cfg = parse_config_text(WORKFLOW)
    approve.check_reviewer(cfg, "rita")
    approve.check_reviewer(cfg, "RON")
    with pytest.raises(Refused, match="mallory is not in reviewers.prod"):
        approve.check_reviewer(cfg, "mallory")
    with pytest.raises(Refused, match="gate_mechanism: environment"):
        approve.check_reviewer(parse_config_text(""), "rita")


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def history(tmp_path):
    """main: c1 (environment gate) -> c2 (workflow gate, rita) -> c3 (adds mallory)."""
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    _git("config", "user.email", "t@example.invalid", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    shas = []
    for text in (
        "stages: [dev, prod]\n",
        "gate_mechanism: workflow\nreviewers: {prod: [rita]}\n",
        "gate_mechanism: workflow\nreviewers: {prod: [rita, mallory]}\n",
    ):
        (root / ".ayasee.yaml").write_text(text)
        _git("add", ".", cwd=root)
        _git("commit", "-q", "-m", "c", cwd=root)
        shas.append(_git("rev-parse", "HEAD", cwd=root))
    _git("checkout", "-q", "-b", "side", shas[0], cwd=root)
    (root / "x").write_text("x")
    _git("add", ".", cwd=root)
    _git("commit", "-q", "-m", "side", cwd=root)
    shas.append(_git("rev-parse", "HEAD", cwd=root))
    _git("checkout", "-q", "main", cwd=root)
    return root, shas


def test_the_sha_must_be_an_ancestor_of_main(history):
    root, (c1, c2, c3, side) = history
    approve.check_ancestor(c2, c3, root)
    approve.check_ancestor(c3, c3, root)
    with pytest.raises(Refused, match="not an ancestor of main"):
        approve.check_ancestor(side, c3, root)
    with pytest.raises(Refused, match="not a commit"):
        approve.check_ancestor("b" * 40, c3, root)


PERM_URL = f"{API}/repos/{REPO}/collaborators/rita/permission"


@respx.mock
@pytest.mark.parametrize("perm", ["admin", "write"])
def test_a_reviewer_needs_write_access(gh, perm):
    respx.get(PERM_URL).mock(return_value=httpx.Response(200, json={"permission": perm}))
    approve.check_permission(gh, REPO, "rita")


@respx.mock
@pytest.mark.parametrize(
    "status, body, message",
    [
        (200, {"permission": "read"}, "has read access"),
        (200, {"permission": "none"}, "has none access"),
        (200, {}, "cannot read the permission"),
        (404, {"message": "Not Found"}, "cannot read the permission"),
        (500, {"message": "boom"}, "cannot read the permission"),
    ],
)
def test_a_reviewer_without_write_access_is_refused(gh, status, body, message):
    respx.get(PERM_URL).mock(return_value=httpx.Response(status, json=body))
    with pytest.raises(Refused, match=message):
        approve.check_permission(gh, REPO, "rita")


# ---- the prod environment ----------------------------------------------------------------------

ENV = "iac-prod"
ENV_URL = f"{API}/repos/{REPO}/environments/{ENV}"


def _env(policy=None, reviewers=0, status=200):
    rules = [{"type": "required_reviewers", "reviewers": [{}] * reviewers}] if reviewers else []
    body = {"name": ENV, "protection_rules": rules, "deployment_branch_policy": policy}
    respx.get(ENV_URL).mock(return_value=httpx.Response(status, json=body))


def _policies(*names):
    respx.get(f"{ENV_URL}/deployment-branch-policies").mock(
        return_value=httpx.Response(200, json={"branch_policies": [{"name": n} for n in names]})
    )


def _private_free():
    respx.get(f"{API}/repos/{REPO}").mock(
        return_value=httpx.Response(
            200, json={"visibility": "private", "owner": {"login": "acme", "type": "Organization"}}
        )
    )
    respx.get(f"{API}/meta").mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{API}/orgs/acme").mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{API}/repos/{REPO}/rulesets").mock(
        return_value=httpx.Response(403, json={"message": UPGRADE})
    )


CUSTOM = {"protected_branches": False, "custom_branch_policies": True}


@respx.mock
def test_a_prod_environment_restricted_to_main_passes(gh):
    _env(CUSTOM)
    _policies("main")
    assert approve.check_environment(gh, REPO, ENV) == []


@respx.mock
def test_reviewers_on_the_environment_are_noted(gh):
    _env(CUSTOM, reviewers=1)
    _policies("main")
    assert "also has required reviewers" in approve.check_environment(gh, REPO, ENV)[0]


@respx.mock
@pytest.mark.parametrize("names", [("main", "dev"), ("*",), ()])
def test_a_prod_environment_open_to_other_branches_is_refused(gh, names):
    _env(CUSTOM)
    _policies(*names)
    with pytest.raises(Refused, match="expected \\[main\\]"):
        approve.check_environment(gh, REPO, ENV)


@respx.mock
def test_protected_branches_are_not_main_only(gh):
    _env({"protected_branches": True, "custom_branch_policies": False})
    with pytest.raises(Refused, match="protected branches"):
        approve.check_environment(gh, REPO, ENV)


@respx.mock
def test_no_branch_policy_on_the_free_plan_is_a_deviation(gh):
    _env(None)
    _private_free()
    (note,) = approve.check_environment(gh, REPO, ENV)
    assert note.startswith("DEVIATION:") and "any collaborator with write" in note


@respx.mock
def test_no_branch_policy_on_a_plan_that_has_them_is_refused(gh):
    _env(None)
    respx.get(f"{API}/repos/{REPO}").mock(
        return_value=httpx.Response(200, json={"visibility": "public", "owner": {}})
    )
    with pytest.raises(Refused, match="not restricted to main"):
        approve.check_environment(gh, REPO, ENV)


@respx.mock
def test_no_branch_policy_on_an_unknown_plan_is_refused(gh):
    _env(None)
    respx.get(f"{API}/repos/{REPO}").mock(return_value=httpx.Response(500, json={}))
    with pytest.raises(Refused, match="not restricted to main"):
        approve.check_environment(gh, REPO, ENV)


@respx.mock
@pytest.mark.parametrize("status, message", [(404, "does not exist"), (403, "cannot read")])
def test_an_unreadable_prod_environment_is_refused(gh, status, message):
    _env(status=status)
    with pytest.raises(Refused, match=message):
        approve.check_environment(gh, REPO, ENV)


# ---- the plan ----------------------------------------------------------------------------------


def _artifact(art_id, run_id, *, sha=SHA, created="2026-11-14T10:00:00Z", expired=False):
    return {
        "id": art_id,
        "name": "ayasee-prod-plan",
        "expired": expired,
        "created_at": created,
        "workflow_run": {"id": run_id, "head_sha": sha, "head_branch": "main"},
    }


def _run(run_id, **kw):
    run = {
        "id": run_id,
        "head_sha": SHA,
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "actor": {"login": "pusher"},
        "triggering_actor": {"login": "pusher"},
        "referenced_workflows": [
            {"path": "tttao/ayasee/.github/workflows/iac-staged.yml@" + "f" * 40}
        ],
    }
    run.update(kw)
    respx.get(f"{API}/repos/{REPO}/actions/runs/{run_id}").mock(
        return_value=httpx.Response(200, json=run)
    )


def _artifacts(*items, status=200):
    respx.get(f"{API}/repos/{REPO}/actions/artifacts").mock(
        return_value=httpx.Response(status, json={"artifacts": list(items)})
    )


@respx.mock
def test_the_newest_plan_of_the_sha_is_found(gh):
    _artifacts(
        _artifact(1, 10, created="2026-11-13T10:00:00Z"),
        _artifact(2, 20, created="2026-11-14T10:00:00Z"),
        _artifact(3, 30, sha="b" * 40, created="2026-11-15T10:00:00Z"),
    )
    _run(20)
    assert approve.find_plan(gh, REPO, SHA) == approve.PlanRun(20, 2, ("pusher", "pusher"))


@respx.mock
@pytest.mark.parametrize(
    "run, message",
    [
        ({"head_branch": "feature"}, "did not run"),
        ({"head_sha": "b" * 40}, "did not run"),
        ({"event": "pull_request"}, "not by a push to main"),
        ({"conclusion": "failure"}, "not completed successfully"),
        ({"status": "in_progress", "conclusion": None}, "not completed successfully"),
        ({"referenced_workflows": []}, "not a run of the ayasee iac-staged"),
        (
            {"referenced_workflows": [{"path": "acme/x/.github/workflows/deploy.yml@main"}]},
            "not a run of the ayasee iac-staged",
        ),
        ({"actor": None, "triggering_actor": None}, "no actor"),
    ],
)
def test_a_plan_from_the_wrong_run_is_refused(gh, run, message):
    _artifacts(_artifact(2, 20))
    _run(20, **run)
    with pytest.raises(Refused, match=message):
        approve.find_plan(gh, REPO, SHA)


@respx.mock
def test_no_or_an_expired_plan_is_refused(gh):
    _artifacts(_artifact(3, 30, sha="b" * 40))
    with pytest.raises(Refused, match="no prod plan"):
        approve.find_plan(gh, REPO, SHA)
    _artifacts(_artifact(2, 20, expired=True))
    with pytest.raises(Refused, match="expired"):
        approve.find_plan(gh, REPO, SHA)


@respx.mock
def test_an_artifact_api_error_is_refused(gh):
    _artifacts(status=500)
    with pytest.raises(Refused, match="cannot list the prod plans"):
        approve.find_plan(gh, REPO, SHA)


# ---- independence ------------------------------------------------------------------------------


def _change(author="alice", committer="web-flow", pr_user="alice", merged_by="mergy"):
    respx.get(f"{API}/repos/{REPO}/commits/{SHA}").mock(
        return_value=httpx.Response(
            200, json={"author": {"login": author}, "committer": {"login": committer}}
        )
    )
    respx.get(f"{API}/repos/{REPO}/commits/{SHA}/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"number": 7, "user": {"login": pr_user}, "base": {"ref": "main"}},
                {"number": 8, "user": {"login": "other"}, "base": {"ref": "release"}},
            ],
        )
    )
    respx.get(f"{API}/repos/{REPO}/pulls/7").mock(
        return_value=httpx.Response(200, json={"merged_by": {"login": merged_by}})
    )


@respx.mock
def test_the_people_of_a_change(gh):
    _change()
    people = approve.change_people(gh, REPO, SHA, ("pusher",))
    assert people == {"pusher", "alice", "web-flow", "mergy"}
    approve.check_independent("rita", people, "made it")
    for who in ("pusher", "ALICE", "mergy"):
        with pytest.raises(Refused, match="must not approve their own change"):
            approve.check_independent(who, people, "made it")


@respx.mock
def test_an_unreadable_change_is_refused(gh):
    respx.get(f"{API}/repos/{REPO}/commits/{SHA}").mock(return_value=httpx.Response(502))
    with pytest.raises(Refused, match="cannot read who made"):
        approve.change_people(gh, REPO, SHA, ())


def _authorize(gh, history, cfg_text=WORKFLOW, actor="rita"):
    root, (c1, c2, c3, _side) = history
    return approve.authorize(
        gh,
        repo=REPO,
        **_ok(sha=c2, actor=actor),
        head=c3,
        cfg=parse_config_text(cfg_text),
        environment=ENV,
        root=root,
    )


def _all_green(c2, author="alice", merged_by="mergy"):
    respx.get(f"{API}/repos/{REPO}/collaborators/rita/permission").mock(
        return_value=httpx.Response(200, json={"permission": "write"})
    )
    _env(CUSTOM)
    _policies("main")
    respx.get(f"{API}/repos/{REPO}/actions/artifacts").mock(
        return_value=httpx.Response(200, json={"artifacts": [_artifact(2, 20, sha=c2)]})
    )
    _run(20, head_sha=c2)
    respx.get(f"{API}/repos/{REPO}/commits/{c2}").mock(
        return_value=httpx.Response(200, json={"author": {"login": author}, "committer": None})
    )
    respx.get(f"{API}/repos/{REPO}/commits/{c2}/pulls").mock(
        return_value=httpx.Response(
            200, json=[{"number": 7, "user": {"login": author}, "base": {"ref": "main"}}]
        )
    )
    respx.get(f"{API}/repos/{REPO}/pulls/7").mock(
        return_value=httpx.Response(200, json={"merged_by": {"login": merged_by}})
    )


@respx.mock
def test_authorize_runs_every_check(gh, history):
    _all_green(history[1][1])
    plan, notes = _authorize(gh, history)
    assert plan.run_id == 20 and notes == []


@respx.mock
def test_authorize_refuses_the_merger_unless_solo_mode(gh, history):
    _all_green(history[1][1], merged_by="rita")
    with pytest.raises(Refused, match="rita pushed, wrote or merged it"):
        _authorize(gh, history)
    plan, notes = _authorize(gh, history, WORKFLOW + "solo_mode: true\n")
    assert plan.run_id == 20 and notes[0].startswith("NON-COMPLIANT")


@respx.mock
def test_authorize_refuses_before_any_api_call_when_not_a_reviewer(gh, history):
    # no route is mocked: an API call would fail the test with a respx error
    with pytest.raises(Refused, match="mallory is not in reviewers.prod"):
        _authorize(gh, history, actor="mallory")


# ---- the digest --------------------------------------------------------------------------------


def _manifest(tmp_path, sha=SHA, env="prod"):
    m = Manifest(sha, env, "opentofu", "1.10.0", {"u": UnitBinding("p" * 64, "l" * 64)})
    path = tmp_path / "manifest.json"
    path.write_text(m.to_json())
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_digest_binds_the_manifest(tmp_path):
    path, digest = _manifest(tmp_path)
    approve.check_digest(path, digest, SHA)
    with pytest.raises(Refused, match="does not match manifest-digest"):
        approve.check_digest(path, "0" * 64, SHA)
    path, digest = _manifest(tmp_path, sha="b" * 40)
    with pytest.raises(Refused, match="not prod at"):
        approve.check_digest(path, digest, SHA)
    path, digest = _manifest(tmp_path, env="uat")
    with pytest.raises(Refused, match="binds uat"):
        approve.check_digest(path, digest, SHA)
    with pytest.raises(Refused, match="cannot read the manifest"):
        approve.check_digest(tmp_path / "missing.json", digest, SHA)


# ---- the baseline ------------------------------------------------------------------------------


def _live(tmp_path, sha, converged=True):
    store = FileStore(tmp_path / "ledger")
    units = {"u": UnitResult("applied", converged)}
    store.append(
        record_from_run(repo=REPO, env="prod", sha=sha, run_id=1, run_attempt=1, units=units)
    )
    return store


def _compare(base, head, *logins, status=200):
    commits = [{"author": {"login": x}, "committer": {"login": "web-flow"}} for x in logins]
    respx.get(f"{API}/repos/{REPO}/compare/{base}...{head}").mock(
        return_value=httpx.Response(status, json={"commits": commits})
    )


def _baseline(gh, store, history, sha, actor="rita", solo=False):
    root, _ = history
    return approve.baseline(
        gh,
        store,
        repo=REPO,
        sha=sha,
        actor=actor,
        run_id=20,
        main_solo=solo,
        config_path=".ayasee.yaml",
        root=root,
    )


@respx.mock
def test_baseline_without_a_prod_record_notes_it(gh, history, tmp_path):
    store = FileStore(tmp_path / "ledger")
    (note,) = _baseline(gh, store, history, history[1][2])
    assert "no prod record yet" in note


@respx.mock
def test_baseline_refuses_a_sha_already_live(gh, history, tmp_path):
    c3 = history[1][2]
    with pytest.raises(Refused, match="already live"):
        _baseline(gh, _live(tmp_path, c3), history, c3)


@respx.mock
def test_baseline_refuses_a_reviewer_added_by_the_change_itself(gh, history, tmp_path):
    _, (c1, c2, c3, _side) = history
    _compare(c2, c3, "alice")
    store = _live(tmp_path, c2)
    with pytest.raises(Refused, match="mallory was not in reviewers.prod at the commit live"):
        _baseline(gh, store, history, c3, actor="mallory")
    assert _baseline(gh, store, history, c3) == []


@respx.mock
def test_baseline_refuses_the_author_of_a_commit_since_prod(gh, history, tmp_path):
    _, (c1, c2, c3, _side) = history
    _compare(c2, c3, "alice", "rita")
    with pytest.raises(Refused, match="wrote a commit since"):
        _baseline(gh, _live(tmp_path, c2), history, c3)


@respx.mock
def test_baseline_after_the_environment_gate_notes_the_transition(gh, history, tmp_path):
    _, (c1, c2, c3, _side) = history
    _compare(c1, c3, "alice")
    (note,) = _baseline(gh, _live(tmp_path, c1), history, c3, actor="mallory")
    assert "predates gate_mechanism: workflow" in note


@respx.mock
def test_baseline_refuses_an_unreadable_compare(gh, history, tmp_path):
    _, (c1, c2, c3, _side) = history
    _compare(c2, c3, status=500)
    with pytest.raises(Refused, match="cannot list the commits"):
        _baseline(gh, _live(tmp_path, c2), history, c3)


@respx.mock
def test_baseline_refuses_a_live_commit_outside_the_history(gh, history, tmp_path):
    with pytest.raises(Refused, match="not in the history of main"):
        _baseline(gh, _live(tmp_path, "b" * 40), history, history[1][2])


@respx.mock
def test_solo_mode_counts_only_when_both_commits_have_it(gh, history, tmp_path):
    # main says solo_mode, the commit live in prod does not: independence is still checked
    _, (c1, c2, c3, _side) = history
    _run(20, head_sha=c3)
    respx.get(f"{API}/repos/{REPO}/commits/{c3}").mock(
        return_value=httpx.Response(200, json={"author": {"login": "rita"}})
    )
    respx.get(f"{API}/repos/{REPO}/commits/{c3}/pulls").mock(
        return_value=httpx.Response(200, json=[])
    )
    with pytest.raises(Refused, match="rita pushed, wrote or merged it"):
        _baseline(gh, _live(tmp_path, c2), history, c3, solo=True)


# ---- the approval issue: identifiers and counts only -------------------------------------------

SENTINEL = "ayasee-fixture-secret-marker"


def test_the_issue_body_holds_identifiers_and_counts_only():
    body = approve.issue_body(
        sha=SHA,
        digest=DIGEST,
        run_url="https://github.com/x/y/actions/runs/1",
        changes=3,
        critical=1,
        high=2,
    )
    assert SHA in body and DIGEST in body and "Changes: 3 (critical 1, high 2)" in body
    assert "DEVIATION: gate enforced by ayasee" in body


def _cli(monkeypatch, *argv):
    monkeypatch.setattr(cli, "_COMMANDS", {"approve": approve._setup})
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    return cli.main(["approve", *argv])


ISSUE_ARGS = [
    "issue",
    "--repo",
    REPO,
    "--sha",
    SHA,
    "--digest",
    DIGEST,
    "--run-url",
    "https://github.com/acme/estate/actions/runs/20",
    "--changes",
    "3",
    "--critical",
    "0",
    "--high",
    "1",
]


@respx.mock
def test_the_issue_is_opened_then_rewritten(monkeypatch):
    respx.get(f"{API}/repos/{REPO}/issues").mock(return_value=httpx.Response(200, json=[]))
    created = respx.post(f"{API}/repos/{REPO}/issues").mock(
        return_value=httpx.Response(201, json={"number": 5})
    )
    assert _cli(monkeypatch, *ISSUE_ARGS) == 0
    sent = created.calls.last.request.content.decode()
    assert "ayasee:prod-approval" in sent and SENTINEL not in sent
    respx.get(f"{API}/repos/{REPO}/issues").mock(
        return_value=httpx.Response(200, json=[{"number": 5, "body": "old"}])
    )
    patched = respx.patch(f"{API}/repos/{REPO}/issues/5").mock(return_value=httpx.Response(200))
    commented = respx.post(f"{API}/repos/{REPO}/issues/5/comments").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    assert _cli(monkeypatch, *ISSUE_ARGS) == 0
    assert SHA[:12] in patched.calls.last.request.content.decode()
    assert "Replaced by the plan of" in commented.calls.last.request.content.decode()


@pytest.mark.parametrize(
    "flag, value",
    [
        ("--changes", SENTINEL),
        ("--critical", "-1"),
        ("--run-url", "javascript:x"),
        ("--sha", SENTINEL),
        ("--digest", "x"),
    ],
)
def test_the_issue_refuses_anything_but_identifiers_and_counts(monkeypatch, capsys, flag, value):
    args = list(ISSUE_ARGS)
    args[args.index(flag) + 1] = value
    assert _cli(monkeypatch, *args) == 1
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err


@respx.mock
def test_close_issue_closes_only_the_issue_of_the_applied_plan(monkeypatch):
    respx.get(f"{API}/repos/{REPO}/issues").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"number": 5, "body": f"{SHA} {DIGEST}"},
                {"number": 6, "body": f"{SHA} {'e' * 64}"},
            ],
        )
    )
    closed = respx.patch(f"{API}/repos/{REPO}/issues/5").mock(return_value=httpx.Response(200))
    respx.post(f"{API}/repos/{REPO}/issues/5/comments").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    args = [
        "close-issue",
        "--repo",
        REPO,
        "--sha",
        SHA,
        "--digest",
        DIGEST,
        "--run-url",
        "https://github.com/acme/estate/actions/runs/9",
        "--approver",
        "rita",
    ]
    assert _cli(monkeypatch, *args) == 0
    assert closed.called


def test_the_digest_cli(tmp_path, monkeypatch, capsys):
    path, digest = _manifest(tmp_path)
    args = ["digest", "--manifest", str(path), "--sha", SHA]
    assert _cli(monkeypatch, *args, "--digest", digest) == 0
    assert _cli(monkeypatch, *args, "--digest", "0" * 64) == 1
    assert "approve: the prod plan does not match" in capsys.readouterr().err


@respx.mock
def test_the_authorize_cli_prints_outputs_and_refusals(monkeypatch, capsys, history):
    root, (c1, c2, c3, _side) = history
    _all_green(c2)
    monkeypatch.chdir(root)
    args = [
        "authorize",
        "--repo",
        REPO,
        "--sha",
        c2,
        "--digest",
        DIGEST,
        "--ref",
        "refs/heads/main",
        "--event",
        "workflow_dispatch",
        "--actor",
        "rita",
        "--triggering-actor",
        "rita",
        "--head",
        c3,
        "--config-path",
        ".ayasee.yaml",
    ]
    # main (c3) lists rita and mallory, with no environment_prefix
    respx.get(f"{API}/repos/{REPO}/environments/prod").mock(
        return_value=httpx.Response(
            200, json={"protection_rules": [], "deployment_branch_policy": CUSTOM}
        )
    )
    respx.get(f"{API}/repos/{REPO}/environments/prod/deployment-branch-policies").mock(
        return_value=httpx.Response(200, json={"branch_policies": [{"name": "main"}]})
    )
    assert _cli(monkeypatch, *args) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "run-id=20",
        "approver=rita",
        "solo-mode=false",
        "environment-prefix=",
        "env-prod=prod",
    ]
    args[args.index("--triggering-actor") + 1] = "mallory"
    assert _cli(monkeypatch, *args) == 1
    assert "approve: this run was started by rita" in capsys.readouterr().err
