"""Pre-commit hooks and the secret scan: pinning, versions aligned with CI, the gitleaks allowlist.

The behaviour tests run the real gitleaks binary when it is installed (.tools/bin or PATH); the CI
`secrets` job sets AYASEE_REQUIRE_GITLEAKS=1 so that a missing binary fails there instead of
skipping.
"""

import base64
import hashlib
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".pre-commit-config.yaml"
CONFIG_TEXT = CONFIG.read_text()
CI = ROOT / ".github" / "workflows" / "ci.yml"
GITLEAKS_TOML = ROOT / ".gitleaks.toml"
CHECK_SECRETS = ROOT / "scripts" / "check-secrets.sh"
DEV_TOOL = ROOT / "scripts" / "dev-tool.sh"


def _config():
    return yaml.safe_load(CONFIG_TEXT)


def _remote_repos():
    return [r for r in _config()["repos"] if r["repo"] not in ("local", "meta")]


def _frozen_tag(repo_url: str) -> str:
    m = re.search(
        rf"repo: {re.escape(repo_url)}\n\s+rev: [0-9a-f]{{40}}\s+# frozen: (v\S+)", CONFIG_TEXT
    )
    assert m, f"{repo_url} has no '# frozen: <tag>' comment"
    return m.group(1)


def test_every_remote_hook_repo_is_pinned_by_commit_sha_with_its_tag():
    repos = _remote_repos()
    assert repos
    for r in repos:
        assert re.fullmatch(r"[0-9a-f]{40}", str(r["rev"])), r["repo"]
        assert r["repo"].startswith("https://github.com/"), r["repo"]
        _frozen_tag(r["repo"])


def test_ruff_hook_matches_the_locked_ruff():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = next(p["version"] for p in lock["package"] if p["name"] == "ruff")
    assert _frozen_tag("https://github.com/astral-sh/ruff-pre-commit") == f"v{locked}"


def test_zizmor_hook_matches_ci():
    ci_version = re.search(r"uvx zizmor@(\S+)", CI.read_text()).group(1)
    assert _frozen_tag("https://github.com/zizmorcore/zizmor-pre-commit") == f"v{ci_version}"


def _hooks():
    return {h["id"]: h for r in _config()["repos"] for h in r["hooks"]}


def test_secret_hooks_are_present():
    hooks = _hooks()
    assert hooks["gitleaks"]["entry"] == "scripts/check-secrets.sh --staged"
    assert hooks["gitleaks"]["pass_filenames"] is False
    assert "detect-private-key" in hooks
    assert hooks["no-commit-to-branch"]["args"] == ["--branch", "main"]


def test_local_tool_hooks_go_through_dev_tool():
    for r in _config()["repos"]:
        if r["repo"] != "local":
            continue
        for h in r["hooks"]:
            entry = h["entry"].split()[0]
            assert entry.startswith("scripts/") or entry in ("python3", "uv"), h["id"]
    hooks = _hooks()
    for tool_hook in ("actionlint", "tofu-fmt", "opa-fmt", "opa-check"):
        assert hooks[tool_hook]["entry"].startswith("scripts/dev-tool.sh "), tool_hook


def test_tests_run_only_before_push_and_only_unit():
    hooks = _hooks()
    pytest_hooks = [h for h in hooks.values() if "pytest" in h.get("entry", "")]
    assert [h["id"] for h in pytest_hooks] == ["pytest-unit"]
    assert hooks["pytest-unit"]["stages"] == ["pre-push"]
    assert '-m "not integration"' in hooks["pytest-unit"]["entry"]
    assert _config()["default_install_hook_types"] == ["pre-commit"]


def test_renovate_updates_the_hook_revs():
    import json

    cfg = json.loads((ROOT / "renovate.json").read_text())
    assert "pre-commit" in cfg["enabledManagers"]
    assert cfg["pre-commit"]["enabled"] is True


# --- CI job --------------------------------------------------------------------------------------


def _secrets_job():
    return yaml.safe_load(CI.read_text())["jobs"]["secrets"]


def test_ci_secrets_job_is_read_only_and_fetches_history():
    job = _secrets_job()
    assert job["permissions"] == {"contents": "read"}
    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"] == {"fetch-depth": 0, "persist-credentials": False}


def test_ci_secrets_job_scans_the_pr_range_through_env():
    job = _secrets_job()
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert "scripts/install-dev-tools.sh gitleaks" in runs
    assert "scripts/check-secrets.sh" in runs
    assert "${{" not in runs  # event values reach the script through env only
    scan = next(s for s in job["steps"] if "check-secrets.sh" in s.get("run", ""))
    assert scan["env"] == {
        "BASE_SHA": "${{ github.event.pull_request.base.sha }}",
        "HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
    }


# --- the allowlist -------------------------------------------------------------------------------


def test_allowlist_is_never_global():
    cfg = tomllib.loads(GITLEAKS_TOML.read_text())
    assert cfg["extend"] == {"useDefault": True}
    assert "allowlist" not in cfg  # the old single-table form
    for a in cfg.get("allowlists", []):
        assert a["condition"] == "AND", a
        assert a["paths"] and a["regexes"], a
        assert all(p.startswith("^(tests|e2e)/") for p in a["paths"]), a
    for rule in cfg.get("rules", []):
        assert "allowlists" not in rule and "allowlist" not in rule


def _gitleaks():
    local = ROOT / ".tools" / "bin" / "gitleaks"
    found = str(local) if local.exists() else shutil.which("gitleaks")
    if not found:
        if os.environ.get("AYASEE_REQUIRE_GITLEAKS") == "1":
            pytest.fail("gitleaks is required here: scripts/install-dev-tools.sh gitleaks")
        pytest.skip("gitleaks not installed (scripts/install-dev-tools.sh gitleaks)")
    return found


# Built at run time so this file does not itself hold a detectable value. The key bodies are
# 16 of [A-Z2-7], the shape gitleaks' aws-access-token rule matches.
AWS_KEY = "AKIA" + "Z5TPQ3XMB7NV4HKR"
MARKED_AWS_KEY = "AKIA" + "AYASEETESTZ7K4MQ"  # the marker inside a valid-shape key id
MARKED_SECRET = "ayasee" + "TestSecretKey0123456789/+abcdefghi"
_PEM_BODY = base64.b64encode(hashlib.sha512(b"ayasee").digest() * 3).decode()
_PEM_LINES = "\n".join(_PEM_BODY[i : i + 64] for i in range(0, len(_PEM_BODY), 64))
_BEGIN, _END = "-----BEGIN ", "-----END "
PEM = f"{_BEGIN}{'PRIVATE'} KEY-----\n{_PEM_LINES}\n{_END}{'PRIVATE'} KEY-----\n"
# a real key with a marker comment inside the block, and one whose label carries the marker
PEM_MARKER_LINE = PEM.replace("\n", "\n# ayasee-test\n", 1)
PEM_MARKER_LABEL = PEM.replace(" PRIVATE KEY", " AYASEE-TEST PRIVATE KEY")
VALUES = (AWS_KEY, MARKED_AWS_KEY, MARKED_SECRET, _PEM_BODY[:40])


def _repo_with(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}

    def git(*a):
        subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=env)

    git("init", "-q", "-b", "work")
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "c")
    return repo


def _scan(gitleaks: str, repo: Path) -> subprocess.CompletedProcess:
    flags = re.search(r"^args=\(git (.*?)\)$", CHECK_SECRETS.read_text(), re.M | re.S).group(1)
    assert '--config "$root/.gitleaks.toml" --gitleaks-ignore-path "$root"' in flags
    assert "--ignore-gitleaks-allow" in flags and "--redact" in flags
    return subprocess.run(
        # the flags of scripts/check-secrets.sh, with the scanned repository as $root
        [gitleaks, "git", "--config", str(GITLEAKS_TOML), "--gitleaks-ignore-path", str(repo)]
        + ["--ignore-gitleaks-allow", "--redact", "--verbose", "--no-banner", str(repo)],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "rel,text,rule",
    [
        ("tests/test_x.py", f'SECRET = "{MARKED_SECRET}"\n', None),
        ("e2e/fixture/x.py", f'SECRET = "{MARKED_SECRET}"\n', None),
        ("tests/test_x.py", f'ids = ["{MARKED_AWS_KEY}"]\n', None),
        ("src/ayasee/x.py", f'SECRET = "{MARKED_SECRET}"\n', "generic-api-key"),
        ("src/ayasee/x.py", f'ids = ["{MARKED_AWS_KEY}"]\n', "aws-access-token"),
        ("tests/test_x.py", f'ids = ["{AWS_KEY}"]\n', "aws-access-token"),
        ("src/ayasee/x.py", f'ids = ["{AWS_KEY}"]\n', "aws-access-token"),
        ("tests/fixtures/k.pem", PEM, "private-key"),
        ("tests/fixtures/k.pem", PEM_MARKER_LINE, "private-key"),
        ("tests/fixtures/k.pem", PEM_MARKER_LABEL, "private-key"),
    ],
    # explicit ids: the default would put the values in the test names and the pytest cache
    ids=[
        "marked-secret-tests",
        "marked-secret-e2e",
        "marked-aws-key-tests",
        "marked-secret-src",
        "marked-aws-key-src",
        "aws-key-tests",
        "aws-key-src",
        "pem-tests",
        "pem-marker-line-tests",
        "pem-marker-label-tests",
    ],
)
def test_allowlist_needs_the_test_path_and_a_marked_single_token(tmp_path, rel, text, rule):
    gitleaks = _gitleaks()
    p = _scan(gitleaks, _repo_with(tmp_path, {rel: text}))
    out = p.stdout + p.stderr
    assert p.returncode == (0 if rule is None else 1), out
    if rule:
        # a finding names its rule and file
        assert f"RuleID:      {rule}" in out and rel in out, out
    # the value never reaches the log
    for v in VALUES:
        assert v not in out


def test_an_inline_gitleaks_allow_comment_does_not_hide_a_secret(tmp_path):
    gitleaks = _gitleaks()
    text = f'ids = ["{AWS_KEY}"]  # gitleaks:allow\n'
    p = _scan(gitleaks, _repo_with(tmp_path, {"src/ayasee/x.py": text}))
    assert p.returncode == 1, p.stdout + p.stderr


def test_a_rotated_finding_is_recorded_by_fingerprint_in_gitleaksignore(tmp_path):
    gitleaks = _gitleaks()
    repo = _repo_with(tmp_path, {"src/ayasee/x.py": f'ids = ["{AWS_KEY}"]\n'})
    first = _scan(gitleaks, repo)
    assert first.returncode == 1
    fingerprint = re.search(r"^Fingerprint:\s+(\S+)$", first.stdout + first.stderr, re.M).group(1)
    assert re.fullmatch(r"[0-9a-f]{40}:src/ayasee/x\.py:aws-access-token:1", fingerprint)
    (repo / ".gitleaksignore").write_text(f"# rotated\n{fingerprint}\n")
    assert _scan(gitleaks, repo).returncode == 0
    # the fingerprint names one commit: the same key committed again is caught
    (repo / "src/ayasee/y.py").write_text(f'ids = ["{AWS_KEY}"]\n')
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "d"],
        cwd=repo,
        check=True,
    )
    assert _scan(gitleaks, repo).returncode == 1


def test_check_secrets_rejects_anything_but_a_sha_range():
    for bad in ["HEAD~1..HEAD", "--all", "a" * 40 + "..." + "b" * 40, "-p"]:
        p = subprocess.run(["bash", str(CHECK_SECRETS), bad], capture_output=True, text=True)
        assert p.returncode == 2, bad
        assert "BASE..HEAD" in p.stderr


def test_dev_tool_names_the_install_command_when_a_tool_is_missing(tmp_path):
    p = subprocess.run(
        ["bash", str(DEV_TOOL), "no-such-tool-ayasee"],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": "/usr/bin:/bin"},
    )
    assert p.returncode == 1
    assert "scripts/install-dev-tools.sh no-such-tool-ayasee" in p.stderr
