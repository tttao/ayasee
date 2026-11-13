"""sg_setup_encryption from actions/lib/ayasee-lib.sh: the TF_ENCRYPTION block per key provider.

Sourced in bash; no tofu and no cloud needed. The last test (integration) feeds the rendered
aws_kms block to ``tofu init`` (1.13 or later, the version actions/setup pins) fully offline and
shows OpenTofu accepts it and gets as far as its first AWS call. No test here reaches a real KMS
(issue #10).
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "actions" / "lib" / "ayasee-lib.sh"
TMPL = REPO / "actions" / "plan" / "encryption.tofu.tmpl"
KEY_ARN = "arn:aws:kms:eu-west-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab"
PASSPHRASE = "pbkdf2+base64/passphrase=ayasee-0123456789"
# fake, well-formed IAM key pair (no such key exists)
ACCESS = "AKIAAYASEETEST000001"
SECRET = "ayaseeTestSecretKey0123456789/+abcdefghi"
KMS_PAIR = {"AYASEE_KMS_ACCESS_KEY_ID": ACCESS, "AYASEE_KMS_SECRET_ACCESS_KEY": SECRET}
AWS_KMS = {"SG_KEY_PROVIDER": "aws_kms", "SG_KMS_KEY_ID": KEY_ARN, **KMS_PAIR}
# a clean environment: only what the test sets reaches sg_setup_encryption
_KEYS = (
    "SG_KEY_PROVIDER",
    "SG_KEY_VAULT_KEY_ID",
    "SG_KMS_KEY_ID",
    "AYASEE_PLAN_PASSPHRASE",
    "AYASEE_TEST_PASSPHRASE",
    "AYASEE_KMS_ACCESS_KEY_ID",
    "AYASEE_KMS_SECRET_ACCESS_KEY",
)


def _setup(**env):
    base = {k: v for k, v in os.environ.items() if k not in _KEYS}
    script = f'. "{LIB}"; sg_setup_encryption "$1"; printf "%s" "$TF_ENCRYPTION"'
    return subprocess.run(
        ["bash", "-c", script, "_", str(TMPL)],
        capture_output=True,
        text=True,
        env={**base, "SG_ENCRYPT": "true", **env},
    )


def _no_secret(proc):
    assert ACCESS not in proc.stderr and SECRET not in proc.stderr
    assert PASSPHRASE not in proc.stdout + proc.stderr


def _aws_block(arn: str, region: str) -> str:
    return f'''key_provider "aws_kms" "ayasee" {{
  kms_key_id = "{arn}"
  region     = "{region}"
  key_spec   = "AES_256"
  access_key = "{ACCESS}"
  secret_key = "{SECRET}"

  encryption_context = {{
    ayasee = "plan"
  }}
}}

method "aes_gcm" "ayasee" {{
  keys = key_provider.aws_kms.ayasee
}}

plan {{
  method   = method.aes_gcm.ayasee
  enforced = true
}}'''


def test_aws_kms_renders_the_key_arn_its_region_and_the_kms_key_pair():
    proc = _setup(**AWS_KMS)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == _aws_block(KEY_ARN, "eu-west-1")
    _no_secret(proc)


def test_aws_kms_unsets_the_key_pair_after_rendering():
    """The pair lives only in TF_ENCRYPTION afterwards, not in the env of later child processes."""
    base = {k: v for k, v in os.environ.items() if k not in _KEYS}
    script = (
        f'. "{LIB}"; sg_setup_encryption "$1"; '
        'echo "shell=${AYASEE_KMS_ACCESS_KEY_ID-unset},${AYASEE_KMS_SECRET_ACCESS_KEY-unset}"; '
        "env | grep -c '^AYASEE_KMS_ACCESS_KEY_ID=\\|^AYASEE_KMS_SECRET_ACCESS_KEY=' || true; "
        'case "$TF_ENCRYPTION" in *"$2"*) echo rendered ;; esac'
    )
    proc = subprocess.run(
        ["bash", "-c", script, "_", str(TMPL), SECRET],
        capture_output=True,
        text=True,
        env={**base, "SG_ENCRYPT": "true", **AWS_KMS},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["shell=unset,unset", "0", "rendered"]


@pytest.mark.parametrize(
    ("arn", "region"),
    [
        ("arn:aws:kms:us-east-1:111122223333:alias/ayasee-plan", "us-east-1"),
        ("arn:aws:kms:ap-southeast-4:111122223333:alias/team/ayasee_plan", "ap-southeast-4"),
        (
            "arn:aws:kms:eu-central-1:111122223333:key/mrk-1234abcd12ab34cd56ef1234567890ab",
            "eu-central-1",
        ),
        (
            "arn:aws-us-gov:kms:us-gov-west-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab",
            "us-gov-west-1",
        ),
        (
            "arn:aws-cn:kms:cn-north-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab",
            "cn-north-1",
        ),
    ],
)
def test_aws_kms_accepts_key_alias_and_multi_region_arns(arn, region):
    proc = _setup(**{**AWS_KMS, "SG_KMS_KEY_ID": arn})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == _aws_block(arn, region)


@pytest.mark.parametrize(
    "value",
    [
        "1234abcd-12ab-34cd-56ef-1234567890ab",  # a bare key id: no region
        "alias/ayasee-plan",
        "arn:aws:kms:eu-west-1:111122223333:key/1234abcd",
        "arn:aws:s3:eu-west-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab",
        "arn:aws:kms:eu-west-1:1111:key/1234abcd-12ab-34cd-56ef-1234567890ab",
        f'{KEY_ARN}" }} evil {{ x = "',
        f"{KEY_ARN}${{var.x}}",
        f"{KEY_ARN}\nmore",
        f" {KEY_ARN}",
    ],
)
def test_aws_kms_refuses_a_malformed_key(value):
    proc = _setup(**{**AWS_KMS, "SG_KMS_KEY_ID": value})
    assert proc.returncode != 0
    assert "kms-key-id must be a KMS key or alias ARN" in proc.stderr
    assert proc.stdout == ""
    _no_secret(proc)


@pytest.mark.parametrize(
    ("pair", "message"),
    [
        ({"AYASEE_KMS_ACCESS_KEY_ID": ""}, "needs AYASEE_KMS_ACCESS_KEY_ID and"),
        ({"AYASEE_KMS_SECRET_ACCESS_KEY": ""}, "needs AYASEE_KMS_ACCESS_KEY_ID and"),
        # a temporary key needs a session token and expires: not for a stored secret
        ({"AYASEE_KMS_ACCESS_KEY_ID": "ASIAAYASEETEST000001"}, "long-term IAM access key id"),
        ({"AYASEE_KMS_ACCESS_KEY_ID": "AKIAayaseetest000001"}, "long-term IAM access key id"),
        ({"AYASEE_KMS_ACCESS_KEY_ID": ACCESS + "X"}, "long-term IAM access key id"),
        ({"AYASEE_KMS_ACCESS_KEY_ID": 'AKIA" } x { "AAAAAAAA'}, "long-term IAM access key id"),
        ({"AYASEE_KMS_SECRET_ACCESS_KEY": SECRET[:-1]}, "40 chars of"),
        ({"AYASEE_KMS_SECRET_ACCESS_KEY": SECRET[:-1] + '"'}, "40 chars of"),
        ({"AYASEE_KMS_SECRET_ACCESS_KEY": "${var.x}" + SECRET[8:]}, "40 chars of"),
        ({"AYASEE_KMS_SECRET_ACCESS_KEY": SECRET[:-1] + "é"}, "40 chars of"),
    ],
)
def test_aws_kms_refuses_a_missing_or_malformed_key_pair(pair, message):
    proc = _setup(**{**AWS_KMS, **pair})
    assert proc.returncode != 0
    assert message in proc.stderr
    assert proc.stdout == ""
    _no_secret(proc)


@pytest.mark.parametrize(
    ("env", "message"),
    [
        (
            {"SG_KEY_PROVIDER": "aws_kms", **KMS_PAIR},
            "key-provider aws_kms needs the kms-key-id input",
        ),
        (
            {**AWS_KMS, "SG_KEY_VAULT_KEY_ID": "x"},
            "key-provider aws_kms with key-vault-key-id set is ambiguous",
        ),
        (
            {**AWS_KMS, "AYASEE_PLAN_PASSPHRASE": PASSPHRASE},
            "AYASEE_PLAN_PASSPHRASE is set but key-provider is aws_kms",
        ),
        (
            {**AWS_KMS, "AYASEE_TEST_PASSPHRASE": "t"},
            "both AYASEE_TEST_PASSPHRASE and kms-key-id are set",
        ),
        (
            {**KMS_PAIR, "AYASEE_TEST_PASSPHRASE": "t"},
            "both AYASEE_TEST_PASSPHRASE and the AYASEE_KMS_* key pair are set",
        ),
        (
            {"SG_KEY_PROVIDER": "azure_vault", "SG_KMS_KEY_ID": KEY_ARN},
            "kms-key-id is set but key-provider is azure_vault",
        ),
        (
            {
                "SG_KEY_PROVIDER": "azure_vault",
                "SG_KEY_VAULT_KEY_ID": "https://kv-1.vault.azure.net/keys/plan-key",
                "AYASEE_KMS_SECRET_ACCESS_KEY": SECRET,
            },
            "AYASEE_KMS_SECRET_ACCESS_KEY is set but key-provider is azure_vault",
        ),
        (
            {"SG_KEY_PROVIDER": "pbkdf2", "AYASEE_PLAN_PASSPHRASE": PASSPHRASE, **KMS_PAIR},
            "AYASEE_KMS_SECRET_ACCESS_KEY is set but key-provider is pbkdf2",
        ),
        (
            {
                "SG_KEY_PROVIDER": "pbkdf2",
                "SG_KMS_KEY_ID": KEY_ARN,
                "AYASEE_PLAN_PASSPHRASE": PASSPHRASE,
            },
            "kms-key-id is set but key-provider is pbkdf2",
        ),
        ({"SG_KEY_PROVIDER": "gcp_kms"}, "key-provider must be azure_vault, pbkdf2 or aws_kms"),
        ({"SG_KEY_PROVIDER": "openbao"}, "key-provider must be azure_vault, pbkdf2 or aws_kms"),
    ],
)
def test_ambiguous_or_unknown_key_sources_are_refused(env, message):
    proc = _setup(**env)
    assert proc.returncode != 0
    assert message in proc.stderr
    assert proc.stdout == ""
    _no_secret(proc)


def test_azure_vault_rendering_is_unchanged():
    proc = _setup(SG_KEY_VAULT_KEY_ID="https://kv-1.vault.azure.net/keys/plan-key")
    assert proc.returncode == 0, proc.stderr
    assert (
        proc.stdout
        == """key_provider "azure_vault" "ayasee" {
  vault_uri      = "https://kv-1.vault.azure.net"
  vault_key_name = "plan-key"
  key_length     = 32
}

method "aes_gcm" "ayasee" {
  keys = key_provider.azure_vault.ayasee
}

plan {
  method   = method.aes_gcm.ayasee
  enforced = true
}"""
    )


def test_encrypt_false_ignores_the_key_inputs():
    proc = _setup(SG_ENCRYPT="false", SG_KEY_PROVIDER="aws_kms", SG_KMS_KEY_ID="junk")
    assert proc.returncode == 0 and proc.stdout == ""


def _tofu() -> str | None:
    """tofu from .tools/bin or PATH, if it is 1.13 or later (encryption_context needs 1.13)."""
    path = os.pathsep.join([str(REPO / ".tools" / "bin"), os.environ["PATH"]])
    tofu = shutil.which("tofu", path=path)
    if not tofu:
        return None
    out = subprocess.run([tofu, "version"], capture_output=True, text=True).stdout
    m = re.match(r"OpenTofu v(\d+)\.(\d+)\.", out)
    return tofu if m and (int(m[1]), int(m[2])) >= (1, 13) else None


def _tofu_init(tofu: str, work: Path, body: str) -> subprocess.CompletedProcess:
    work.mkdir()
    (work / "main.tf").write_text('output "x" {\n  value = 1\n}\n')
    nowhere = "http://127.0.0.1:1"  # every AWS endpoint points at a closed local port
    # Fully isolated from the developer's AWS setup: no inherited AWS_* variable, no
    # ~/.aws/config or credentials (credential_process included), no instance metadata.
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(work),
        "TF_IN_AUTOMATION": "1",
        "TF_ENCRYPTION": body,
        "AWS_CONFIG_FILE": str(work / "no-aws-config"),
        "AWS_SHARED_CREDENTIALS_FILE": str(work / "no-aws-credentials"),
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_ENDPOINT_URL": nowhere,
        "AWS_ENDPOINT_URL_STS": nowhere,
        "AWS_ENDPOINT_URL_KMS": nowhere,
        "AWS_MAX_ATTEMPTS": "1",
    }
    return subprocess.run(
        [tofu, "init", "-input=false", "-no-color"],
        cwd=work,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )


@pytest.mark.integration
def test_tofu_accepts_the_rendered_aws_kms_block_offline(tmp_path):
    """OpenTofu parses the block, takes the static key pair and makes its first AWS call.

    The first call (STS GetCallerIdentity, validating the key pair) goes to a closed local port,
    so nothing leaves the machine. A schema error would stop tofu earlier with another message.
    A real GenerateDataKey/Decrypt round trip is not tested.
    """
    tofu = _tofu()
    if not tofu:
        pytest.skip("tofu 1.13 or later is required (actions/setup pins 1.13.9)")
    body = _setup(**AWS_KMS).stdout
    assert body
    proc = _tofu_init(tofu, tmp_path / "ok", body)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "retrieving caller identity from STS" in out, out
    assert "127.0.0.1:1" in out and "connection refused" in out, out
    assert "Unsupported argument" not in out and "Missing required argument" not in out, out
    assert SECRET not in out

    # the same check catches a schema error, so the assertion above is not vacuous
    proc = _tofu_init(tofu, tmp_path / "bad", body.replace("key_spec", "key_spek"))
    assert proc.returncode != 0
    assert 'An argument named "key_spek" is not expected here' in proc.stdout + proc.stderr
