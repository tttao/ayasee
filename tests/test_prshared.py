"""`ayasee pr-shared` (issue #40, D15): the pull-request plan of the shared prod units with
`gate_mechanism: workflow`. A prod reviewer authorises one head SHA of one pull request from a
dispatch on main; every check refuses on anything it cannot read, before any secret is used."""

import json

import httpx
import pytest
import respx

from ayasee import cli, prcomment, prshared
from ayasee.approve import Refused
from ayasee.config import parse_config_text
from ayasee.github import GitHub

API = "https://api.github.com"
REPO = "acme/estate"
SHA = "a" * 40
OTHER = "b" * 40
SHARED = "stages: [dev, prod]\nshared_units: [edge]\n"
WORKFLOW = SHARED + "gate_mechanism: workflow\nreviewers: {prod: [Rita, ron]}\n"
ENV = "prod-pr-plan"
ENV_URL = f"{API}/repos/{REPO}/environments/{ENV}"
CUSTOM = {"protected_branches": False, "custom_branch_policies": True}
SENTINEL = "ayasee-fixture-secret-marker"


@pytest.fixture
def gh():
    client = GitHub(API, "tok")
    yield client
    client.close()


def _ok(**kw):
    base = dict(pr="7", sha=SHA, ref="refs/heads/main", event="workflow_dispatch", actor="rita")
    base.update(kw)
    base.setdefault("triggering_actor", base["actor"])
    return base


# ---- the request -------------------------------------------------------------------------------


def test_a_well_formed_dispatch_on_main_passes():
    assert prshared.check_request(**_ok()) == 7
    assert prshared.check_request(**_ok(actor="Rita", triggering_actor="rita")) == 7


@pytest.mark.parametrize(
    "kw, message",
    [
        ({"ref": "refs/heads/feature"}, "on refs/heads/main only"),
        ({"ref": "refs/pull/7/merge"}, "on refs/heads/main only"),
        ({"event": "pull_request"}, "workflow_dispatch only"),
        ({"event": "pull_request_target"}, "workflow_dispatch only"),
        ({"sha": "A" * 40}, "40-character lowercase"),
        ({"sha": "a" * 39}, "40-character lowercase"),
        ({"pr": "0"}, "pull request number"),
        ({"pr": "7 "}, "pull request number"),
        ({"pr": "-7"}, "pull request number"),
        ({"pr": "1" * 12}, "pull request number"),
        ({"triggering_actor": "mallory"}, "not re-run by another person"),
        ({"actor": "", "triggering_actor": ""}, "no actor"),
    ],
)
def test_the_request_is_refused(kw, message):
    with pytest.raises(Refused, match=message):
        prshared.check_request(**_ok(**kw))


# ---- the settings of main ----------------------------------------------------------------------


def test_the_reviewer_comes_from_the_configuration_of_main():
    cfg = parse_config_text(WORKFLOW)
    prshared.check_settings(cfg, "rita")
    prshared.check_settings(cfg, "RON")
    with pytest.raises(Refused, match="mallory is not in reviewers.prod"):
        prshared.check_settings(cfg, "mallory")


def test_the_environment_gate_plans_in_the_pull_request_itself():
    with pytest.raises(Refused, match="gate_mechanism: environment"):
        prshared.check_settings(parse_config_text(SHARED), "rita")


def test_a_configuration_without_shared_units_has_nothing_to_plan():
    text = "gate_mechanism: workflow\nreviewers: {prod: [rita]}\n"
    with pytest.raises(Refused, match="no shared_units"):
        prshared.check_settings(parse_config_text(text), "rita")


# ---- the pull request --------------------------------------------------------------------------


def _pr(**kw):
    body = {
        "number": 7,
        "state": "open",
        "user": {"login": "alice"},
        "base": {"ref": "main", "repo": {"full_name": REPO}},
        "head": {"sha": SHA, "repo": {"full_name": REPO}},
    }
    for key, value in kw.items():
        if key in ("base_ref", "base_repo", "head_sha", "head_repo"):
            part, field = key.split("_")
            target = body[part]
            if field == "ref":
                target["ref"] = value
            elif field == "sha":
                target["sha"] = value
            else:
                target["repo"] = value if value is None else {"full_name": value}
        else:
            body[key] = value
    return body


def _mock_pr(**kw):
    respx.get(f"{API}/repos/{REPO}/pulls/7").mock(return_value=httpx.Response(200, json=_pr(**kw)))


@respx.mock
def test_an_open_same_repository_pull_request_at_the_sha_passes(gh):
    _mock_pr()
    assert prshared.check_pull_request(gh, REPO, 7, SHA)["user"]["login"] == "alice"


@respx.mock
@pytest.mark.parametrize(
    "kw, message",
    [
        ({"state": "closed"}, "is closed"),
        # re-pushed after the authorisation
        ({"head_sha": OTHER}, "#7 is now bbbbbbbbbbbb, not aaaaaaaaaaaa"),
        ({"head_repo": "mallory/estate"}, "from a fork"),
        ({"head_repo": None}, "from a fork"),  # a deleted fork
        ({"base_ref": "release"}, "targets release, not main"),
        ({"base_repo": "other/estate"}, "not a pull request of acme/estate"),
    ],
)
def test_the_pull_request_is_refused(gh, kw, message):
    _mock_pr(**kw)
    with pytest.raises(Refused, match=message):
        prshared.check_pull_request(gh, REPO, 7, SHA)


@respx.mock
def test_an_unreadable_pull_request_is_refused(gh):
    respx.get(f"{API}/repos/{REPO}/pulls/7").mock(return_value=httpx.Response(404, json={}))
    with pytest.raises(Refused, match="cannot read pull request #7"):
        prshared.check_pull_request(gh, REPO, 7, SHA)


def _commits(*people, status=200):
    commits = [
        {"author": {"login": a} if a else None, "committer": {"login": c} if c else None}
        for a, c in people
    ]
    respx.get(f"{API}/repos/{REPO}/pulls/7/commits").mock(
        return_value=httpx.Response(status, json=commits if status == 200 else {})
    )


@respx.mock
def test_the_people_of_a_pull_request(gh):
    _commits(("alice", "web-flow"), ("bob", "bob"), (None, None))
    people, unknown = prshared.pr_people(gh, REPO, 7, _pr())
    assert people == {"alice", "web-flow", "bob"} and unknown == 1


@respx.mock
def test_unreadable_commits_are_refused(gh):
    _commits(status=500)
    with pytest.raises(Refused, match="cannot read the commits of pull request #7"):
        prshared.pr_people(gh, REPO, 7, _pr())


@respx.mock
def test_a_pull_request_with_more_commits_than_the_api_lists_is_refused(gh):
    _commits(*[("alice", "alice")] * 250)
    with pytest.raises(Refused, match="250 commits or more"):
        prshared.pr_people(gh, REPO, 7, _pr())


# ---- the environment ---------------------------------------------------------------------------


def _env(policy=None, reviewers=0, status=200):
    rules = [{"type": "required_reviewers", "reviewers": [{}] * reviewers}] if reviewers else []
    body = {"name": ENV, "protection_rules": rules, "deployment_branch_policy": policy}
    respx.get(ENV_URL).mock(return_value=httpx.Response(status, json=body))


def _policies(*names):
    respx.get(f"{ENV_URL}/deployment-branch-policies").mock(
        return_value=httpx.Response(200, json={"branch_policies": [{"name": n} for n in names]})
    )


@respx.mock
def test_an_environment_restricted_to_main_passes(gh):
    _env(CUSTOM)
    _policies("main")
    assert prshared.check_environment(gh, REPO, ENV) == []


@respx.mock
def test_reviewers_on_the_environment_are_noted(gh):
    _env(CUSTOM, reviewers=1)
    _policies("main")
    assert "also has required reviewers" in prshared.check_environment(gh, REPO, ENV)[0]


@respx.mock
@pytest.mark.parametrize("names", [("main", "feature/*"), ("*",), ()])
def test_an_environment_open_to_other_branches_is_refused(gh, names):
    _env(CUSTOM)
    _policies(*names)
    with pytest.raises(Refused, match="expected \\[main\\]"):
        prshared.check_environment(gh, REPO, ENV)


@respx.mock
def test_protected_branches_are_not_main_only(gh):
    _env({"protected_branches": True, "custom_branch_policies": False})
    with pytest.raises(Refused, match="protected branches"):
        prshared.check_environment(gh, REPO, ENV)


@respx.mock
def test_no_branch_policy_is_refused_even_on_github_free(gh):
    """On GitHub Free the environment cannot be closed to other branches and holds no secret of
    its own: the plan would run the PR's code with the prod apply's credentials. No API call
    about the plan is needed to refuse that: any environment open to every branch is refused."""
    _env(None)
    with pytest.raises(Refused, match="accepts every branch") as exc:
        prshared.check_environment(gh, REPO, ENV)
    assert "GitHub Free" in str(exc.value)


@respx.mock
@pytest.mark.parametrize("status, message", [(404, "does not exist"), (403, "cannot read")])
def test_an_unreadable_environment_is_refused(gh, status, message):
    _env(status=status)
    with pytest.raises(Refused, match=message):
        prshared.check_environment(gh, REPO, ENV)


# ---- authorize ---------------------------------------------------------------------------------


def _all_green(author="alice", commits=(("alice", "alice"),)):
    respx.get(f"{API}/repos/{REPO}/collaborators/rita/permission").mock(
        return_value=httpx.Response(200, json={"permission": "write"})
    )
    _mock_pr(user={"login": author})
    _commits(*commits)
    _env(CUSTOM)
    _policies("main")


def _authorize(gh, text=WORKFLOW, **kw):
    return prshared.authorize(
        gh, repo=REPO, cfg=parse_config_text(text), environment=ENV, **_ok(**kw)
    )


@respx.mock
def test_authorize_runs_every_check(gh):
    _all_green()
    assert _authorize(gh) == []


@respx.mock
@pytest.mark.parametrize(
    "author, commits",
    [
        ("rita", (("alice", "alice"),)),  # opened the pull request
        ("alice", (("rita", "web-flow"),)),  # wrote a commit of it
        ("alice", (("alice", "RITA"),)),  # committed one (logins ignore case)
    ],
)
def test_authorize_refuses_the_author_unless_solo_mode(gh, author, commits):
    _all_green(author, commits)
    with pytest.raises(Refused, match="rita opened or wrote it"):
        _authorize(gh)
    notes = _authorize(gh, WORKFLOW + "solo_mode: true\n")
    assert notes[0].startswith("NON-COMPLIANT")


@respx.mock
def test_commits_without_a_login_are_reported(gh):
    _all_green(commits=(("alice", "alice"), (None, None)))
    (note,) = _authorize(gh)
    assert "1 commit(s) of pull request #7 have no GitHub login" in note


@respx.mock
def test_authorize_refuses_before_any_api_call_when_not_a_reviewer(gh):
    # no route is mocked: an API call would fail the test with a respx error
    with pytest.raises(Refused, match="mallory is not in reviewers.prod"):
        _authorize(gh, actor="mallory")


@respx.mock
def test_authorize_refuses_a_reviewer_without_write_access(gh):
    _all_green()
    respx.get(f"{API}/repos/{REPO}/collaborators/rita/permission").mock(
        return_value=httpx.Response(200, json={"permission": "read"})
    )
    with pytest.raises(Refused, match="a reviewer needs write"):
        _authorize(gh)


@respx.mock
def test_authorize_refuses_a_head_that_moved_on(gh):
    _all_green()
    _mock_pr(head_sha=OTHER)
    with pytest.raises(Refused, match="authorise the new head"):
        _authorize(gh)


# ---- the CLI -----------------------------------------------------------------------------------


def _cli(monkeypatch, *argv):
    monkeypatch.setattr(cli, "_COMMANDS", {"pr-shared": prshared._setup})
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    return cli.main(["pr-shared", *argv])


AUTH_ARGS = [
    "authorize",
    "--repo",
    REPO,
    "--pr",
    "7",
    "--sha",
    SHA,
    "--ref",
    "refs/heads/main",
    "--event",
    "workflow_dispatch",
    "--actor",
    "rita",
    "--triggering-actor",
    "rita",
]


@respx.mock
def test_the_authorize_cli_prints_outputs_and_refusals(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / ".ayasee.yaml"
    cfg.write_text(WORKFLOW + "environment_prefix: iac-\n")
    _all_green()
    respx.get(f"{API}/repos/{REPO}/environments/iac-prod-pr-plan").mock(
        return_value=httpx.Response(
            200, json={"protection_rules": [], "deployment_branch_policy": CUSTOM}
        )
    )
    respx.get(f"{API}/repos/{REPO}/environments/iac-prod-pr-plan/deployment-branch-policies").mock(
        return_value=httpx.Response(200, json={"branch_policies": [{"name": "main"}]})
    )
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", SENTINEL)
    assert _cli(monkeypatch, *AUTH_ARGS, "--config-path", str(cfg)) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines() == ["approver=rita", "environment=iac-prod-pr-plan"]
    args = list(AUTH_ARGS)
    args[args.index("--triggering-actor") + 1] = "mallory"
    assert _cli(monkeypatch, *args, "--config-path", str(cfg)) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "pr-shared: this run was started by rita" in captured.err
    assert SENTINEL not in captured.out + captured.err


def test_the_authorize_cli_fails_closed_on_a_bad_configuration(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / ".ayasee.yaml"
    cfg.write_text("stages: [prod, dev\n")
    assert _cli(monkeypatch, *AUTH_ARGS, "--config-path", str(cfg)) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("pr-shared: ")


# ---- the comment -------------------------------------------------------------------------------

RUN_URL = "https://github.com/acme/estate/actions/runs/99"


def _comment_args(tmp_path, body="### Shared prod units plan\n\n1 to change\n", **kw):
    f = tmp_path / "body.md"
    f.write_text(body)
    args = {
        "--repo": REPO,
        "--pr": "7",
        "--sha": SHA,
        "--approver": "rita",
        "--run-url": RUN_URL,
        "--body-file": str(f),
    }
    args.update(kw)
    return ["comment", *[x for kv in args.items() for x in kv]]


def _capture_comment():
    respx.get(f"{API}/repos/{REPO}/issues/7/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    route = respx.post(f"{API}/repos/{REPO}/issues/7/comments").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    return route


@respx.mock
def test_the_comment_names_the_authorised_sha_and_the_approver(tmp_path, monkeypatch):
    _mock_pr()
    route = _capture_comment()
    assert _cli(monkeypatch, *_comment_args(tmp_path)) == 0
    sent = json.loads(route.calls.last.request.content)["body"]
    assert sent.startswith(prcomment.MARKERS["pr-plan-shared"] + "\n")
    assert f"`{SHA[:12]}`" in sent and "authorised by rita" in sent and RUN_URL in sent
    assert "1 to change" in sent and "moved on" not in sent
    assert "@rita" not in sent  # no mention: the approver is named, not notified


@respx.mock
def test_the_comment_says_when_the_pull_request_moved_on(tmp_path, monkeypatch):
    _mock_pr(head_sha=OTHER)
    route = _capture_comment()
    assert _cli(monkeypatch, *_comment_args(tmp_path)) == 0
    sent = json.loads(route.calls.last.request.content)["body"]
    assert "moved on" in sent and f"`{OTHER[:12]}`" in sent


@respx.mock
def test_the_comment_carries_only_the_body_file_and_identifiers(tmp_path, monkeypatch):
    """The plan job writes the body from the change set (no values); the comment job adds
    identifiers only. A value in its environment never reaches the comment."""
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", SENTINEL)
    monkeypatch.setenv("AYASEE_PROVIDER_ENV", json.dumps({"HCLOUD_TOKEN": SENTINEL}))
    _mock_pr()
    route = _capture_comment()
    assert _cli(monkeypatch, *_comment_args(tmp_path)) == 0
    assert SENTINEL not in route.calls.last.request.content.decode()


@pytest.mark.parametrize(
    "flag, value",
    [
        ("--sha", SENTINEL),
        ("--sha", "A" * 40),
        ("--run-url", "javascript:alert(1)"),
        ("--run-url", f"https://x/{SENTINEL} [x](y)"),
        ("--approver", "rita]\n<img src=x>"),
        ("--pr", "0"),
    ],
)
def test_the_comment_refuses_anything_but_identifiers(tmp_path, monkeypatch, capsys, flag, value):
    assert _cli(monkeypatch, *_comment_args(tmp_path, **{flag: value})) == 1
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err


@respx.mock
def test_an_unreadable_pull_request_still_posts_but_says_so(tmp_path, monkeypatch):
    respx.get(f"{API}/repos/{REPO}/pulls/7").mock(return_value=httpx.Response(500, json={}))
    route = _capture_comment()
    assert _cli(monkeypatch, *_comment_args(tmp_path)) == 0
    sent = json.loads(route.calls.last.request.content)["body"]
    assert "could not confirm the head" in sent
