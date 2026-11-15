"""Can the repository's GitHub plan hold the gate (issue #39)? A public repository can; a private
one needs GitHub Enterprise for environment reviewers. Anything not established is UNKNOWN, never
SUPPORTED."""

import httpx
import pytest
import respx

from ayasee import plansupport as ps
from ayasee.github import GitHub, GitHubError

API = "https://api.github.com"
REPO = "acme/estate"
UPGRADE = "Upgrade to GitHub Pro or make this repository public to enable this feature."


@pytest.fixture
def gh():
    client = GitHub(API, "tok")
    yield client
    client.close()


def _repo(visibility="private", owner_type="Organization", private=None):
    body = {"full_name": REPO, "owner": {"login": "acme", "type": owner_type}}
    if visibility is not None:
        body["visibility"] = visibility
    if private is not None:
        body["private"] = private
    return respx.get(f"{API}/repos/{REPO}").mock(return_value=httpx.Response(200, json=body))


def _org(plan=None, status=200):
    body = {"login": "acme"}
    if plan is not None:
        body["plan"] = {"name": plan}
    return respx.get(f"{API}/orgs/acme").mock(return_value=httpx.Response(status, json=body))


def _rulesets(status=200, message=""):
    body = [] if status == 200 else {"message": message}
    return respx.get(f"{API}/repos/{REPO}/rulesets").mock(
        return_value=httpx.Response(status, json=body)
    )


def _envs(*envs, status=200, message=""):
    body = {"environments": list(envs)} if status == 200 else {"message": message}
    return respx.get(f"{API}/repos/{REPO}/environments").mock(
        return_value=httpx.Response(status, json=body)
    )


def _gated(name="prod"):
    return {
        "name": name,
        "protection_rules": [{"type": "required_reviewers", "reviewers": [{"type": "Team"}]}],
    }


@respx.mock
def test_a_public_repository_has_every_feature(gh):
    _repo("public")
    s = ps.detect(gh, REPO)
    assert s.verdict == ps.SUPPORTED and s.missing == () and s.visibility == "public"


@respx.mock
def test_an_internal_repository_is_enterprise(gh):
    _repo("internal")
    s = ps.detect(gh, REPO)
    assert s.verdict == ps.SUPPORTED and s.plan == "enterprise"


@respx.mock
def test_private_without_visibility_field_reads_private(gh):
    _repo(None, private=False)
    assert ps.detect(gh, REPO).verdict == ps.SUPPORTED


@respx.mock
@pytest.mark.parametrize(
    ("plan", "verdict", "missing"),
    [
        ("free", ps.UNSUPPORTED, (ps.REVIEWERS, ps.BRANCH_POLICIES, ps.RULESETS)),
        ("team", ps.UNSUPPORTED, (ps.REVIEWERS,)),
        ("enterprise", ps.SUPPORTED, ()),
    ],
)
def test_a_private_organization_repository_follows_the_plan(gh, plan, verdict, missing):
    _repo()
    _org(plan)
    s = ps.detect(gh, REPO)
    assert (s.verdict, s.plan, s.missing) == (verdict, plan, missing)


@respx.mock
@pytest.mark.parametrize(("plan", "missing"), [("free", 3), ("pro", 1)])
def test_a_private_user_repository_reads_the_plan_of_its_owner(gh, plan, missing):
    _repo(owner_type="User")
    respx.get(f"{API}/user").mock(
        return_value=httpx.Response(200, json={"login": "acme", "plan": {"name": plan}})
    )
    s = ps.detect(gh, REPO)
    assert s.verdict == ps.UNSUPPORTED and len(s.missing) == missing and s.plan == plan


@respx.mock
def test_the_upgrade_403_on_rulesets_means_the_free_plan(gh):
    """The case of the issue: a token that cannot read the organization's plan."""
    _repo()
    _org(None)  # not an owner: no plan field
    _rulesets(403, UPGRADE)
    s = ps.detect(gh, REPO)
    assert s.verdict == ps.UNSUPPORTED and s.plan == "free"
    assert set(s.missing) == {ps.REVIEWERS, ps.BRANCH_POLICIES, ps.RULESETS}


@respx.mock
def test_reviewers_already_set_are_evidence_of_the_feature(gh):
    _repo()
    _org(status=404)
    _rulesets()
    _envs({"name": "dev", "protection_rules": []}, _gated())
    s = ps.detect(gh, REPO)
    assert s.verdict == ps.SUPPORTED and s.plan is None and s.inferred


@respx.mock
def test_a_private_repository_whose_plan_cannot_be_read_is_unknown(gh):
    _repo()
    _org(None)
    _rulesets()
    _envs({"name": "dev", "protection_rules": []})
    s = ps.detect(gh, REPO)
    assert s.verdict == ps.UNKNOWN and not s.supported


@respx.mock
def test_an_unknown_plan_name_is_not_taken_for_a_paid_one(gh):
    _repo()
    _org("something-new")
    _rulesets()
    _envs()
    assert ps.detect(gh, REPO).verdict == ps.UNKNOWN


@respx.mock
def test_other_api_errors_are_unknown_never_supported(gh):
    _repo()
    _org(status=500)
    _rulesets(500, "boom")
    _envs(status=403, message="Resource not accessible by integration")
    assert ps.detect(gh, REPO).verdict == ps.UNKNOWN


@respx.mock
def test_an_unreadable_repository_is_unknown(gh):
    respx.get(f"{API}/repos/{REPO}").mock(return_value=httpx.Response(404, json={}))
    s = ps.detect(gh, REPO)
    assert s.verdict == ps.UNKNOWN and "cannot read the repository" in " ".join(s.evidence)


@respx.mock
def test_an_unknown_visibility_is_unknown(gh):
    _repo("secret-new-kind")
    assert ps.detect(gh, REPO).verdict == ps.UNKNOWN


def test_github_unreachable_is_unknown(gh):
    with respx.mock:
        respx.get(f"{API}/repos/{REPO}").mock(side_effect=httpx.ConnectError("down"))
        assert ps.detect(gh, REPO).verdict == ps.UNKNOWN


@respx.mock
def test_an_enterprise_server_host_has_every_feature():
    client = GitHub("https://ghes.example/api/v3", "tok")
    respx.get("https://ghes.example/api/v3/repos/acme/estate").mock(
        return_value=httpx.Response(
            200, json={"visibility": "private", "owner": {"login": "acme", "type": "Organization"}}
        )
    )
    respx.get("https://ghes.example/api/v3/meta").mock(
        return_value=httpx.Response(200, json={"installed_version": "3.19.0"})
    )
    try:
        s = ps.detect(client, REPO)
    finally:
        client.close()
    assert s.verdict == ps.SUPPORTED and s.plan == "enterprise"


def test_the_explanation_names_the_features_and_the_guarantees():
    s = ps.PlanSupport(ps.UNSUPPORTED, "private", "free", ps.FREE_MISSING)
    text = "\n".join(s.explain())
    assert "required reviewers" in text and "prod gate" in text
    assert "prevent_self_review" in text and "break-glass" in text
    assert "main" in text and "ledger token" in text
    assert "public" in text and "GitHub Enterprise" in text and "#40" in text


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"protection_rules": 5},
        {"protection_rules": [{"type": "required_reviewers", "reviewers": "x"}]},
        {"protection_rules": [{"type": "wait_timer"}]},
    ],
)
def test_an_unexpected_environment_shape_counts_no_reviewer(env):
    assert ps.reviewer_count(env) == 0


def test_the_upgrade_error_is_recognised():
    assert ps.is_plan_error(GitHubError(403, UPGRADE))
    assert not ps.is_plan_error(GitHubError(403, "Resource not accessible by integration"))
    assert not ps.is_plan_error(GitHubError(404, UPGRADE))
