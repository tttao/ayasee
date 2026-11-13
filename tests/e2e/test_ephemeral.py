"""The ephemeral sandbox: rewriting the real workflows, and `up`/`down` against a fake gh."""

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from e2e.ephemeral import (
    TOPIC,
    WORKFLOWS,
    Ephemeral,
    EphemeralError,
    add_dependent_units,
    build_source,
    build_workflows,
    cloudless_workflow,
    pin_workflow,
)
from e2e.sandbox import NotASandbox

from ayasee.config import load_config
from e2e import ephemeral

ROOT = Path(__file__).resolve().parents[2]
REPO = "alice/ayasee-demo-sandbox"
SHA = "a" * 40


def _src(name: str) -> str:
    return (ROOT / ".github" / "workflows" / f"{name}.yml").read_text()


def _steps(wf: dict):
    for job in wf["jobs"].values():
        yield from job.get("steps", [])


@pytest.mark.parametrize("name", WORKFLOWS)
def test_pinned_workflows_reference_only_the_sandbox_and_pinned_third_parties(name):
    text = pin_workflow(_src(name), REPO, SHA)
    assert "OWNER/" not in text
    for step in _steps(yaml.safe_load(text)):
        uses = step.get("uses")
        if uses is None:
            continue
        if "/ayasee-demo-sandbox/" in uses:
            assert uses.startswith(f"{REPO}/actions/") and uses.endswith(f"@{SHA}")
        else:
            assert len(uses.split("@")[1]) == 40, uses


@pytest.mark.parametrize("name", WORKFLOWS)
def test_cloudless_workflows_have_no_azure_login_and_stay_valid(name):
    before = yaml.safe_load(_src(name))
    after = yaml.safe_load(cloudless_workflow(_src(name)))
    assert "azure/login" not in json.dumps(after)
    assert set(after["jobs"]) == set(before["jobs"])
    for job, spec in before["jobs"].items():
        kept = [s for s in spec.get("steps", []) if "azure/login" not in s.get("uses", "")]
        assert after["jobs"][job].get("steps", []) == [
            _with_passphrase(s) if "AYASEE_EXTRA_SECRET" in s.get("env", {}) else s for s in kept
        ]


def _with_passphrase(step: dict) -> dict:
    return {
        **step,
        "env": {**step["env"], "AYASEE_TEST_PASSPHRASE": "${{ secrets.plan-passphrase }}"},
    }


@pytest.mark.parametrize("name", WORKFLOWS)
def test_every_encrypted_plan_or_apply_gets_the_passphrase(name):
    wf = yaml.safe_load(cloudless_workflow(_src(name)))
    encrypted = [s for s in _steps(wf) if s.get("with", {}).get("encrypt") == "true"]
    for step in encrypted:
        assert step["env"]["AYASEE_TEST_PASSPHRASE"] == "${{ secrets.plan-passphrase }}"
    if encrypted:
        declared = wf[True]["workflow_call"]["secrets"]
        assert declared["plan-passphrase"]["required"] is False


def test_rewrites_fail_closed_when_the_markers_are_gone():
    with pytest.raises(EphemeralError):
        pin_workflow("jobs: {}\n", REPO, SHA)
    with pytest.raises(EphemeralError):
        cloudless_workflow("jobs: {}\n")


def test_built_sandbox_is_self_contained(tmp_path):
    build_source(tmp_path)
    assert not (tmp_path / ".github").exists()  # commit 1 starts no workflow
    for d in ("actions", "src", "policy", "rules", "stages", "scripts"):
        assert (tmp_path / d).is_dir()
    # grants read base's outputs: they land in a later run (R52)
    assert sorted(p.name for p in (tmp_path / "stages").glob("*/*")) == ["base"] * 3
    add_dependent_units(tmp_path)
    for stage in ("dev", "uat", "prod"):
        assert (tmp_path / "stages" / stage / "grants" / "terragrunt.hcl").is_file()
    build_workflows(tmp_path, REPO, SHA)
    wf = tmp_path / ".github" / "workflows"
    assert sorted(p.stem for p in wf.glob("*.yml")) == sorted(
        [*WORKFLOWS, "pr", "main", "nightly", "breakglass"]
    )
    for caller, target in [
        ("pr", "iac-pr"),
        ("main", "iac-staged"),
        ("nightly", "iac-nightly"),
        ("breakglass", "iac-breakglass"),
    ]:
        job = next(iter(yaml.safe_load((wf / f"{caller}.yml").read_text())["jobs"].values()))
        assert job["uses"] == f"./.github/workflows/{target}.yml"
        passed = job.get("secrets", {})
        assert ("plan-passphrase" in passed) == (caller != "pr")
        if caller != "pr":
            assert passed["plan-passphrase"] == "${{ secrets.PLAN_PASSPHRASE }}"
    for text in (p.read_text() for p in wf.glob("*.yml")):
        assert "OWNER/" not in text and "0" * 40 not in text and "azure/login" not in text
    cfg = load_config(tmp_path / ".ayasee.yaml")
    assert "/home/runner/work/ayasee-demo-sandbox/ayasee-demo-sandbox?" in cfg.ledger.url
    baseline = json.loads((tmp_path / ".ayasee" / "checkov.baseline").read_text())
    accepted = {f["file"]: f["findings"] for f in baseline["failed_checks"]}
    assert set(accepted) == {
        "/.github/workflows/breakglass.yml",
        "/.github/workflows/iac-breakglass.yml",
    }
    for findings in accepted.values():
        assert [x["check_ids"] for x in findings] == [["CKV_GHA_7"]]


class FakeGh:
    """Records gh calls; runs git for real except for the push. Each pushed commit gets a staged
    run that waits at the prod gate until approved (after ``failures`` failed attempts that
    each need a re-run)."""

    def __init__(self, topics=(TOPIC,), delete_fails="", failures=0):
        self.calls: list[tuple[list[str], str | None]] = []
        self.topics = list(topics)
        self.delete_fails = delete_fails
        self.failures = failures
        self.runs: dict[int, dict] = {}

    def _run_api(self, method, path):
        m = re.search(r"/actions/runs\?head_sha=(\w+)&event=push", path)
        if m:
            run = next((r for r in self.runs.values() if r["head_sha"] == m[1]), None)
            if run is None:
                run = {"id": len(self.runs) + 1, "head_sha": m[1], "html_url": "u", "failures": 0}
                run["path"] = ".github/workflows/main.yml"
                run.update(status="completed" if self.failures else "waiting")
                run.update(conclusion="failure" if self.failures else None)
                self.runs[run["id"]] = run
            other = {**run, "id": 0, "path": ".github/workflows/pr.yml"}
            return {"workflow_runs": [other, run]}
        m = re.search(r"/actions/runs/(\d+)(/[a-z_-]+)?", path)
        run = self.runs[int(m[1])]
        if m[2] is None:
            return run
        if m[2] == "/pending_deployments" and method == "GET":
            gated = run["status"] == "waiting"
            return [{"environment": {"id": 99, "name": "prod"}}] if gated else []
        if m[2] == "/pending_deployments":
            run.update(status="completed", conclusion="success")
        elif m[2] == "/jobs":
            return {"jobs": [{"name": "staged / dev", "conclusion": "failure"}]}
        elif m[2] == "/rerun-failed-jobs":
            run["failures"] += 1
            if run["failures"] < self.failures:
                run.update(status="completed", conclusion="failure")
            else:
                run.update(status="waiting", conclusion=None)
        return None

    def __call__(self, cmd, *, input=None, cwd=None, env=None):
        cmd = list(cmd)
        self.calls.append((cmd, input))
        if cmd[0] == "git" and "push" not in cmd:
            return subprocess.run(
                cmd, input=input, capture_output=True, text=True, env=_git_env(env)
            )
        out = ""
        if cmd[:2] == ["gh", "api"]:
            path = cmd[4]
            if path == "/user":
                out = json.dumps({"login": "alice"})
            elif path.startswith("/users/"):
                out = json.dumps({"id": 7})
            elif path.endswith("/topics") and cmd[3] == "GET":
                out = json.dumps({"names": self.topics})
            elif "/actions/" in path:
                result = self._run_api(cmd[3], path)
                out = "" if result is None else json.dumps(result)
        elif cmd[:3] == ["gh", "auth", "token"]:
            out = "gho_secret\n"
        elif cmd[:3] == ["gh", "repo", "delete"] and self.delete_fails:
            return subprocess.CompletedProcess(cmd, 1, "", self.delete_fails)
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def gh(self):
        return [(c, i) for c, i in self.calls if c[0] == "gh"]


def _gh_or_push(fake):
    return [
        (["git", "push", "", "", ""] if c[0] == "git" else c, i)
        for c, i in fake.calls
        if c[0] == "gh" or (c[0] == "git" and "push" in c)
    ]


def _git_env(env):
    import os

    return {**os.environ, **(env or {})}


def _up(fake, tmp_path, **kw):
    args = dict(
        name="ayasee-demo-sandbox",
        reviewers=[],
        private=False,
        prevent_self_review=False,
        contents_token="tok-123",
        use_gh_token=False,
        teams_webhook=None,
        approve=True,
        keep_workdir=tmp_path / "work",
    )
    args.update(kw)
    return Ephemeral(fake, sleep=lambda s: None).up(**args)


def test_up_creates_configures_then_pushes_the_workflows_last(tmp_path):
    fake = FakeGh()
    assert _up(fake, tmp_path) == REPO
    order = [
        "git push" if c[0] == "git" else " ".join(c[:5])
        for c, _ in fake.calls
        if c[0] == "gh" or (c[0] == "git" and "push" in c)
    ]
    create = next(i for i, c in enumerate(order) if c.startswith("gh repo create"))
    pushes = [i for i, c in enumerate(order) if c == "git push"]
    envs = [i for i, c in enumerate(order) if "/environments/" in c]
    sets = [i for i, c in enumerate(order) if c.startswith("gh secret set")]
    assert create < pushes[0] < min(envs) and max(envs) < pushes[1] and max(sets) < pushes[1]
    assert len(pushes) == 3
    # the dependent units are pushed only once the first staged run succeeded
    first_run_done = next(
        i for i, (c, _) in enumerate(_gh_or_push(fake)) if c[3] == "POST" and "pending" in c[4]
    )
    assert pushes[1] < first_run_done < pushes[2]
    # the second commit pins the first one
    log = subprocess.run(
        ["git", "-C", str(tmp_path / "work"), "log", "--format=%H %s"],
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    first = log[2].split()[0]
    assert log[1].endswith(f"pinned to {first[:12]}")
    assert "dependent units" in log[0]
    staged = (tmp_path / "work" / ".github" / "workflows" / "iac-staged.yml").read_text()
    assert f"{REPO}/actions/setup@{first}" in staged


def test_up_passes_secret_values_on_stdin_only(tmp_path):
    fake = FakeGh()
    _up(fake, tmp_path, teams_webhook="https://echo.example/x")
    secrets = {c[3]: i for c, i in fake.gh() if c[:3] == ["gh", "secret", "set"]}
    assert set(secrets) == {"LEDGER_TOKEN", "STATE_TOKEN", "PLAN_PASSPHRASE", "TEAMS_WEBHOOK"}
    assert secrets["LEDGER_TOKEN"] == secrets["STATE_TOKEN"] == "tok-123"
    assert len(secrets["PLAN_PASSPHRASE"]) >= 16
    for cmd, _ in fake.calls:
        assert not any("tok-123" in c or secrets["PLAN_PASSPHRASE"] in c for c in cmd)


def test_up_gates_prod_and_breakglass_and_restricts_to_main(tmp_path):
    fake = FakeGh()
    _up(fake, tmp_path, reviewers=["bob"], prevent_self_review=True)
    bodies = {
        c[4].rsplit("/", 1)[1]: json.loads(i)
        for c, i in fake.gh()
        if c[:4] == ["gh", "api", "-X", "PUT"] and "/environments/" in c[4]
    }
    assert set(bodies) == set(ephemeral.ENVIRONMENTS)
    # the fixture's first stage names the pull-request plan environment (issue #11)
    stages = load_config(ephemeral.FIXTURE / ".ayasee.yaml").stages
    assert list(ephemeral.STAGES) == stages and ephemeral.PR_PLAN == f"{stages[0]}-plan"
    for env, body in bodies.items():
        gated = env in ("prod", "prod-breakglass")
        assert body["reviewers"] == ([{"type": "User", "id": 7}] if gated else [])
        assert body["prevent_self_review"] is True
        assert (body["deployment_branch_policy"] is None) == (env == ephemeral.PR_PLAN)
    policies = [c[4] for c, _ in fake.gh() if c[4].endswith("/deployment-branch-policies")]
    assert len(policies) == len(ephemeral.ENVIRONMENTS) - 1


def test_up_sets_the_ledger_url_on_the_breakglass_environment(tmp_path):
    # iac-breakglass takes the ledger URL from vars.AYASEE_LEDGER_URL only (no config fallback)
    fake = FakeGh()
    _up(fake, tmp_path)
    variables = [
        (c[4], json.loads(i))
        for c, i in fake.gh()
        if c[:2] == ["gh", "api"] and c[4].endswith("/variables")
    ]
    name = REPO.split("/", 1)[1]
    assert variables == [
        (
            f"/repos/{REPO}/environments/prod-breakglass/variables",
            {
                "name": "AYASEE_LEDGER_URL",
                "value": f"git-branch:///home/runner/work/{name}/{name}"
                "?branch=ayasee-ledger&remote=origin",
            },
        )
    ]
    config = (tmp_path / "work" / ".ayasee.yaml").read_text()
    assert f"url: {variables[0][1]['value']}\n" in config


def test_up_needs_an_explicit_token_source(tmp_path):
    fake = FakeGh()
    with pytest.raises(EphemeralError, match="SANDBOX_CONTENTS_TOKEN"):
        _up(fake, tmp_path, contents_token=None)
    assert not any(c[:3] == ["gh", "repo", "create"] for c, _ in fake.calls)
    fake = FakeGh()
    _up(fake, tmp_path / "b", contents_token=None, use_gh_token=True)
    secrets = {c[3]: i for c, i in fake.gh() if c[:3] == ["gh", "secret", "set"]}
    assert secrets["LEDGER_TOKEN"] == "gho_secret"


def test_up_refuses_a_name_that_is_not_a_sandbox(tmp_path):
    with pytest.raises(NotASandbox):
        _up(FakeGh(), tmp_path, name="ayasee")


def test_down_deletes_only_a_marked_sandbox():
    fake = FakeGh()
    Ephemeral(fake).down(REPO)
    assert (["gh", "repo", "delete", REPO, "--yes"], None) in fake.calls
    fake = FakeGh(topics=["other"])
    with pytest.raises(EphemeralError, match="refusing"):
        Ephemeral(fake).down(REPO)
    assert not any(c[:3] == ["gh", "repo", "delete"] for c, _ in fake.calls)
    with pytest.raises(NotASandbox):
        Ephemeral(FakeGh()).down("alice/production")


def test_down_explains_the_missing_scope():
    fake = FakeGh(delete_fails="HTTP 403: Must have admin rights (needs the delete_repo scope)")
    with pytest.raises(EphemeralError, match="gh auth refresh"):
        Ephemeral(fake).down(REPO)


def test_main_reports_errors_with_exit_code_2(capsys):
    assert ephemeral.main(["down", "--repo", "alice/prod"], env={}, eph=Ephemeral(FakeGh())) == 2
    assert "refusing" in capsys.readouterr().err


def test_up_approves_both_bootstrap_gates_with_approve(tmp_path):
    fake = FakeGh()
    _up(fake, tmp_path)
    approvals = [
        json.loads(i)
        for c, i in fake.gh()
        if c[3] == "POST" and c[4].endswith("/pending_deployments")
    ]
    assert [a["environment_ids"] for a in approvals] == [[99], [99]]
    assert all(a["state"] == "approved" for a in approvals)


def test_up_reruns_failed_jobs_then_gives_up(tmp_path, capsys):
    fake = FakeGh(failures=1)
    _up(fake, tmp_path)
    assert "re-running the failed jobs (staged / dev)" in capsys.readouterr().out
    fake = FakeGh(failures=5)
    with pytest.raises(EphemeralError, match="failed \\(staged / dev\\)"):
        _up(fake, tmp_path / "b", reruns=2)


def test_up_without_approve_waits_for_a_reviewer(tmp_path, capsys):
    fake = FakeGh()
    with pytest.raises(EphemeralError, match="did not finish in time"):
        _up(fake, tmp_path, approve=False, timeout=60)
    out = capsys.readouterr().out
    assert out.count("prod waits for approval by alice") == 1
    assert not any(c[3] == "POST" and "pending" in c[4] for c, _ in fake.gh())
