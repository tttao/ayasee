"""`ayasee doctor` (issue #11): one list of what the first run needs, before it runs.

The repository under test is a copy of examples/cloudflare-s3 with its callers pinned; the GitHub
API is a respx fake whose state each test bends to break one thing."""

import base64
import shutil
from pathlib import Path

import httpx
import pytest
import respx

from ayasee import __version__, cli, doctor  # noqa: F401  (registers the subcommand)

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


def _mock_pin(version=None, parents=None, status=200, head=None):
    """The pinned commit PIN of tttao/ayasee, a release commit of ``version`` (default: the CLI)."""
    version = version or __version__
    tag = "2" * 40
    text = f'__version__ = "{version}"\n'
    respx.get(f"{API}/repos/tttao/ayasee/contents/src/ayasee/__init__.py").mock(
        return_value=httpx.Response(
            status, json={"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}
        )
    )
    respx.get(f"{API}/repos/tttao/ayasee/git/ref/tags/v{version}").mock(
        return_value=httpx.Response(200, json={"object": {"sha": tag, "type": "commit"}})
    )
    respx.get(f"{API}/repos/tttao/ayasee/git/ref/heads/release/v{version}").mock(
        return_value=httpx.Response(200, json={"object": {"sha": head or PIN}})
    )
    respx.get(f"{API}/repos/tttao/ayasee/git/commits/{PIN}").mock(
        return_value=httpx.Response(200, json={"parents": [{"sha": p} for p in parents or [tag]]})
    )


def _mock(state):
    _mock_pin()

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
    main = estate / ".github/workflows/iac-staged.yml"
    main.write_text(main.read_text().replace(f"@{PIN}", "@v0.1.0"))
    pr = estate / ".github/workflows/iac-pr.yml"
    pr.write_text(
        pr.read_text().replace(
            f"tttao/ayasee/.github/workflows/iac-pr.yml@{PIN}",
            "OWNER/ayasee/.github/workflows/iac-pr.yml@" + "0" * 40,
        )
    )
    nightly = estate / ".github/workflows/iac-nightly.yml"
    nightly.write_text(nightly.read_text().replace(PIN, "2" * 40))
    rc, out = _doctor(estate, capsys)
    fails = _fails(out)
    assert rc == 1
    assert any("@v0.1.0 is not a full commit SHA" in f for f in fails)
    assert any("iac-pr.yml" in f and "placeholder" in f for f in fails)
    assert any("pin different releases" in f for f in fails)


def test_a_paths_filter_on_the_pull_request_caller_fails(estate, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    pr = estate / ".github/workflows/iac-pr.yml"
    pr.write_text(pr.read_text().replace("  pull_request:\n", "  pull_request:\n    paths: [x]\n"))
    _, out = _doctor(estate, capsys)
    assert any("paths: filter on pull_request" in f for f in _fails(out))


def test_missing_callers(estate, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    (estate / ".github/workflows/iac-staged.yml").unlink()
    (estate / ".github/workflows/iac-nightly.yml").unlink()
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
    main = estate / ".github/workflows/iac-staged.yml"
    main.write_text(
        main.read_text().replace(
            "      AYASEE_PLAN_PASSPHRASE: ${{ secrets.AYASEE_PLAN_PASSPHRASE }}\n", ""
        )
    )
    _mock(_good_state())
    _, out = _doctor(estate, capsys)
    assert any("the plan key needs AYASEE_PLAN_PASSPHRASE; pass it" in f for f in _fails(out))


def _switch_key_provider(estate, provider: str, secret: str) -> None:
    """Make the example use another key provider, passing its secret instead of the passphrase."""
    cfg = estate / ".ayasee.yaml"
    cfg.write_text(cfg.read_text().replace("key_provider: pbkdf2", f"key_provider: {provider}"))
    for name in ("iac-staged.yml", "iac-breakglass.yml"):
        wf = estate / ".github/workflows" / name
        wf.write_text(
            wf.read_text().replace(
                "AYASEE_PLAN_PASSPHRASE: ${{ secrets.AYASEE_PLAN_PASSPHRASE }}",
                f"{secret}: ${{{{ secrets.{secret} }}}}",
            )
        )


@pytest.mark.parametrize(
    ("provider", "secret", "variable"),
    [
        ("gcp_kms", "AYASEE_GCP_KMS_CREDENTIALS", "AYASEE_GCP_KMS_KEY_NAME"),
        ("openbao", "AYASEE_OPENBAO_TOKEN", "AYASEE_OPENBAO_KEY_URL"),
    ],
)
@respx.mock
def test_kms_key_providers(estate, capsys, provider, secret, variable):
    """issue #10: the key credential is an environment secret of the three key environments
    only, and the key variable is set where the plan and apply steps read it."""
    _switch_key_provider(estate, provider, secret)
    state = _good_state()
    for env in ("prod-plan", "prod", "prod-breakglass"):
        state["secrets"][env].discard("AYASEE_PLAN_PASSPHRASE")
        state["secrets"][env].add(secret)
        state["variables"][env].add(variable)
    _mock(state)
    _, out = _doctor(estate, capsys)
    assert _fails(out) == [], out

    state["secrets"]["prod"].discard(secret)
    state["repo_secrets"].add(secret)
    state["variables"]["prod-breakglass"].discard(variable)
    _mock(state)
    _, out = _doctor(estate, capsys)
    fails = _fails(out)
    assert f"prod: {secret} is a repository secret; make it an environment secret" in fails
    assert f"prod-breakglass: variable {variable} is missing (key_provider {provider})" in fails
    # pre-prod environments never need the key credential
    assert not any(f.startswith(("test:", "test-plan:")) and secret in f for f in fails)


@respx.mock
def test_a_kms_key_credential_must_be_passed(estate, capsys):
    _switch_key_provider(estate, "openbao", "AYASEE_OPENBAO_TOKEN")
    main = estate / ".github/workflows/iac-staged.yml"
    main.write_text(
        main.read_text().replace(
            "      AYASEE_OPENBAO_TOKEN: ${{ secrets.AYASEE_OPENBAO_TOKEN }}\n", ""
        )
    )
    _mock(_good_state())
    _, out = _doctor(estate, capsys)
    assert any("the plan key needs AYASEE_OPENBAO_TOKEN; pass it" in f for f in _fails(out))


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
    _mock_pin()
    respx.get(f"{API}/repos/{REPO}/environments").mock(return_value=httpx.Response(403, json={}))
    rc, out = _doctor(estate, capsys)
    assert rc == 1
    assert any(f.startswith("cannot list the environments") for f in _fails(out))


# ---- the release pin (issue #32) ---------------------------------------------------------------


def _pin_lines(out):
    sec = out.split("release pin\n", 1)[1].split("\n\n", 1)[0]
    return [line.strip() for line in sec.splitlines()]


@respx.mock
def test_a_pin_of_this_release_passes(estate, capsys):
    _mock(_good_state())
    rc, out = _doctor(estate, capsys)
    assert all(line.startswith("ok") for line in _pin_lines(out)), out
    assert f"release commit of {__version__}" in out


@respx.mock
def test_a_pin_older_than_the_cli_fails(estate, capsys):
    # the issue's example: callers on the rc.2 commit, CLI rc.3
    _mock(_good_state())
    _mock_pin("0.0.1")
    rc, out = _doctor(estate, capsys)
    assert rc == 1
    assert any("older than this CLI" in f for f in _fails(out)), out


@respx.mock
def test_a_pin_newer_than_the_cli_passes(estate, capsys):
    _mock(_good_state())
    _mock_pin("99.0.0")
    rc, out = _doctor(estate, capsys)
    assert not any("release commit" in f for f in _fails(out)), out


@respx.mock
def test_a_pin_that_is_not_a_release_commit_fails(estate, capsys):
    _mock(_good_state())
    _mock_pin(parents=["3" * 40, "4" * 40])
    rc, out = _doctor(estate, capsys)
    assert rc == 1 and any("one commit on top of tag" in f for f in _fails(out)), out


@respx.mock
def test_a_pin_that_is_not_the_release_branch_head_fails(estate, capsys):
    _mock(_good_state())
    _mock_pin(head="5" * 40)  # e.g. a fork's commit on top of the tag
    rc, out = _doctor(estate, capsys)
    assert rc == 1 and any("not the head of release/v" in f for f in _fails(out)), out


@respx.mock
def test_an_api_error_on_the_pin_is_could_not_check_not_a_pass(estate, capsys):
    _mock(_good_state())
    _mock_pin(status=500)
    rc, out = _doctor(estate, capsys)
    assert any(
        line.startswith("skip") and "could not check the release" in line
        for line in _pin_lines(out)
    ), out
    assert not any(line.startswith("ok") for line in _pin_lines(out))
    assert rc == 2


def test_the_pin_is_not_checked_without_a_token(estate, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    rc, out = _doctor(estate, capsys)
    assert "set GITHUB_TOKEN to check the pinned release" in out


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


LEDGER_KEY = ("AYASEE_LEDGER_AWS_ACCESS_KEY_ID", "AYASEE_LEDGER_AWS_SECRET_ACCESS_KEY")


def _pass_ledger_key(
    estate, files=("iac-staged.yml", "iac-nightly.yml", "iac-breakglass.yml"), names=LEDGER_KEY
):
    for f in files:
        wf = estate / ".github/workflows" / f
        extra = "".join(f"      {n}: ${{{{ secrets.{n} }}}}\n" for n in names)
        wf.write_text(wf.read_text().rstrip("\n") + "\n" + extra)


@respx.mock
def test_the_s3_ledger_key_is_an_environment_secret_of_each_ledger_environment(estate, capsys):
    """Issue #10: the guard and ledger steps of every stage, drift and break-glass environment
    read the dedicated ledger key; the pull-request environments never do."""
    _pass_ledger_key(estate)
    state = _good_state()
    for env in ("test", "prod-plan", "prod", "drift-test", "drift-prod", "prod-breakglass"):
        state["secrets"][env] |= set(LEDGER_KEY)
    _mock(state)
    _, out = _doctor(estate, capsys)
    assert _fails(out) == [], out

    state["secrets"]["drift-prod"].discard(LEDGER_KEY[1])
    state["secrets"]["prod-plan"].discard(LEDGER_KEY[0])
    state["repo_secrets"].add(LEDGER_KEY[0])
    respx.clear()
    _mock(state)
    _, out = _doctor(estate, capsys)
    fails = _fails(out)
    assert f"drift-prod: secret {LEDGER_KEY[1]} is missing (iac-nightly: {LEDGER_KEY[1]})" in fails
    assert (
        f"prod-plan: {LEDGER_KEY[0]} is a repository secret; make it an environment secret" in fails
    )
    assert not any(f.startswith(("test-plan:", "prod-pr-plan:")) for f in fails), fails


@respx.mock
def test_the_s3_ledger_key_goes_in_pairs(estate, capsys):
    _pass_ledger_key(estate, files=("iac-staged.yml",), names=LEDGER_KEY[:1])
    state = _good_state()
    for env in ("test", "prod-plan", "prod"):
        state["secrets"][env].add(LEDGER_KEY[0])
    _mock(state)
    _, out = _doctor(estate, capsys)
    assert (
        f".github/workflows/iac-staged.yml: {LEDGER_KEY[0]} is passed without {LEDGER_KEY[1]}"
        in _fails(out)
    )


@respx.mock
def test_an_s3_ledger_url_needs_the_ledger_key(estate, capsys):
    cfg = estate / ".ayasee.yaml"
    cfg.write_text(cfg.read_text() + "ledger:\n  url: s3://acme-ledger?region=eu-west-3\n")
    _mock(_good_state())
    _, out = _doctor(estate, capsys)
    fails = _fails(out)
    for f in ("iac-staged.yml", "iac-nightly.yml", "iac-breakglass.yml"):
        assert any(
            x.startswith(f".github/workflows/{f}: ledger.url is s3://, but the ledger key")
            for x in fails
        ), fails
    assert not any("iac-pr.yml: ledger.url" in x for x in fails)


def test_the_bootstrap_module_lists_the_plan_key_secrets_doctor_checks():
    """issue #10: key_secret_scopes of modules/github-environments, per key provider, names the
    same secrets as PLAN_KEY_SECRETS, and its key_provider validation the same providers."""
    import re

    from ayasee.config import KEY_PROVIDERS

    module = Path(__file__).resolve().parents[1] / "modules" / "github-environments"
    main = (module / "main.tf").read_text()
    block = main[main.index("key_secret_scopes = {") : main.index("pending_secrets")]
    found = {}
    for provider in KEY_PROVIDERS:
        m = re.search(
            rf"^    {provider} = \{{\}}$|^    {provider} = (.*?)^    \}}", block, re.M | re.S
        )
        assert m, provider
        found[provider] = set(re.findall(r"^\s+(AYASEE_[A-Z_]+)\s+=", m.group(0), re.M))
    assert found == {p: set(doctor.PLAN_KEY_SECRETS.get(p, ())) for p in KEY_PROVIDERS}
    variables = (module / "variables.tf").read_text()
    listed = ", ".join(f'"{p}"' for p in KEY_PROVIDERS)
    assert f"contains([{listed}], var.key_provider)" in variables


# ---- environment_prefix (issue #31) ------------------------------------------------------


def _prefixed(state, prefix):
    """The same GitHub side under prefixed names, every environment marked as the bootstrap's."""
    out = dict(state)
    for key in ("envs", "policies", "secrets", "variables"):
        out[key] = {prefix + k: v for k, v in state[key].items()}
    for name, env in out["envs"].items():
        env["name"] = name
    out["variables"] = {k: {*v, "AYASEE_MANAGED"} for k, v in out["variables"].items()}
    return out


def _with_prefix(estate, prefix="iac-"):
    cfg = estate / ".ayasee.yaml"
    cfg.write_text(cfg.read_text() + f"\nenvironment_prefix: {prefix}\n")


@respx.mock
def test_a_prefixed_repository_is_checked_under_the_prefixed_names(estate, capsys):
    _with_prefix(estate)
    _mock(_prefixed(_good_state(), "iac-"))
    rc, out = _doctor(estate, capsys)
    assert _fails(out) == [], out
    assert "environments present: iac-test, iac-prod-plan, iac-prod, iac-test-plan" in out
    assert "not created by the ayasee bootstrap" not in out  # every environment is marked
    assert "variable(s) in place" in out and "environment secret(s) in place" in out


@respx.mock
def test_the_application_environments_do_not_satisfy_a_prefixed_repository(estate, capsys):
    """With a prefix, the unprefixed `prod` (with reviewers) is not iac-prod: it is missing."""
    _with_prefix(estate)
    _mock(_good_state())  # test, prod, prod-breakglass ... exist, no iac- ones
    _, out = _doctor(estate, capsys)
    fails = _fails(out)
    assert "environment iac-prod is missing (run the bootstrap)" in fails
    assert "environment iac-prod-breakglass is missing (run the bootstrap)" in fails
    assert not any(f.startswith("prod:") for f in fails)  # the unprefixed ones are not judged


@respx.mock
@pytest.mark.parametrize("gated", ["prod", "prod-breakglass", "prod-pr-plan"])
def test_the_reviewer_checks_follow_the_prefix(estate, capsys, gated):
    """The gate keyed on a literal name would pass iac-prod with no reviewers: it must not."""
    _with_prefix(estate)
    state = _prefixed(_good_state(), "iac-")
    state["envs"][f"iac-{gated}"]["protection_rules"] = []
    _mock(state)
    _, out = _doctor(estate, capsys)
    assert f"iac-{gated}: no required reviewers" in _fails(out)


@respx.mock
def test_the_main_only_and_pr_plan_rules_follow_the_prefix(estate, capsys):
    _with_prefix(estate)
    state = _prefixed(_good_state(), "iac-")
    state["envs"]["iac-prod-plan"]["deployment_branch_policy"] = None
    state["envs"]["iac-test-plan"]["deployment_branch_policy"] = {"custom_branch_policies": True}
    _mock(state)
    _, out = _doctor(estate, capsys)
    fails = _fails(out)
    assert "iac-prod-plan: not restricted to main (deployment branch policy)" in fails
    assert "iac-test-plan: restricted branches; pull-request plans need any" in fails


@respx.mock
def test_a_solo_maintainer_prefixed_prod_that_prevents_self_review_warns(estate, capsys):
    _with_prefix(estate)
    cfg = estate / ".ayasee.yaml"
    cfg.write_text(cfg.read_text() + "solo_mode: true\n")
    _mock(_prefixed(_good_state(), "iac-"))
    _, out = _doctor(estate, capsys)
    assert "iac-prod: prevents self-review, so a solo maintainer cannot approve" in out


@respx.mock
def test_secrets_and_variables_are_looked_up_on_the_prefixed_environment(estate, capsys):
    _with_prefix(estate)
    state = _prefixed(_good_state(), "iac-")
    state["secrets"]["iac-prod"].discard("CLOUDFLARE_API_TOKEN")
    state["variables"]["iac-prod-breakglass"].discard("AYASEE_LEDGER_URL")
    _mock(state)
    _, out = _doctor(estate, capsys)
    fails = _fails(out)
    assert any(f.startswith("iac-prod: secret CLOUDFLARE_API_TOKEN is missing") for f in fails)
    assert any(f.startswith("iac-prod-breakglass: variable AYASEE_LEDGER_URL") for f in fails)


@respx.mock
def test_environments_without_the_marker_are_listed_once(estate, capsys):
    state = _good_state()
    state["variables"]["prod"].add("AYASEE_MANAGED")
    _mock(state)
    _, out = _doctor(estate, capsys)
    line = next(ln for ln in out.splitlines() if "not created by the ayasee bootstrap" in ln)
    assert line.strip().startswith("warn")
    assert "AYASEE_MANAGED" in line and " prod," not in line and "test-plan" in line
    assert out.count("not created by the ayasee bootstrap") == 1


def _application_workflow(estate, name, env, job="deploy"):
    (estate / ".github" / "workflows" / name).write_text(
        f"name: {name}\non: push\njobs:\n  {job}:\n    runs-on: ubuntu-latest\n"
        f"    environment: {env}\n    steps:\n      - run: echo deploy\n"
    )


def test_a_workflow_that_is_not_an_ayasee_caller_using_an_ayasee_environment_warns(estate, capsys):
    _application_workflow(estate, "deploy-test.yml", "test")
    _application_workflow(estate, "deploy-prod.yml", "{name: prod, url: https://x.example}")
    _application_workflow(estate, "deploy-other.yml", "staging")
    _application_workflow(estate, "deploy-dynamic.yml", "${{ inputs.target }}")
    rc, out = _doctor(estate, capsys)
    warns = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("warn")]
    shared = [w for w in warns if "is also used by" in w]
    assert len(shared) == 2, out
    assert any(
        w.startswith("warn  test is also used by .github/workflows/deploy-test.yml (job deploy)")
        for w in shared
    )
    assert any(".github/workflows/deploy-prod.yml" in w for w in shared)
    assert all("environment_prefix" in w for w in shared)
    assert "staging" not in out and "deploy-dynamic" not in out


def test_the_shared_environment_check_needs_no_token(estate, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    _application_workflow(estate, "deploy-test.yml", "test")
    _, out = _doctor(estate, capsys)
    assert "test is also used by .github/workflows/deploy-test.yml" in out


def test_a_prefix_ends_the_sharing(estate, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    _application_workflow(estate, "deploy-test.yml", "test")
    _application_workflow(estate, "deploy-prod.yml", "prod")
    _with_prefix(estate)
    _, out = _doctor(estate, capsys)
    assert "is also used by" not in out
    assert "no other workflow uses iac-* environments" in out


def test_a_workflow_using_the_prefixed_environment_still_warns(estate, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    _with_prefix(estate)
    _application_workflow(estate, "oops.yml", "iac-prod")
    _, out = _doctor(estate, capsys)
    assert "iac-prod is also used by .github/workflows/oops.yml (job deploy)" in out


def test_the_ayasee_callers_themselves_are_not_reported(estate, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    _, out = _doctor(estate, capsys)
    assert "is also used by" not in out
