"""`ayasee trunk-guard` (issue #11): is main behind a pull-request rule the ledger token cannot
bypass? Anything it cannot read or does not recognise is reported as not verified, never as
protected."""

import httpx
import pytest
import respx

from ayasee import cli, trunkguard  # noqa: F401  (registers the subcommand)
from ayasee.github import GitHub, GitHubError

API = "https://api.github.com"
RULES = f"{API}/repos/acme/infra/rules/branches/main"
BRANCH = f"{API}/repos/acme/infra/branches/main"


def _branch(protected):
    return respx.get(BRANCH).mock(
        return_value=httpx.Response(200, json={"name": "main", "protected": protected})
    )


def _ruleset(rid, enforcement="active", bypass="never"):
    body = {"id": rid, "enforcement": enforcement}
    if bypass is not None:
        body["current_user_can_bypass"] = bypass
    return respx.get(f"{API}/repos/acme/infra/rulesets/{rid}").mock(
        return_value=httpx.Response(200, json=body)
    )


def _rules(*items):
    return respx.get(RULES).mock(return_value=httpx.Response(200, json=list(items)))


def _pr_rule(rid):
    return {"type": "pull_request", "ruleset_id": rid, "parameters": {}}


@pytest.fixture
def gh():
    client = GitHub(API, "ledger-tok")
    yield client
    client.close()


@respx.mock
def test_pull_request_rule_without_bypass_is_protected(gh):
    rules = _rules({"type": "deletion", "ruleset_id": 3}, _pr_rule(7))
    _ruleset(7)
    assert trunkguard.check(gh, "acme/infra") == []
    assert rules.calls.last.request.headers["Authorization"] == "Bearer ledger-tok"


@respx.mock
def test_no_pull_request_rule_and_unprotected_branch_is_definite(gh):
    _rules({"type": "deletion", "ruleset_id": 3})
    _branch(False)
    problems = trunkguard.check(gh, "acme/infra")
    assert len(problems) == 1 and problems[0].startswith("main is unprotected")


@respx.mock
def test_no_pull_request_rule_but_protected_branch_is_not_verifiable(gh):
    # classic branch protection: its bypass settings need Administration read
    _rules()
    _branch(True)
    problems = trunkguard.check(gh, "acme/infra")
    assert len(problems) == 1 and "not verifiable" in problems[0]
    assert "classic branch protection" in problems[0]


@respx.mock
@pytest.mark.parametrize("protected", [None, "true", 1])
def test_unknown_protected_value_raises(gh, protected):
    _rules()
    _branch(protected)
    with pytest.raises(GitHubError):
        trunkguard.check(gh, "acme/infra")


@respx.mock
def test_branch_api_failure_raises(gh):
    _rules()
    respx.get(BRANCH).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    with pytest.raises(GitHubError):
        trunkguard.check(gh, "acme/infra")


@respx.mock
@pytest.mark.parametrize("bypass", ["always", "pull_requests_only", "exempt"])
def test_a_bypass_for_the_token_is_a_problem(gh, bypass):
    _rules(_pr_rule(7))
    _ruleset(7, bypass=bypass)
    problems = trunkguard.check(gh, "acme/infra")
    assert len(problems) == 1 and problems[0].startswith("ruleset 7: the ledger token can bypass")
    assert f"({bypass})" in problems[0]


@respx.mock
def test_pull_requests_only_is_described_as_a_merge_bypass(gh):
    _rules(_pr_rule(7))
    _ruleset(7, bypass="pull_requests_only")
    assert trunkguard.check(gh, "acme/infra") == [
        "ruleset 7: the ledger token can bypass it when merging a pull request (pull_requests_only)"
    ]


@respx.mock
@pytest.mark.parametrize("bypass", [None, "sometimes", 1])
def test_unknown_bypass_is_not_verified(gh, bypass):
    _rules(_pr_rule(7))
    _ruleset(7, bypass=bypass)
    problems = trunkguard.check(gh, "acme/infra")
    assert len(problems) == 1 and "cannot tell" in problems[0]


@respx.mock
def test_a_ruleset_that_is_not_active_is_a_problem(gh):
    _rules(_pr_rule(7))
    _ruleset(7, enforcement="evaluate")
    assert trunkguard.check(gh, "acme/infra") == ["ruleset 7: enforcement is not active"]


@respx.mock
def test_one_ruleset_without_bypass_is_enough(gh):
    # every active ruleset applies; one the token cannot bypass already forces a pull request
    _rules(_pr_rule(7), _pr_rule(9))
    _ruleset(7, bypass="always")
    _ruleset(9)
    assert trunkguard.check(gh, "acme/infra") == []


@respx.mock
def test_an_inactive_ruleset_then_one_without_bypass_is_verified(gh):
    _rules(_pr_rule(7), _pr_rule(9))
    _ruleset(7, enforcement="evaluate")
    _ruleset(9)
    assert trunkguard.check(gh, "acme/infra") == []


@respx.mock
def test_ruleset_is_read_with_its_parents(gh):
    _rules(_pr_rule(7))
    route = _ruleset(7)
    trunkguard.check(gh, "acme/infra")
    assert route.calls.last.request.url.params["includes_parents"] == "true"


@respx.mock
def test_each_ruleset_is_read_once(gh):
    _rules(_pr_rule(7), _pr_rule(7))
    route = _ruleset(7, bypass="always")
    assert trunkguard.check(gh, "acme/infra")
    assert route.call_count == 1


@respx.mock
@pytest.mark.parametrize("rid", [None, "7", True])
def test_a_pull_request_rule_without_a_ruleset_id_raises(gh, rid):
    _rules({"type": "pull_request", "ruleset_id": rid})
    with pytest.raises(GitHubError):
        trunkguard.check(gh, "acme/infra")


@respx.mock
@pytest.mark.parametrize(
    "response", [httpx.Response(403, json={"message": "no"}), httpx.Response(200, json={})]
)
def test_rules_api_failure_raises(gh, response):
    respx.get(RULES).mock(return_value=response)
    with pytest.raises(GitHubError):
        trunkguard.check(gh, "acme/infra")


@respx.mock
def test_ruleset_api_failure_raises(gh):
    _rules(_pr_rule(7))
    respx.get(f"{API}/repos/acme/infra/rulesets/7").mock(return_value=httpx.Response(404))
    with pytest.raises(GitHubError):
        trunkguard.check(gh, "acme/infra")


# --- the command ---------------------------------------------------------------------------


@pytest.fixture
def _env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ledger-tok")
    monkeypatch.delenv("GITHUB_API_URL", raising=False)


@respx.mock
def test_command_exits_0_when_protected(_env, capsys):
    _rules(_pr_rule(7))
    _ruleset(7)
    assert cli.main(["trunk-guard", "--repo", "acme/infra"]) == 0
    assert capsys.readouterr().err == ""


@respx.mock
def test_command_exits_1_with_reasons_when_not_protected(_env, capsys):
    _rules()
    _branch(False)
    assert cli.main(["trunk-guard", "--repo", "acme/infra"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("trunk-guard: main is unprotected")


@respx.mock
def test_command_api_error_is_not_verified(_env, capsys):
    respx.get(RULES).mock(return_value=httpx.Response(500, json={"message": "boom"}))
    assert cli.main(["trunk-guard", "--repo", "acme/infra"]) == 1
    err = capsys.readouterr().err
    assert "cannot tell" in err and "ledger-tok" not in err


def test_command_missing_token_is_not_verified(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert cli.main(["trunk-guard", "--repo", "acme/infra"]) == 1
    assert "GITHUB_TOKEN" in capsys.readouterr().err


def test_bad_repo_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        cli.main(["trunk-guard", "--repo", "not a repo"])
    assert exc.value.code == 2
