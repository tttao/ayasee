"""Run plan.sh and apply.sh for real against a local-backend Terragrunt fixture.

Needs tofu on PATH and terragrunt (PATH or .tools/bin). The tests share one environment and
build on each other, so they run in file order.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "tg"
PASSPHRASE = "ayasee-test-passphrase-1234"
MARKER = b"ayasee-fixture-secret-marker"
SHA = "a" * 40


def _make_sandbox(root: Path, fixture: str = "tg"):
    tools = REPO / ".tools" / "bin"
    path = os.pathsep.join([str(tools), os.environ["PATH"]])
    if not (shutil.which("tofu", path=path) and shutil.which("terragrunt", path=path)):
        pytest.skip("tofu and terragrunt are required")
    shim = root / "bin"
    shim.mkdir()
    cli = shim / "ayasee"
    cli.write_text(
        f"#!/bin/sh\nPYTHONPATH={REPO / 'src'} exec {sys.executable} "
        '-c "from ayasee.cli import entrypoint; entrypoint()" "$@"\n'
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    shutil.copytree(REPO / "tests" / "fixtures" / fixture, root / "repo")
    (root / "state").mkdir()
    (root / "temp").mkdir()
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("TF_", "TG_"))},
        "PATH": os.pathsep.join([str(shim), path]),
        "RUNNER_TEMP": str(root / "temp"),
        "AYASEE_TEST_PASSPHRASE": PASSPHRASE,
        "AYASEE_FIXTURE_STATE_DIR": str(root / "state"),
        "GITHUB_SHA": SHA,
        "SG_ENV": "dev",
        "SG_UNITS_ROOT": str(root / "repo" / "stages"),
        "SG_ENCRYPT": "true",
        "SG_BINDING_MODE": "encrypted_plan",
    }
    return root, env


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    return _make_sandbox(tmp_path_factory.mktemp("sg"))


@pytest.fixture
def fresh(tmp_path):
    """A new sandbox per test: fresh(fixture_name) -> (root, env)."""
    return lambda fixture="tg": _make_sandbox(tmp_path, fixture)


def _no_leak(*procs):
    for proc in procs:
        assert MARKER.decode() not in proc.stdout + proc.stderr, "resource values reached the log"


def _run(script, env, **extra):
    out = env["RUNNER_TEMP"] + f"/out-{script.stem}-{len(extra)}.txt"
    Path(out).unlink(missing_ok=True)
    proc = subprocess.run(
        ["bash", str(REPO / "actions" / script)],
        env={**env, "GITHUB_OUTPUT": out, **extra},
        capture_output=True,
        text=True,
        cwd=env["RUNNER_TEMP"],
    )
    outputs = {}
    if Path(out).exists():
        for line in Path(out).read_text().splitlines():
            k, _, v = line.partition("=")
            outputs[k] = v
    return proc, outputs


def _work(env):
    return Path(env["RUNNER_TEMP"]) / "ayasee" / "dev"


def _states(root):
    return sorted((root / "state").glob("*.tfstate"))


def test_plan_encrypts_and_produces_artifacts(sandbox):
    root, env = sandbox
    proc, out = _run(Path("plan/plan.sh"), env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    _no_leak(proc)
    work = _work(env)
    assert out["has-changes"] == "true"
    assert out["changeset-path"] == str(work / "changeset.json")
    # the engine output is kept, but only in the job temp dir
    assert MARKER in (work / "logs" / "base.plan.log").read_bytes()
    assert out["manifest-path"] == str(work / "manifest.json")
    assert out["plan-dir"] == str(work / "plans")
    plans = sorted(p.name for p in (work / "plans").iterdir())
    assert plans == ["app.tfplan", "base.tfplan"]
    assert sorted(p.name for p in (work / "json").iterdir()) == ["app.json", "base.json"]
    assert sorted(p.name for p in (work / "locks").iterdir()) == [
        "app.lock.hcl",
        "base.lock.hcl",
    ]
    manifest = json.loads((work / "manifest.json").read_text())
    assert set(manifest["units"]) == {"app", "base"}
    # the JSON proves the marker is in the config, but the plan files must not show it
    assert MARKER in (work / "json" / "base.json").read_bytes()
    for plan in (work / "plans").iterdir():
        assert MARKER not in plan.read_bytes()
    bare = {k: v for k, v in env.items() if k != "TF_ENCRYPTION"}
    shown = subprocess.run(
        ["tofu", "show", str(work / "plans" / "base.tfplan")],
        env=bare,
        capture_output=True,
        text=True,
        cwd=env["RUNNER_TEMP"],
    )
    assert shown.returncode != 0
    assert _states(root) == []


def test_encrypt_without_key_fails_clearly(sandbox):
    _, env = sandbox
    clean = {k: v for k, v in env.items() if k != "AYASEE_TEST_PASSPHRASE"}
    proc, _ = _run(Path("plan/plan.sh"), clean)
    assert proc.returncode != 0
    assert "key-vault-key-id" in proc.stderr


def test_unencrypted_prod_plan_is_refused(sandbox):
    _, env = sandbox
    proc, _ = _run(Path("plan/plan.sh"), env, SG_ENCRYPT="false", SG_ENV="prod")
    assert proc.returncode != 0
    assert "encrypted_plan requires encrypt=true" in proc.stderr


def test_tampered_plan_stops_before_apply(sandbox):
    root, env = sandbox
    tampered = root / "tampered"
    shutil.copytree(_work(env) / "plans", tampered)
    data = bytearray((tampered / "app.tfplan").read_bytes())
    data[len(data) // 2] ^= 0xFF
    (tampered / "app.tfplan").write_bytes(bytes(data))
    proc, out = _run(
        Path("apply/apply.sh"),
        env,
        SG_BINDING_MODE="encrypted_plan",
        SG_PLAN_ARTIFACT=str(tampered),
        SG_MANIFEST_PATH=str(_work(env) / "manifest.json"),
    )
    assert proc.returncode != 0
    assert "re-plan" in proc.stderr
    assert _states(root) == []
    assert not (_work(env) / "units.json").exists()


def test_apply_encrypted_plan_applies_in_order_and_converges(sandbox):
    root, env = sandbox
    proc, out = _run(
        Path("apply/apply.sh"),
        env,
        SG_BINDING_MODE="encrypted_plan",
        SG_PLAN_ARTIFACT=str(_work(env)),
        SG_MANIFEST_PATH=str(_work(env) / "manifest.json"),
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    _no_leak(proc)
    assert out["units-json"] == str(_work(env) / "units.json")
    units = json.loads((_work(env) / "units.json").read_text())
    assert list(units) == ["base", "app"]
    assert units == {
        "base": {"status": "applied", "converged": True},
        "app": {"status": "applied", "converged": True},
    }
    assert [p.name for p in _states(root)] == ["app.tfstate", "base.tfstate"]


def test_second_plan_has_no_changes(sandbox):
    _, env = sandbox
    proc, out = _run(Path("plan/plan.sh"), env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert out["has-changes"] == "false"


def test_replan_mode_verifies_and_reports_no_changes(sandbox):
    _, env = sandbox
    proc, _ = _run(
        Path("apply/apply.sh"),
        env,
        SG_BINDING_MODE="replan",
        SG_MANIFEST_PATH=str(_work(env) / "manifest.json"),
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    _no_leak(proc)
    units = json.loads((_work(env) / "units.json").read_text())
    assert units == {
        "base": {"status": "no_changes", "converged": True},
        "app": {"status": "no_changes", "converged": True},
    }


@pytest.mark.parametrize("script", ["plan/plan.sh", "apply/apply.sh"])
def test_same_job_is_refused_outside_dev_and_uat(sandbox, script):
    _, env = sandbox
    proc, _ = _run(Path(script), env, SG_ENV="prod", SG_BINDING_MODE="same-job")
    assert proc.returncode != 0
    assert "only allowed for dev and uat" in proc.stderr


def test_apply_refuses_unencrypted_encrypted_plan_mode(sandbox):
    _, env = sandbox
    proc, _ = _run(Path("apply/apply.sh"), env, SG_ENCRYPT="false")
    assert proc.returncode != 0
    assert "requires encrypt=true" in proc.stderr


def test_ambiguous_or_versioned_keys_are_refused(sandbox):
    _, env = sandbox
    both = {**env, "SG_KEY_VAULT_KEY_ID": "https://v.vault.azure.net/keys/k"}
    proc, _ = _run(Path("plan/plan.sh"), both)
    assert proc.returncode != 0 and "use exactly one" in proc.stderr
    clean = {k: v for k, v in env.items() if k != "AYASEE_TEST_PASSPHRASE"}
    for bad in (
        "https://v.vault.azure.net/keys/k/0123abc",
        "https://v.managedhsm.azure.net/keys/k",
    ):
        proc, _ = _run(Path("plan/plan.sh"), clean, SG_KEY_VAULT_KEY_ID=bad)
        assert proc.returncode != 0 and "no key version, no managed HSM" in proc.stderr


def test_unencrypted_replan_run_deletes_plan_files(fresh):
    root, env = fresh()
    env = {**env, "SG_ENCRYPT": "false", "SG_BINDING_MODE": "replan"}
    proc, out = _run(Path("plan/plan.sh"), env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    _no_leak(proc)
    assert "plan-dir" not in out
    assert not (_work(env) / "plans").exists()
    assert (_work(env) / "manifest.json").exists()
    proc, _ = _run(Path("apply/apply.sh"), env, SG_MANIFEST_PATH=str(_work(env) / "manifest.json"))
    assert proc.returncode == 0, proc.stderr[-2000:]
    _no_leak(proc)
    units = json.loads((_work(env) / "units.json").read_text())
    assert {u["status"] for u in units.values()} == {"applied"}


def _same_job(env):
    env = {**env, "SG_BINDING_MODE": "same-job"}
    plan, _ = _run(Path("plan/plan.sh"), env)
    assert plan.returncode == 0, plan.stderr[-2000:]
    apply, outputs = _run(Path("apply/apply.sh"), env)
    _no_leak(plan, apply)
    units = json.loads((_work(env) / "units.json").read_text())
    return apply, outputs, units


def test_failed_unit_stops_the_run_and_skips_later_units(fresh):
    root, env = fresh("tg_fail")
    apply, outputs, units = _same_job(env)
    assert apply.returncode != 0
    assert outputs["units-json"] == str(_work(env) / "units.json")
    assert units == {
        "base": {"status": "applied", "converged": True},
        "bad": {"status": "failed", "converged": False},
        "last": {"status": "skipped", "converged": False},
    }
    assert not (root / "state" / "last.tfstate").exists()


def test_non_converging_unit_is_reported_and_stops_the_run(fresh):
    root, env = fresh("tg_drift")
    apply, _, units = _same_job(env)
    assert apply.returncode != 0
    assert units == {
        "base": {"status": "applied", "converged": True},
        "drift": {"status": "applied", "converged": False},
        "last": {"status": "skipped", "converged": False},
    }
    assert not (root / "state" / "last.tfstate").exists()


def test_replan_mismatch_stops_before_any_apply(fresh):
    root, env = fresh()
    env = {**env, "SG_ENCRYPT": "false", "SG_BINDING_MODE": "replan"}
    proc, _ = _run(Path("plan/plan.sh"), env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    main = root / "repo" / "stages" / "dev" / "app" / "main.tf"
    main.write_text(main.read_text().replace("marker-app", "marker-app-changed"))
    proc, _ = _run(Path("apply/apply.sh"), env, SG_MANIFEST_PATH=str(_work(env) / "manifest.json"))
    assert proc.returncode != 0
    assert "re-plan" in proc.stderr
    _no_leak(proc)
    assert _states(root) == []
    assert not (_work(env) / "units.json").exists()


def test_only_units_plans_the_named_units_in_dependency_order(fresh):
    root, env = fresh()
    proc, out = _run(Path("plan/plan.sh"), env, SG_ONLY_UNITS="app")
    assert proc.returncode == 0, proc.stderr[-2000:]
    _no_leak(proc)
    work = _work(env)
    assert [p.name for p in (work / "json").iterdir()] == ["app.json"]
    assert set(json.loads((work / "manifest.json").read_text())["units"]) == {"app"}
    assert out["summary-path"] == str(work / "summary.md")
    assert (work / "summary.md").read_text().startswith("### Prod gate")
    # the order of the list does not matter: the discovered dependency order wins
    proc, _ = _run(Path("plan/plan.sh"), env, SG_ONLY_UNITS="app,base,")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert sorted(p.name for p in (work / "json").iterdir()) == ["app.json", "base.json"]
    assert "base.plan.log" in {p.name for p in (work / "logs").iterdir()}


def test_only_units_unknown_name_fails_before_planning(fresh):
    root, env = fresh()
    proc, _ = _run(Path("plan/plan.sh"), env, SG_ONLY_UNITS="app,nope")
    assert proc.returncode != 0 and "unknown unit: nope" in proc.stderr
    assert not list((_work(env) / "json").iterdir())


def test_lock_false_plans_without_the_state_lock_and_bad_value_fails(fresh):
    root, env = fresh()
    proc, out = _run(Path("plan/plan.sh"), env, SG_LOCK="false")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert out["has-changes"] == "true"
    bad, _ = _run(Path("plan/plan.sh"), env, SG_LOCK="maybe")
    assert bad.returncode != 0 and "lock must be true or false" in bad.stderr


def _drift_env(env):
    return {**env, "SG_ENCRYPT": "false", "SG_BINDING_MODE": "drift", "SG_DRIFT": "true"}


def test_drift_mode_records_exit_codes_and_removes_plan_files(fresh):
    root, env = fresh()
    apply, _, _ = _same_job(env)
    assert apply.returncode == 0, apply.stderr[-2000:]
    denv = _drift_env(env)
    proc, out = _run(Path("plan/plan.sh"), denv)
    assert proc.returncode == 0, proc.stderr[-2000:]
    _no_leak(proc)
    codes_file = _work(env) / "exit-codes.json"
    assert out["exit-codes-path"] == str(codes_file)
    assert json.loads(codes_file.read_text()) == {"app": 0, "base": 0}
    assert out["has-changes"] == "false"
    # change the config of one unit after the apply: that unit now reports drift (exit 2)
    main = root / "repo" / "stages" / "dev" / "app" / "main.tf"
    main.write_text(main.read_text().replace("marker-app", "marker-app-changed"))
    proc, out = _run(Path("plan/plan.sh"), denv, SG_LOCK="true")
    assert proc.returncode == 0, proc.stderr[-2000:]
    _no_leak(proc)
    assert json.loads(codes_file.read_text()) == {"app": 2, "base": 0}
    assert out["has-changes"] == "true"
    assert "plan-dir" not in out
    assert not (_work(env) / "plans").exists()
    assert not list(_work(env).rglob("*.tfplan"))
    assert (_work(env) / "json" / "app.json").exists()


def test_drift_plan_failure_is_not_recorded_as_drift(fresh):
    root, env = fresh("tg_fail")
    main = root / "repo" / "stages" / "dev" / "base" / "main.tf"
    main.write_text('resource "terraform_data" "r" {\n  input = nope.nope\n}\n')
    proc, _ = _run(Path("plan/plan.sh"), _drift_env(env))
    assert proc.returncode != 0 and "planning" in proc.stderr
    assert not (_work(env) / "exit-codes.json").exists()


def test_drift_mode_combinations_are_checked(fresh):
    root, env = fresh()
    cases = [
        ({**_drift_env(env), "SG_ENCRYPT": "true"}, "requires encrypt=false"),
        ({**_drift_env(env), "SG_BINDING_MODE": "replan"}, "needs binding-mode drift"),
        (
            {**env, "SG_ENCRYPT": "false", "SG_BINDING_MODE": "drift"},
            "only allowed with drift: true",
        ),
    ]
    for bad, message in cases:
        proc, _ = _run(Path("plan/plan.sh"), bad)
        assert proc.returncode != 0 and message in proc.stderr, message
    # an apply never accepts the drift mode
    proc, _ = _run(Path("apply/apply.sh"), _drift_env(env))
    assert proc.returncode != 0 and "only allowed with drift: true" in proc.stderr


def _spy_tofu(env):
    """Put a `tofu` in front of the real one that records its arguments."""
    real = shutil.which("tofu", path=env["PATH"])
    log = Path(env["RUNNER_TEMP"]) / "tofu-args.log"
    spy = Path(env["RUNNER_TEMP"]) / "spy"
    spy.mkdir(exist_ok=True)
    tofu = spy / "tofu"
    tofu.write_text(f'#!/bin/sh\necho "$@" >>{log}\nexec {real} "$@"\n')
    tofu.chmod(tofu.stat().st_mode | stat.S_IXUSR)
    log.unlink(missing_ok=True)
    return {**env, "PATH": f"{spy}{os.pathsep}{env['PATH']}"}, log


def _lines(log, word):
    return [ln for ln in log.read_text().splitlines() if ln.startswith(word)]


def test_lock_timeout_reaches_plan_apply_and_converge(fresh):
    root, env = fresh()
    env, log = _spy_tofu({**env, "SG_BINDING_MODE": "same-job"})
    proc, _ = _run(Path("plan/plan.sh"), env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert _lines(log, "plan") and all("-lock-timeout=10m" in ln for ln in _lines(log, "plan"))
    proc, _ = _run(Path("apply/apply.sh"), env, SG_LOCK_TIMEOUT="30s")
    assert proc.returncode == 0, proc.stderr[-2000:]
    applies = _lines(log, "apply")
    assert applies and all("-lock-timeout=30s" in ln for ln in applies)
    converge = [ln for ln in _lines(log, "plan") if "-detailed-exitcode" in ln]
    assert converge and all("-lock-timeout=30s" in ln for ln in converge)


def test_lock_timeout_is_not_passed_with_lock_false_and_drift_uses_it(fresh):
    root, env = fresh()
    env, log = _spy_tofu(env)
    proc, _ = _run(Path("plan/plan.sh"), env, SG_LOCK="false", SG_LOCK_TIMEOUT="5m")
    assert proc.returncode == 0, proc.stderr[-2000:]
    plans = _lines(log, "plan")
    assert plans and all("-lock=false" in ln and "-lock-timeout" not in ln for ln in plans)
    log.unlink()
    proc, _ = _run(Path("plan/plan.sh"), _drift_env(env), SG_LOCK_TIMEOUT="2m")
    assert proc.returncode == 0, proc.stderr[-2000:]
    plans = _lines(log, "plan")
    assert plans and all("-lock-timeout=2m" in ln and "-detailed-exitcode" in ln for ln in plans)


def test_bad_lock_timeout_is_refused(fresh):
    root, env = fresh()
    for bad in ("10", "m", "1h", "10m; id", "-1m"):
        proc, _ = _run(Path("plan/plan.sh"), env, SG_LOCK_TIMEOUT=bad)
        assert proc.returncode != 0 and "lock-timeout must look like" in proc.stderr, bad
    proc, _ = _run(Path("apply/apply.sh"), env, SG_LOCK_TIMEOUT="1h")
    assert proc.returncode != 0 and "lock-timeout must look like" in proc.stderr


def test_expect_sha_requires_the_checkout_to_be_at_that_commit(fresh):
    root, env = fresh()
    repo = root / "repo"
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], check=True)
    head = subprocess.run(
        [*git, "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()

    def run(sha, **extra):
        return subprocess.run(
            ["bash", str(REPO / "actions" / "plan" / "plan.sh")],
            env={**env, "SG_SHA": sha, "GITHUB_OUTPUT": str(root / "out"), **extra},
            capture_output=True,
            text=True,
            cwd=repo,
        )

    assert run(head, SG_EXPECT_SHA="true").returncode == 0
    bad = run("b" * 40, SG_EXPECT_SHA="true")
    assert bad.returncode != 0 and "expect-sha" in bad.stderr
    assert run("b" * 40).returncode == 0  # opt-in: off by default
    assert run(head, SG_EXPECT_SHA="maybe").returncode != 0


# --- break-glass (R44): plan and apply in one job, prod only, with an incident reference ---------


def _breakglass_env(env, **extra):
    return {**env, "SG_ENV": "prod", "SG_BINDING_MODE": "breakglass", **extra}


@pytest.mark.parametrize("script", ["plan/plan.sh", "apply/apply.sh"])
@pytest.mark.parametrize("target", ["dev", "uat", "prod-plan"])
def test_breakglass_is_refused_outside_prod(sandbox, script, target):
    _, env = sandbox
    env = _breakglass_env(env, SG_ENV=target, SG_BREAKGLASS_INCIDENT="INC123")
    proc, _ = _run(Path(script), env)
    assert proc.returncode != 0
    assert "binding-mode breakglass is only allowed for prod" in proc.stderr


@pytest.mark.parametrize("script", ["plan/plan.sh", "apply/apply.sh"])
@pytest.mark.parametrize(
    "incident", [None, "", "inc123", "INC", "ABC-", "abc-1", "INC123 ", "INC1\nINC2", "X-1;ls"]
)
def test_breakglass_needs_a_well_formed_incident(sandbox, script, incident):
    _, env = sandbox
    extra = {} if incident is None else {"SG_BREAKGLASS_INCIDENT": incident}
    proc, _ = _run(Path(script), _breakglass_env(env, **extra))
    assert proc.returncode != 0
    assert "SG_BREAKGLASS_INCIDENT" in proc.stderr


@pytest.mark.parametrize("incident", ["INC123", "OPS-42", "SEC-7"])
def test_breakglass_plans_and_applies_prod_with_a_valid_incident(fresh, incident):
    root, env = fresh()
    shutil.copytree(root / "repo" / "stages" / "dev", root / "repo" / "stages" / "prod")
    env = _breakglass_env(env, SG_BREAKGLASS_INCIDENT=incident)
    plan, _ = _run(Path("plan/plan.sh"), env)
    assert plan.returncode == 0, plan.stderr[-2000:]
    work = Path(env["RUNNER_TEMP"]) / "ayasee" / "prod"
    assert (work / "plans" / "base.tfplan").exists()
    apply, outputs = _run(Path("apply/apply.sh"), env)
    assert apply.returncode == 0, apply.stderr[-2000:]
    _no_leak(plan, apply)
    assert json.loads((work / "units.json").read_text()) == {
        "base": {"status": "applied", "converged": True},
        "app": {"status": "applied", "converged": True},
    }
    assert outputs["units-json"] == str(work / "units.json")
    assert len(_states(root)) == 2


def test_breakglass_refuses_only_units_in_plan_and_apply(fresh):
    """R71: break-glass always plans and applies the whole prod tree."""
    root, env = fresh()
    shutil.copytree(root / "repo" / "stages" / "dev", root / "repo" / "stages" / "prod")
    env = _breakglass_env(env, SG_BREAKGLASS_INCIDENT="INC9", SG_ONLY_UNITS="base")
    plan, _ = _run(Path("plan/plan.sh"), env)
    assert plan.returncode != 0
    assert "only-units is not allowed with binding-mode breakglass" in plan.stderr
    apply, _ = _run(Path("apply/apply.sh"), env)
    assert apply.returncode != 0
    assert "only-units is only allowed with binding-mode same-job, not breakglass" in apply.stderr
    assert _states(root) == []


@pytest.mark.parametrize("mode", ["encrypted_plan", "replan"])
def test_apply_refuses_only_units_outside_same_job(sandbox, mode):
    _, env = sandbox
    proc, _ = _run(Path("apply/apply.sh"), env, SG_BINDING_MODE=mode, SG_ONLY_UNITS="base")
    assert proc.returncode != 0
    assert "only-units is only allowed with binding-mode same-job" in proc.stderr


# ---- R52: dependency outputs versus saved plans ----


def _dep(fresh, **extra):
    root, env = fresh("tg_dep")
    return root, {**env, "SG_ENCRYPT": "false", **extra}


def _units(env):
    return json.loads((_work(env) / "units.json").read_text())


def test_same_job_converges_when_base_and_its_dependent_both_change(fresh):
    root, env = _dep(fresh, SG_BINDING_MODE="same-job")
    for marker in ("v1", "v2"):
        env = {**env, "DEP_MARKER": marker}
        proc, _ = _run(Path("plan/plan.sh"), env)
        assert proc.returncode == 0, proc.stderr[-2000:]
        proc, _ = _run(Path("apply/apply.sh"), env)
        assert proc.returncode == 0, proc.stderr[-2000:]
        units = _units(env)
        assert set(units) == {"base", "grants"}
        assert all(u["converged"] for u in units.values()), units
    state = json.loads((root / "state" / "grants.tfstate").read_text())
    assert "v2" in json.dumps(state)  # grants got the fresh output of base


def _grants_hcl(root):
    return root / "repo" / "stages" / "dev" / "grants" / "terragrunt.hcl"


def _applied_v1(fresh, config_path=None, **extra):
    """Both units applied at v1, then the next plan is made with the given env."""
    root, env = _dep(fresh, SG_BINDING_MODE="same-job")
    if config_path:
        hcl = _grants_hcl(root)
        hcl.write_text(
            hcl.read_text().replace('config_path = "../base"', f'config_path = "{config_path}"')
        )
    for script in ("plan/plan.sh", "apply/apply.sh"):
        proc, _ = _run(Path(script), env)
        assert proc.returncode == 0, proc.stderr[-2000:]
    return root, {**env, "SG_BINDING_MODE": "encrypted_plan", "SG_ENCRYPT": "true", **extra}


def test_encrypted_plan_refuses_a_cascading_change(fresh):
    root, env = _applied_v1(fresh, DEP_MARKER="v2", GRANTS_TAG="g2")
    proc, out = _run(Path("plan/plan.sh"), env)
    assert proc.returncode == 1
    assert (
        "cascading change: grants depends on base, which also changes; land base first"
        in proc.stderr
    )
    assert "manifest-path" not in out


def test_allow_cascade_proceeds(fresh):
    root, env = _applied_v1(fresh, DEP_MARKER="v2", GRANTS_TAG="g2", SG_ALLOW_CASCADE="true")
    proc, out = _run(Path("plan/plan.sh"), env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert out["has-changes"] == "true"
    assert "unsafe" in proc.stderr


def test_encrypted_plan_with_only_the_dependent_changing_proceeds(fresh):
    root, env = _applied_v1(fresh, GRANTS_TAG="g2")
    proc, out = _run(Path("plan/plan.sh"), env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert out["has-changes"] == "true"


def test_ordering_only_dependencies_are_not_a_cascade(fresh):
    # tests/fixtures/tg: app lists base under `dependencies` (order only, no outputs)
    root, env = fresh("tg")
    proc, _ = _run(Path("plan/plan.sh"), env)
    assert proc.returncode == 0, proc.stderr[-2000:]


def test_replan_mode_refuses_too(fresh):
    root, env = _applied_v1(fresh, DEP_MARKER="v2", GRANTS_TAG="g2")
    env = {**env, "SG_BINDING_MODE": "replan", "SG_ENCRYPT": "false", "SG_ENGINE": "opentofu"}
    proc, _ = _run(Path("plan/plan.sh"), env)
    assert proc.returncode == 1 and "cascading change" in proc.stderr


def test_an_interpolated_absolute_config_path_is_still_a_cascade(fresh):
    # Terragrunt renders ${get_terragrunt_dir()}/../base as an absolute path.
    root, env = _applied_v1(
        fresh,
        config_path="${get_terragrunt_dir()}/../base",
        DEP_MARKER="v2",
        GRANTS_TAG="g2",
    )
    proc, out = _run(Path("plan/plan.sh"), env)
    assert proc.returncode == 1
    assert "cascading change: grants depends on base" in proc.stderr
    assert "manifest-path" not in out


def test_a_dependency_outside_the_units_root_fails_closed(fresh):
    root, env = _applied_v1(fresh, DEP_MARKER="v2", GRANTS_TAG="g2")
    hcl = _grants_hcl(root)
    hcl.write_text(
        hcl.read_text().replace('config_path = "../base"', 'config_path = "/nonexistent/elsewhere"')
    )
    proc, out = _run(Path("plan/plan.sh"), env)
    assert proc.returncode == 1
    assert "outside" in proc.stderr and "cascading" not in proc.stderr
    assert "manifest-path" not in out


def test_a_dependency_that_is_not_a_unit_fails_closed(fresh):
    root, env = _applied_v1(fresh, DEP_MARKER="v2", GRANTS_TAG="g2")
    (root / "repo" / "stages" / "dev" / "ghost").mkdir()
    hcl = _grants_hcl(root)
    hcl.write_text(hcl.read_text().replace('config_path = "../base"', 'config_path = "../ghost"'))
    proc, out = _run(Path("plan/plan.sh"), env)
    assert proc.returncode == 1
    assert "manifest-path" not in out  # the plan or the edge check stopped the run


# ---- R53: break-glass applies only what the policy pack saw ----


def _breakglass_dep(fresh, **extra):
    root, env = fresh("tg_dep")
    shutil.copytree(root / "repo" / "stages" / "dev", root / "repo" / "stages" / "prod")
    return root, _breakglass_env(env, SG_BREAKGLASS_INCIDENT="INC123", **extra)


def test_breakglass_refuses_a_unit_whose_fresh_plan_differs_from_the_checked_preview(fresh):
    root, env = _breakglass_dep(fresh)
    for script in ("plan/plan.sh", "apply/apply.sh"):  # first apply: preview and fresh plan agree
        proc, _ = _run(Path(script), env)
        assert proc.returncode == 0, proc.stderr[-2000:]
    env = {**env, "DEP_MARKER": "v2"}
    proc, _ = _run(Path("plan/plan.sh"), env)  # preview: base changes, grants shows nothing
    assert proc.returncode == 0, proc.stderr[-2000:]
    proc, _ = _run(Path("apply/apply.sh"), env)
    assert proc.returncode != 0
    assert "grants: plan changed since the policy check; re-run break-glass" in proc.stderr
    units = json.loads((Path(env["RUNNER_TEMP"]) / "ayasee" / "prod" / "units.json").read_text())
    assert units["base"]["status"] == "applied" and units["grants"]["status"] == "failed"
    state = json.loads((root / "state" / "grants.tfstate").read_text())
    assert "v2" not in json.dumps(state)  # the unchecked change was not applied


def test_same_job_has_no_such_gate(fresh):
    # the dev/uat behaviour of R52 (a) stays: the fresh plan is applied without a comparison
    root, env = _dep(fresh, SG_BINDING_MODE="same-job")
    for marker in ("v1", "v2"):
        env = {**env, "DEP_MARKER": marker}
        for script in ("plan/plan.sh", "apply/apply.sh"):
            proc, _ = _run(Path(script), env)
            assert proc.returncode == 0, proc.stderr[-2000:]


# ---- R72: an output-only change goes through the prod gate ----


def test_output_only_change_reports_has_changes_for_prod(fresh):
    """Only a root output changes: prod-plan must say has-changes=true (gate), not in sync."""
    root, env = fresh()
    stages = root / "repo" / "stages"
    shutil.copytree(stages / "dev", stages / "prod")
    prod = {**env, "SG_ENV": "prod"}
    # make prod live first (break-glass is the one-job prod path)
    bg = {**prod, "SG_BINDING_MODE": "breakglass", "SG_BREAKGLASS_INCIDENT": "INC1"}
    plan, _ = _run(Path("plan/plan.sh"), bg)
    assert plan.returncode == 0, plan.stderr[-2000:]
    apply, _ = _run(Path("apply/apply.sh"), bg)
    assert apply.returncode == 0, apply.stderr[-2000:]
    work = Path(env["RUNNER_TEMP"]) / "ayasee" / "prod"

    proc, out = _run(Path("plan/plan.sh"), prod)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert out["has-changes"] == "false"  # in sync: prod-in-sync would run

    main = stages / "prod" / "base" / "main.tf"
    main.write_text(
        main.read_text()
        + '\noutput "extra" {\n  value = "ayasee-fixture-secret-marker-output"\n}\n'
        + 'output "hidden" {\n  value     = "ayasee-fixture-secret-marker-sensitive"\n'
        + "  sensitive = true\n}\n"
    )
    proc, out = _run(Path("plan/plan.sh"), prod)
    assert proc.returncode == 0, proc.stderr[-2000:]
    _no_leak(proc)
    assert out["has-changes"] == "true"  # the prod job and its gate run, not prod-in-sync
    cs = json.loads((work / "changeset.json").read_text())
    assert [(i["unit"], i["kind"], i["address"], i["tier"]) for i in cs["items"]] == [
        ("base", "output", "output.extra", "none"),
        ("base", "output", "output.hidden", "none"),
    ]
    for f in (work / "changeset.json", work / "summary.md"):
        assert MARKER not in f.read_bytes(), f.name
