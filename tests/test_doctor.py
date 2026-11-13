"""`ayasee doctor` (issue #11): one list of what the first run needs, before it runs.

The repository under test is a copy of examples/cloudflare-s3 with its callers pinned; the GitHub
API is a respx fake whose state each test bends to break one thing."""

import shutil
from pathlib import Path

import httpx
import pytest
import respx

from ayasee import cli, doctor  # noqa: F401  (registers the subcommand)

API = "https://api.github.com"
REPO = "acme/estate"
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "cloudflare-s3"
PIN = "1" * 40
PROVIDER = ("CLOUDFLARE_API_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
ENVS = (
    "test-plan",
    "prod-pr-plan",
    "test",
    "prod-plan",
    "prod",
    "prod-breakglass",
    "drift-test",
    "drift-prod",
)


@pytest.fixture
def estate(tmp_path, monkeypatch):
    root = tmp_path / "estate"
    shutil.copytree(EXAMPLE, root)
    for wf in (root / ".github" / "workflows").glob("*.yml"):
        text = wf.read_text().replace("OWNER/ayasee", "tttao/ayasee")
        wf.write_text(text.replace("0" * 40, PIN))
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    return root


def _good_state():
    """The GitHub side of a repository the bootstrap and the README steps have set up."""
    gated = {"prod", "prod-breakglass", "prod-pr-plan"}
    open_envs = {"test-plan", "prod-pr-plan"}
    envs = {}
    for name in ENVS:
        rules = []
        if name in gated:
            rules.append(
                {
                    "type": "required_reviewers",
                    "prevent_self_review": True,
                    "reviewers": [{"type": "Team", "reviewer": {"id": 101}}],
                }
            )
        envs[name] = {
            "name": name,
            "protection_rules": rules,
            "deployment_branch_policy": None
            if name in open_envs
            else {"protected_branches": False, "custom_branch_policies": True},
        }
    secrets = {name: set(PROVIDER) for name in ENVS}
    for name in ("prod-plan", "prod", "prod-breakglass"):
        secrets[name].add("AYASEE_PLAN_PASSPHRASE")
    variables = {name: {"AYASEE_LEDGER_URL"} for name in ENVS}
    return {
        "envs": envs,
        "policies": {name: ["main"] for name in ENVS},
        "secrets": secrets,
        "variables": variables,
        "repo_secrets": {"AYASEE_LEDGER_TOKEN", "TEAMS_WEBHOOK"},
        "repo_variables": set(),
        "rules": [{"type": "pull_request", "ruleset_id": 7}],
    }


def _mock(state):
    def names(items, key):
        return httpx.Response(200, json={key: [{"name": n} for n in sorted(items)]})

    respx.get(f"{API}/repos/{REPO}/environments").mock(
        return_value=httpx.Response(200, json={"environments": list(state["envs"].values())})
    )
    respx.get(f"{API}/repos/{REPO}/actions/secrets").mock(
        return_value=names(state["repo_secrets"], "secrets")
    )
    respx.get(f"{API}/repos/{REPO}/actions/variables").mock(
        return_value=names(state["repo_variables"], "variables")
    )
    respx.get(f"{API}/repos/{REPO}/rules/branches/main").mock(
        return_value=httpx.Response(200, json=state["rules"])
    )
    env_route = (
        rf"^/repos/{REPO}/environments/(?P<env>[^/]+)/"
        r"(?P<what>secrets|variables|deployment-branch-policies)$"
    )

    def per_env(request, env, what):
        if what == "secrets":
            return names(state["secrets"].get(env, set()), "secrets")
        if what == "variables":
            return names(state["variables"].get(env, set()), "variables")
        policies = [{"name": n, "type": "branch"} for n in state["policies"].get(env, [])]
        return httpx.Response(200, json={"branch_policies": policies})

    respx.route(method="GET", path__regex=env_route).mock(side_effect=per_env)


def _doctor(estate, capsys, *extra):
    rc = cli.main(["doctor", "--root", str(estate), "--repo", REPO, *extra])
    return rc, capsys.readouterr().out


def _fails(out):
    return [line.strip()[6:] for line in out.splitlines() if line.strip().startswith("FAIL")]


@respx.mock
def test_a_ready_repository_has_no_failure(estate, capsys):
    _mock(_good_state())
    rc, out = _doctor(estate, capsys)
    assert _fails(out) == [], out
    assert rc == 2  # the lock check is skipped unless asked for
    assert "environments present: test, prod-plan, prod, test-plan, prod-pr-plan" in out
    assert "environment secret(s) in place" in out and "variable(s) in place" in out
    assert "main has a pull-request ruleset" in out


def test_without_a_token_github_is_skipped(estate, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    rc, out = _doctor(estate, capsys)
    assert rc == 2 and "set GITHUB_TOKEN" in out and _fails(out) == []


def test_placeholders_and_unpinned_refs_fail(estate, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    main = estate / ".github/workflows/main.yml"
    main.write_text(main.read_text().replace(f"@{PIN}", "@v0.1.0"))
    pr = estate / ".github/workflows/pr.yml"
    pr.write_text(
        pr.read_text().replace(
            f"tttao/ayasee/.github/workflows/iac-pr.yml@{PIN}",
            "OWNER/ayasee/.github/workflows/iac-pr.yml@" + "0" * 40,
        )
    )
    nightly = estate / ".github/workflows/nightly.yml"
    nightly.write_text(nightly.read_text().replace(PIN, "2" * 40))
    rc, out = _doctor(estate, capsys)
    fails = _fails(out)
    assert rc == 1
    assert any("@v0.1.0 is not a full commit SHA" in f for f in fails)
    assert any("pr.yml" in f and "placeholder" in f for f in fails)
    assert any("pin different releases" in f for f in fails)


def test_a_paths_filter_on_the_pull_request_caller_fails(estate, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    pr = estate / ".github/workflows/pr.yml"
    pr.write_text(pr.read_text().replace("  pull_request:\n", "  pull_request:\n    paths: [x]\n"))
    _, out = _doctor(estate, capsys)
    assert any("paths: filter on pull_request" in f for f in _fails(out))


def test_missing_callers(estate, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    (estate / ".github/workflows/main.yml").unlink()
    (estate / ".github/workflows/nightly.yml").unlink()
    rc, out = _doctor(estate, capsys)
    assert rc == 1 and "no workflow calls iac-staged" in _fails(out)
    assert "warn  no workflow calls iac-nightly" in out


def test_configuration_problems(estate, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    cfg = estate / ".ayasee.yaml"
    cfg.write_text(cfg.read_text().replace("gate: always", "gate: risky"))
    shutil.rmtree(estate / "stages/test")
    _, out = _doctor(estate, capsys)
    fails = _fails(out)
    assert "gate: risky is not supported yet" in fails
    assert "no directory stages/test for stage test" in fails


def test_an_invalid_configuration_skips_github(estate, capsys):
    (estate / ".ayasee.yaml").write_text("stages: 3\n")
    rc, out = _doctor(estate, capsys)
    assert rc == 1 and "the configuration does not load" in out


@respx.mock
def test_missing_environment_and_reviewers(estate, capsys):
    state = _good_state()
    del state["envs"]["prod-breakglass"]
    state["envs"]["prod"]["protection_rules"] = []
    state["envs"]["prod-pr-plan"]["protection_rules"][0]["prevent_self_review"] = False
    _mock(state)
    _, out = _doctor(estate, capsys)
    fails = _fails(out)
    assert "environment prod-breakglass is missing (run the bootstrap)" in fails
    assert "prod: no required reviewers" in fails
    assert "prod-pr-plan: self-review is allowed but solo_mode is off" in fails


@respx.mock
def test_no_environment_claims_nothing_in_place(estate, capsys):
    state = _good_state()
    state["envs"] = {}
    _mock(state)
    _, out = _doctor(estate, capsys)
    assert "in place" not in out and len(_fails(out)) == len(ENVS)


@respx.mock
def test_branch_policies(estate, capsys):
    state = _good_state()
    state["envs"]["test"]["deployment_branch_policy"] = None
    state["policies"]["prod"] = ["main", "release/*"]
    state["envs"]["test-plan"]["deployment_branch_policy"] = {"custom_branch_policies": True}
    _mock(state)
    _, out = _doctor(estate, capsys)
    fails = _fails(out)
    assert "test: not restricted to main (deployment branch policy)" in fails
    assert "prod: deploys from ['main', 'release/*'], expected [main]" in fails
    assert "test-plan: restricted branches; pull-request plans need any" in fails


@respx.mock
def test_secrets(estate, capsys):
    state = _good_state()
    state["secrets"]["test"].discard("CLOUDFLARE_API_TOKEN")
    state["secrets"]["prod"].discard("AYASEE_PLAN_PASSPHRASE")
    state["secrets"]["drift-prod"].discard("AWS_ACCESS_KEY_ID")
    state["repo_secrets"].add("AWS_ACCESS_KEY_ID")
    state["repo_secrets"].discard("TEAMS_WEBHOOK")
    _mock(state)
    rc, out = _doctor(estate, capsys)
    fails = _fails(out)
    assert rc == 1
    assert (
        "test: secret CLOUDFLARE_API_TOKEN is missing (iac-staged: CLOUDFLARE_API_TOKEN)" in fails
    )
    assert any(f.startswith("prod: secret AYASEE_PLAN_PASSPHRASE is missing") for f in fails)
    assert (
        "drift-prod: AWS_ACCESS_KEY_ID is a repository secret; make it an environment secret"
        in (fails)
    )
    assert "secret TEAMS_WEBHOOK is not a repository secret" in out


@respx.mock
def test_the_passphrase_must_be_passed(estate, capsys):
    main = estate / ".github/workflows/main.yml"
    main.write_text(
        main.read_text().replace(
            "      AYASEE_PLAN_PASSPHRASE: ${{ secrets.AYASEE_PLAN_PASSPHRASE }}\n", ""
        )
    )
    _mock(_good_state())
    _, out = _doctor(estate, capsys)
    assert any("the plan key needs AYASEE_PLAN_PASSPHRASE; pass it" in f for f in _fails(out))


@respx.mock
def test_ledger_variable_on_the_environment_or_the_repository(estate, capsys):
    state = _good_state()
    state["variables"]["drift-test"] = set()
    state["variables"]["prod-breakglass"] = set()
    state["variables"]["test"] = set()
    state["repo_variables"] = set()
    _mock(state)
    _, out = _doctor(estate, capsys)
    fails = _fails(out)
    assert (
        "drift-test: variable AYASEE_LEDGER_URL is missing (no ledger.url in the configuration)"
        in fails
    )
    assert any(f.startswith("prod-breakglass: variable AYASEE_LEDGER_URL") for f in fails)
    assert any(f.startswith("test: variable AYASEE_LEDGER_URL") for f in fails)


@respx.mock
def test_a_repository_variable_is_enough(estate, capsys):
    state = _good_state()
    state["variables"] = {}
    state["repo_variables"] = {"AYASEE_LEDGER_URL"}
    _mock(state)
    _, out = _doctor(estate, capsys)
    assert _fails(out) == []


@respx.mock
def test_ledger_token_without_a_pull_request_ruleset(estate, capsys):
    state = _good_state()
    state["rules"] = [{"type": "deletion", "ruleset_id": 3}]
    _mock(state)
    _, out = _doctor(estate, capsys)
    assert any("no ruleset requires pull requests on main" in f for f in _fails(out))


@respx.mock
def test_an_unreadable_api_fails_without_a_traceback(estate, capsys):
    respx.get(f"{API}/repos/{REPO}/environments").mock(return_value=httpx.Response(403, json={}))
    rc, out = _doctor(estate, capsys)
    assert rc == 1
    assert any(f.startswith("cannot list the environments") for f in _fails(out))


# ---- the lock check ----------------------------------------------------------------------------

S3 = "https://s3.example.invalid"
KEY = f"{S3}/ayasee-state-test/{doctor.LOCK_KEY}"
LOCK = f"s3://ayasee-state-test?region=auto&endpoint={S3}"


@pytest.fixture
def aws(monkeypatch, estate):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ayasee-test-secret")
    monkeypatch.delenv("GITHUB_TOKEN")
    return estate


@respx.mock
def test_lock_check_ok(aws, capsys):
    put = respx.put(KEY).mock(side_effect=[httpx.Response(200), httpx.Response(412)])
    delete = respx.delete(KEY).mock(return_value=httpx.Response(204))
    _, out = _doctor(aws, capsys, "--lock-check", LOCK)
    assert "ayasee-state-test: the second conditional write was refused" in out
    assert put.call_count == 2 and delete.call_count == 1
    assert all(c.request.headers["if-none-match"] == "*" for c in put.calls)


@respx.mock
def test_lock_check_no_lock(aws, capsys):
    respx.put(KEY).mock(return_value=httpx.Response(200))
    delete = respx.delete(KEY).mock(return_value=httpx.Response(204))
    _, out = _doctor(aws, capsys, "--lock-check", LOCK)
    assert any("NO LOCK" in f for f in _fails(out)) and delete.call_count == 1


@respx.mock
def test_lock_check_leftover_object_is_not_deleted(aws, capsys):
    respx.put(KEY).mock(return_value=httpx.Response(412))
    delete = respx.delete(KEY).mock(return_value=httpx.Response(204))
    _, out = _doctor(aws, capsys, "--lock-check", LOCK)
    assert any("inconclusive" in f and "exists" in f for f in _fails(out))
    assert delete.call_count == 0


def test_lock_check_bad_url(aws, capsys):
    _, out = _doctor(aws, capsys, "--lock-check", "gs://bucket")
    assert any("expected s3://" in f for f in _fails(out))
