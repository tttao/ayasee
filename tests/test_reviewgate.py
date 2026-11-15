"""`ayasee require-reviewers` (issue #39): the staged run refuses to plan prod, and the break-glass
run to start, when the gated environment would not wait for an approval. Anything it cannot read
is a refusal."""

import httpx
import pytest
import respx

from ayasee import cli, reviewgate  # noqa: F401  (registers the subcommand)
from ayasee.github import GitHub

API = "https://api.github.com"
REPO = "acme/estate"
ENV_URL = f"{API}/repos/{REPO}/environments/iac-prod"
UPGRADE = "Upgrade to GitHub Pro or make this repository public to enable this feature."


@pytest.fixture
def gh():
    client = GitHub(API, "tok")
    yield client
    client.close()


def _env(reviewers=1, status=200, name="iac-prod"):
    rules = []
    if reviewers is not None:
        rules.append(
            {
                "type": "required_reviewers",
                "prevent_self_review": True,
                "reviewers": [{"type": "Team"}] * reviewers,
            }
        )
    body = {"name": name, "protection_rules": rules} if status == 200 else {"message": "x"}
    return respx.get(f"{API}/repos/{REPO}/environments/{name}").mock(
        return_value=httpx.Response(status, json=body)
    )


def _repo(visibility="public"):
    respx.get(f"{API}/repos/{REPO}").mock(
        return_value=httpx.Response(
            200, json={"visibility": visibility, "owner": {"login": "acme", "type": "Organization"}}
        )
    )


@respx.mock
def test_an_environment_with_reviewers_passes(gh):
    _env()
    _repo()
    assert reviewgate.check(gh, REPO, "iac-prod") == ([], [])


@respx.mock
@pytest.mark.parametrize("reviewers", [None, 0])
def test_no_required_reviewers_is_refused(gh, reviewers):
    _env(reviewers)
    _repo()
    problems, _ = reviewgate.check(gh, REPO, "iac-prod")
    assert len(problems) == 1 and "iac-prod has no required reviewers" in problems[0]
    assert "without an approval" in problems[0]


@respx.mock
def test_a_missing_environment_is_refused(gh):
    _env(status=404)
    problems, _ = reviewgate.check(gh, REPO, "iac-prod")
    assert "does not exist" in problems[0]


@respx.mock
def test_an_unreadable_environment_is_refused(gh):
    _env(status=403)
    problems, _ = reviewgate.check(gh, REPO, "iac-prod")
    assert problems and problems[0].startswith("cannot read the environment iac-prod")


def test_github_unreachable_is_refused(gh):
    with respx.mock:
        respx.get(ENV_URL).mock(side_effect=httpx.ConnectError("down"))
        problems, _ = reviewgate.check(gh, REPO, "iac-prod")
    assert problems and "cannot read" in problems[0]


@respx.mock
def test_reviewers_on_a_free_private_repository_are_refused(gh):
    """Rules stored while the repository was public are ignored once it is private on Free."""
    _env()
    _repo("private")
    respx.get(f"{API}/orgs/acme").mock(return_value=httpx.Response(200, json={"login": "acme"}))
    respx.get(f"{API}/repos/{REPO}/rulesets").mock(
        return_value=httpx.Response(403, json={"message": UPGRADE})
    )
    problems, _ = reviewgate.check(gh, REPO, "iac-prod")
    assert len(problems) == 1 and "does not have" in problems[0] and "reviewers" in problems[0]


@respx.mock
def test_an_unknown_plan_with_reviewers_passes_with_a_note(gh):
    """The run's token cannot read an organization's plan; the reviewers GitHub reports stand."""
    _env()
    _repo("private")
    respx.get(f"{API}/orgs/acme").mock(return_value=httpx.Response(200, json={"login": "acme"}))
    respx.get(f"{API}/repos/{REPO}/rulesets").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{API}/repos/{REPO}/environments").mock(
        return_value=httpx.Response(403, json={"message": "Resource not accessible"})
    )
    problems, notes = reviewgate.check(gh, REPO, "iac-prod")
    assert problems == [] and len(notes) == 1 and "cannot confirm" in notes[0]


@respx.mock
def test_reviewers_seen_on_a_private_repository_need_no_note(gh):
    _env()
    _repo("private")
    respx.get(f"{API}/orgs/acme").mock(return_value=httpx.Response(200, json={"login": "acme"}))
    respx.get(f"{API}/repos/{REPO}/rulesets").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{API}/repos/{REPO}/environments").mock(
        return_value=httpx.Response(
            200,
            json={
                "environments": [
                    {
                        "name": "iac-prod",
                        "protection_rules": [
                            {"type": "required_reviewers", "reviewers": [{"type": "Team"}]}
                        ],
                    }
                ]
            },
        )
    )
    assert reviewgate.check(gh, REPO, "iac-prod") == ([], [])


@respx.mock
def test_the_command_exits_1_and_names_the_environment(capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok-sentinel-value")
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    _env(None)
    _repo()
    rc = cli.main(["require-reviewers", "--repo", REPO, "--environment", "iac-prod"])
    err = capsys.readouterr().err
    assert rc == 1 and err.startswith("require-reviewers: iac-prod has no required reviewers")
    assert "tok-sentinel-value" not in err


@respx.mock
def test_the_command_exits_0_when_gated(capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    _env()
    _repo()
    assert cli.main(["require-reviewers", "--repo", REPO, "--environment", "iac-prod"]) == 0


def test_the_command_without_a_token_refuses(capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    rc = cli.main(["require-reviewers", "--repo", REPO, "--environment", "iac-prod"])
    assert rc == 1 and "GITHUB_TOKEN" in capsys.readouterr().err
