"""tool-mirror: the URL rewrite, https-only validation, and mandatory checksums."""

import hashlib
import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "actions" / "lib" / "ayasee-lib.sh"
DEV_TOOLS = REPO / "scripts" / "install-dev-tools.sh"
GH = "https://github.com/open-policy-agent/opa/releases/download/v1.0.0/opa"


def _bash(script: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. "{LIB}"; {script}'],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )


def test_no_mirror_leaves_the_url_alone():
    p = _bash(f'sg_mirror_url "" "{GH}"')
    assert p.returncode == 0 and p.stdout.strip() == GH


def test_mirror_replaces_the_github_prefix():
    p = _bash(f'sg_mirror_url "https://mirror.example.com/gh" "{GH}"')
    assert p.stdout.strip() == (
        "https://mirror.example.com/gh/open-policy-agent/opa/releases/download/v1.0.0/opa"
    )


@pytest.mark.parametrize(
    "bad",
    ["http://mirror.example.com", "https://mirror.example.com/", "mirror.example.com", "ftp://x"],
)
def test_bad_mirror_is_rejected(bad):
    p = _bash(f'sg_check_mirror "{bad}"')
    assert p.returncode != 0
    assert "tool-mirror" in p.stderr


def test_good_mirror_is_accepted():
    assert _bash('sg_check_mirror "https://mirror.example.com/gh"').returncode == 0
    assert _bash('sg_check_mirror ""').returncode == 0


def test_dev_tools_rejects_an_http_mirror(tmp_path):
    p = subprocess.run(
        ["bash", str(DEV_TOOLS), "--mirror", "http://mirror.example.com", "opa"],
        capture_output=True,
        text=True,
    )
    assert p.returncode != 0 and "https" in p.stderr


def _fake_curl(tmp_path: Path, payload: bytes) -> Path:
    shim = tmp_path / "shim"
    shim.mkdir()
    (tmp_path / "payload").write_bytes(payload)
    curl = shim / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        'out=""; url=""\n'
        'while [ $# -gt 0 ]; do case "$1" in\n'
        '  -o) out="$2"; shift 2;; -*) shift;; *) url="$1"; shift;;\n'
        "esac; done\n"
        f'echo "$url" >> "{tmp_path}/urls"\n'
        f'cp "{tmp_path}/payload" "$out"\n'
    )
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)
    return shim


def _run_dev_tools(tmp_path, payload: bytes, sha_script: str):
    """Copy the script into a sandbox root so it installs into tmp_path/.tools/bin."""
    root = tmp_path / "root"
    (root / "scripts").mkdir(parents=True)
    (root / "actions" / "lib").mkdir(parents=True)
    (root / "scripts" / "install-dev-tools.sh").write_text(sha_script)
    (root / "actions" / "lib" / "ayasee-lib.sh").write_text(LIB.read_text())
    shim = _fake_curl(tmp_path, payload)
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "install-dev-tools.sh"),
            "--mirror",
            "https://mirror.example.com/gh",
            "opa",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{shim}:{os.environ['PATH']}"},
    )
    return root, proc


def _script_with_opa_sha(sha: str) -> str:
    import re

    text = DEV_TOOLS.read_text()
    return re.sub(r"^OPA_SHA256=[0-9a-f]{64}", f"OPA_SHA256={sha}", text, flags=re.M)


def test_dev_tools_downloads_from_the_mirror_and_still_checks_the_checksum(tmp_path):
    payload = b"#!/bin/sh\necho opa\n"
    good = hashlib.sha256(payload).hexdigest()
    root, ok = _run_dev_tools(tmp_path, payload, _script_with_opa_sha(good))
    assert ok.returncode == 0, ok.stderr
    urls = (tmp_path / "urls").read_text().split()
    assert urls == [
        "https://mirror.example.com/gh/open-policy-agent/opa/releases/download/"
        f"v{_opa_version()}/opa_linux_amd64_static"
    ]
    assert (root / ".tools" / "bin" / "opa").exists()


def test_dev_tools_rejects_a_tampered_download_from_the_mirror(tmp_path):
    good = hashlib.sha256(b"genuine").hexdigest()
    root, bad = _run_dev_tools(tmp_path, b"tampered", _script_with_opa_sha(good))
    assert bad.returncode != 0 and "checksum mismatch" in bad.stderr
    assert not (root / ".tools" / "bin" / "opa").exists()


def _opa_version() -> str:
    import re

    return re.search(r"^OPA_VERSION=(\S+)", DEV_TOOLS.read_text(), re.M).group(1)


@pytest.mark.parametrize("action", ["setup", "policy"])
def test_actions_expose_and_pass_tool_mirror(action):
    spec = yaml.safe_load((REPO / "actions" / action / "action.yml").read_text())
    assert spec["inputs"]["tool-mirror"]["default"] == ""
    env = next(s["env"] for s in spec["runs"]["steps"] if "env" in s and "run" in s)
    assert env["SG_TOOL_MIRROR"] == "${{ inputs.tool-mirror }}"


@pytest.mark.parametrize(
    "wf", ["iac-pr", "iac-staged", "iac-nightly", "iac-breakglass", "cockpit-collect"]
)
def test_workflows_forward_tool_mirror(wf):
    doc = yaml.safe_load((REPO / ".github" / "workflows" / f"{wf}.yml").read_text())
    assert doc[True]["workflow_call"]["inputs"]["tool-mirror"]["default"] == ""
    seen = 0
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            if "/ayasee/actions/setup@" in step.get(
                "uses", ""
            ) or "/ayasee/actions/policy@" in step.get("uses", ""):
                seen += 1
                assert step["with"]["tool-mirror"] == "${{ inputs.tool-mirror }}"
    assert seen


def test_resolve_exposes_features():
    doc = yaml.safe_load((REPO / ".github" / "workflows" / "iac-staged.yml").read_text())
    resolve = doc["jobs"]["resolve"]
    assert resolve["outputs"]["features"] == "${{ steps.features.outputs.features }}"
    step = next(s for s in resolve["steps"] if s.get("id") == "features")
    assert "ayasee features --json" in step["run"]
