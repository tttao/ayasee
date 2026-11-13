"""sg_load_provider_env from actions/lib/ayasee-lib.sh with the real `ayasee provider-env`."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "actions" / "lib" / "ayasee-lib.sh"
SECRET = "hcloud-s3cr3t-4321"
LINE1 = "pem line one 0123456789"
LINE2 = "pem line two 0123456789"


@pytest.fixture
def env(tmp_path):
    shim = tmp_path / "bin"
    shim.mkdir()
    cli = shim / "ayasee"
    cli.write_text(
        f"#!/bin/sh\nPYTHONPATH={REPO / 'src'} exec {sys.executable} "
        '-c "from ayasee.cli import entrypoint; entrypoint()" "$@"\n'
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "temp").mkdir()
    return {
        **{k: v for k, v in os.environ.items() if not k.startswith("AYASEE_")},
        "PATH": f"{shim}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path / "temp"),
    }


# after loading: what the engine would see, then whether the private file is gone
PROBE = """
set -euo pipefail
. "$1"
sg_load_provider_env
echo "HCLOUD=${HCLOUD_TOKEN-unset}"
printf 'PEM=%s|\\n' "${PEM-unset}"
echo "RAW=${AYASEE_PROVIDER_ENV-unset} NAMES=${AYASEE_PROVIDER_ENV_NAMES-unset}"
echo "LEFT=$(find "$RUNNER_TEMP" -mindepth 1 | wc -l)"
"""


def _run(env, **extra):
    return subprocess.run(
        ["bash", "-c", PROBE, "_", str(LIB)],
        env={**env, **extra},
        capture_output=True,
        text=True,
    )


def test_declared_values_are_masked_exported_and_the_file_removed(env):
    blob = json.dumps({"HCLOUD_TOKEN": SECRET, "PEM": f"{LINE1}\n{LINE2}"})
    proc = _run(env, AYASEE_PROVIDER_ENV=blob, AYASEE_PROVIDER_ENV_NAMES="HCLOUD_TOKEN,PEM")
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    # the masks come before anything else the step prints
    assert lines[:4] == [
        f"::add-mask::{SECRET}",
        f"::add-mask::{LINE1}%0A{LINE2}",
        f"::add-mask::{LINE1}",
        f"::add-mask::{LINE2}",
    ]
    assert f"HCLOUD={SECRET}" in lines
    assert f"PEM={LINE1}" in lines and f"{LINE2}|" in lines
    assert "RAW=unset NAMES=unset" in lines
    assert "LEFT=0" in lines
    assert SECRET not in proc.stderr


def test_an_empty_secret_exports_nothing(env):
    proc = _run(env, AYASEE_PROVIDER_ENV="", AYASEE_PROVIDER_ENV_NAMES="HCLOUD_TOKEN")
    assert proc.returncode == 0, proc.stderr
    assert "HCLOUD=unset" in proc.stdout and "::add-mask::" not in proc.stdout
    assert "RAW=unset NAMES=unset" in proc.stdout and "LEFT=0" in proc.stdout


@pytest.mark.parametrize(
    ("blob", "names"),
    [
        (json.dumps({"LD_PRELOAD": "/tmp/evil.so"}), "LD_PRELOAD"),
        (json.dumps({"BASH_ENV": "/tmp/evil.sh"}), "HCLOUD_TOKEN"),
        (json.dumps({"HCLOUD_TOKEN": SECRET, "PATH": "/tmp"}), "HCLOUD_TOKEN"),
        ("{" + SECRET, "HCLOUD_TOKEN"),
        (json.dumps({"HCLOUD_TOKEN": SECRET}), ""),
        # bash special variables: read-only (UID) or a different shell mode (POSIXLY_CORRECT)
        (json.dumps({"UID": SECRET}), "UID"),
        (json.dumps({"POSIXLY_CORRECT": SECRET}), "POSIXLY_CORRECT"),
        (json.dumps({"FUNCNEST": SECRET}), "FUNCNEST"),
        # a short value would mask job outputs such as has-changes=true
        (json.dumps({"HCLOUD_TOKEN": "true"}), "HCLOUD_TOKEN"),
    ],
)
def test_refusal_stops_the_script_before_exporting_anything(env, blob, names):
    proc = _run(env, AYASEE_PROVIDER_ENV=blob, AYASEE_PROVIDER_ENV_NAMES=names)
    assert proc.returncode != 0
    assert "refusing the provider environment" in proc.stderr
    assert "HCLOUD=" not in proc.stdout and "::add-mask::" not in proc.stdout
    assert SECRET not in proc.stdout + proc.stderr and "evil" not in proc.stderr
    assert not list(Path(env["RUNNER_TEMP"]).iterdir())


@pytest.mark.parametrize("script", ["plan/plan.sh", "apply/apply.sh"])
def test_plan_and_apply_load_the_provider_env_first(script):
    lines = (REPO / "actions" / script).read_text().splitlines()
    src = lines.index('. "$here/../lib/ayasee-lib.sh"')
    assert "sg_load_provider_env" in lines[src + 1 : src + 3]


def test_a_failed_export_still_removes_the_pairs_file(env, tmp_path):
    """Defence in depth: should a read-only name ever get past `ayasee provider-env`, the export
    fails after the file is gone, and the script stops with a clear message."""
    fake = Path(env["PATH"].split(":")[0]) / "ayasee"
    fake.write_text(f'#!/bin/bash\nout="${{@: -1}}"\nprintf \'UID\\0{SECRET}\\0\' >"$out"\n')
    proc = _run(env, AYASEE_PROVIDER_ENV="{}", AYASEE_PROVIDER_ENV_NAMES="UID")
    assert proc.returncode != 0
    assert "cannot export provider variable UID" in proc.stderr
    assert SECRET not in proc.stdout + proc.stderr
    assert not list(Path(env["RUNNER_TEMP"]).iterdir())
