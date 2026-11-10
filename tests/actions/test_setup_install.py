"""actions/setup/install.sh: ayasee is installed with the `azure` extra, hash-pinned (C1)."""

import hashlib
import io
import os
import stat
import subprocess
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL = REPO / "actions" / "setup" / "install.sh"


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _tofu_tgz() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"#!/bin/sh\necho tofu\n"
        info = tarfile.TarInfo("tofu")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _run(tmp_path: Path) -> tuple[subprocess.CompletedProcess, list[str]]:
    shim = tmp_path / "shim"
    shim.mkdir()
    payload = _tofu_tgz()
    (tmp_path / "payload").write_bytes(payload)
    _exe(
        shim / "curl",
        "#!/bin/sh\nout=''\n"
        'while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2;; *) shift;; esac; done\n'
        f'cp "{tmp_path}/payload" "$out"\n',
    )
    log = tmp_path / "uv.log"
    # Fake uv: log every call; `uv venv DIR` creates DIR/bin/ayasee.
    _exe(
        shim / "uv",
        "#!/bin/bash\n"
        f'echo "VIRTUAL_ENV=${{VIRTUAL_ENV:-}} $*" >> "{log}"\n'
        'if [ "$1" = venv ]; then d="${@: -1}"; mkdir -p "$d/bin"; touch "$d/bin/ayasee"; fi\n'
        'if [ "$1" = export ]; then while [ $# -gt 0 ]; do '
        '[ "$1" = -o ] && echo "x==1 --hash=sha256:00" > "$2"; shift; done; fi\n',
    )
    sha = hashlib.sha256(payload).hexdigest()
    runner_temp = tmp_path / "rt"
    runner_temp.mkdir()
    proc = subprocess.run(
        ["bash", str(INSTALL)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{shim}:{os.environ['PATH']}",
            "GITHUB_ACTION_PATH": str(INSTALL.parent),
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_PATH": str(tmp_path / "github_path"),
            "SG_TOFU_VERSION": "1.0.0",
            "SG_TOFU_SHA256": sha,
            "SG_TERRAGRUNT_VERSION": "1.0.0",
            "SG_TERRAGRUNT_SHA256": sha,
        },
    )
    return proc, log.read_text().splitlines() if log.exists() else []


def test_install_exports_and_installs_the_azure_extra_hash_pinned(tmp_path):
    proc, calls = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    export = next(c for c in calls if " export " in c)
    assert "--frozen" in export and "--no-dev" in export
    assert "--extra azure" in export
    assert "--no-hashes" not in export  # hashes are kept and enforced below
    venv = str(tmp_path / "rt" / "ayasee-tools" / "venv")
    deps = next(c for c in calls if "--require-hashes" in c)
    assert deps.startswith(f"VIRTUAL_ENV={venv} pip install")
    project = next(c for c in calls if "--no-deps" in c)
    assert project.startswith(f"VIRTUAL_ENV={venv} pip install")
    assert project.endswith(f"{REPO}[azure]")
    # The venv's ayasee is linked onto the tools bin that goes on GITHUB_PATH.
    link = tmp_path / "rt" / "ayasee-tools" / "bin" / "ayasee"
    assert link.is_symlink() and str(link.resolve()) == f"{venv}/bin/ayasee"
    assert (tmp_path / "github_path").read_text().split() == [str(link.parent)]


def test_install_never_uses_an_unhashed_tool_install():
    text = INSTALL.read_text()
    assert "uv tool install" not in text
    assert "--no-hashes" not in text
