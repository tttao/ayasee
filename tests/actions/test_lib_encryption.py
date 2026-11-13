"""sg_setup_encryption from actions/lib/ayasee-lib.sh: the TF_ENCRYPTION block per key provider.

Sourced in bash; no tofu and no cloud needed. The integration tests feed the rendered aws_kms,
gcp_kms and openbao blocks to ``tofu init`` (1.13 or later, the version actions/setup pins) fully
offline and show OpenTofu accepts each and gets as far as its first call to the key service. No
test here reaches a real KMS or OpenBao server (issue #10).
"""

import json
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
GCP_KEY = "projects/ayasee-test/locations/europe-west1/keyRings/ayasee/cryptoKeys/plan-key"
# fake, well-formed service-account key file (no such key exists), multi-line like a downloaded one;
# the PEM labels are built at run time so this file holds no detectable private-key block
PEM_BEGIN, PEM_END = "-----BEGIN " + "PRIVATE KEY-----", "-----END " + "PRIVATE KEY-----"
SA_PRIVATE_KEY = (
    f"{PEM_BEGIN}\nMIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQCayaseefake\n{PEM_END}\n"
)
SA = {
    "type": "service_account",
    "project_id": "ayasee-test",
    "private_key_id": "0123456789abcdef0123456789abcdef01234567",
    "private_key": SA_PRIVATE_KEY,
    "client_email": "ayasee-plan-kms@ayasee-test.iam.gserviceaccount.com",
    "client_id": "123456789012345678901",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "universe_domain": "googleapis.com",
}
SA_JSON = json.dumps(SA, indent=2) + "\n"
GCP_ONLY_NAME = {"SG_GCP_KMS_KEY_NAME": GCP_KEY}
GCP_ONLY_KEY = {"AYASEE_GCP_KMS_CREDENTIALS": SA_JSON}
GCP_KMS = {"SG_KEY_PROVIDER": "gcp_kms", **GCP_ONLY_NAME, **GCP_ONLY_KEY}
BAO_URL = "https://bao.example.com:8200/v1/team/transit/keys/plan-key"
# fake, well-formed OpenBao service token (no such token exists)
BAO_TOKEN = "s.ayaseeTestToken0123456789abcdef"
OPENBAO = {
    "SG_KEY_PROVIDER": "openbao",
    "SG_OPENBAO_KEY_URL": BAO_URL,
    "AYASEE_OPENBAO_TOKEN": BAO_TOKEN,
}
# a clean environment: only what the test sets reaches sg_setup_encryption
_KEYS = (
    "SG_KEY_PROVIDER",
    "SG_KEY_VAULT_KEY_ID",
    "SG_KMS_KEY_ID",
    "SG_GCP_KMS_KEY_NAME",
    "AYASEE_PLAN_PASSPHRASE",
    "AYASEE_TEST_PASSPHRASE",
    "AYASEE_KMS_ACCESS_KEY_ID",
    "AYASEE_KMS_SECRET_ACCESS_KEY",
    "AYASEE_GCP_KMS_CREDENTIALS",
    "SG_OPENBAO_KEY_URL",
    "AYASEE_OPENBAO_TOKEN",
    "BAO_ADDR",
    "BAO_TOKEN",
    "BAO_SKIP_VERIFY",
    "VAULT_SKIP_VERIFY",
    "RUNNER_TEMP",
)


def _base_env():
    return {k: v for k, v in os.environ.items() if k not in _KEYS and not k.startswith("GOOGLE_")}


def _setup(**env):
    script = f'. "{LIB}"; sg_setup_encryption "$1"; printf "%s" "$TF_ENCRYPTION"'
    return subprocess.run(
        ["bash", "-c", script, "_", str(TMPL)],
        capture_output=True,
        text=True,
        env={**_base_env(), "SG_ENCRYPT": "true", **env},
    )


def _no_secret(proc):
    assert ACCESS not in proc.stderr and SECRET not in proc.stderr
    assert PASSPHRASE not in proc.stdout + proc.stderr
    for part in ("MIIEvAIBADAN", SA["private_key_id"]):
        assert part not in proc.stdout + proc.stderr
    assert BAO_TOKEN not in proc.stderr


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
    base = _base_env()
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
        (
            {"SG_KEY_PROVIDER": "vault"},
            "key-provider must be azure_vault, pbkdf2, aws_kms, gcp_kms or openbao",
        ),
        (
            {"SG_KEY_PROVIDER": "GCP_KMS"},
            "key-provider must be azure_vault, pbkdf2, aws_kms, gcp_kms or openbao",
        ),
        (
            {**GCP_KMS, "SG_KMS_KEY_ID": KEY_ARN},
            "kms-key-id is set but key-provider is gcp_kms",
        ),
        (
            {**GCP_KMS, **KMS_PAIR},
            "AYASEE_KMS_SECRET_ACCESS_KEY is set but key-provider is gcp_kms",
        ),
        (
            {**GCP_KMS, "SG_KEY_VAULT_KEY_ID": "https://kv-1.vault.azure.net/keys/plan-key"},
            "key-provider gcp_kms with key-vault-key-id set is ambiguous",
        ),
        (
            {**GCP_KMS, "AYASEE_PLAN_PASSPHRASE": PASSPHRASE},
            "AYASEE_PLAN_PASSPHRASE is set but key-provider is gcp_kms",
        ),
        (
            {**AWS_KMS, "SG_GCP_KMS_KEY_NAME": GCP_KEY},
            "gcp-kms-key-name is set but key-provider is aws_kms",
        ),
        (
            {**AWS_KMS, "AYASEE_GCP_KMS_CREDENTIALS": SA_JSON},
            "AYASEE_GCP_KMS_CREDENTIALS is set but key-provider is aws_kms",
        ),
        (
            {"SG_KEY_PROVIDER": "pbkdf2", "AYASEE_PLAN_PASSPHRASE": PASSPHRASE, **GCP_ONLY_KEY},
            "AYASEE_GCP_KMS_CREDENTIALS is set but key-provider is pbkdf2",
        ),
        (
            {"SG_KEY_VAULT_KEY_ID": "https://kv-1.vault.azure.net/keys/plan-key", **GCP_ONLY_NAME},
            "gcp-kms-key-name is set but key-provider is azure_vault",
        ),
        (
            {**GCP_ONLY_NAME, "AYASEE_TEST_PASSPHRASE": "t"},
            "both AYASEE_TEST_PASSPHRASE and gcp-kms-key-name are set",
        ),
        (
            {**GCP_ONLY_KEY, "AYASEE_TEST_PASSPHRASE": "t"},
            "both AYASEE_TEST_PASSPHRASE and AYASEE_GCP_KMS_CREDENTIALS are set",
        ),
        (
            {**OPENBAO, "SG_GCP_KMS_KEY_NAME": GCP_KEY},
            "gcp-kms-key-name is set but key-provider is openbao",
        ),
        (
            {**OPENBAO, **GCP_ONLY_KEY},
            "AYASEE_GCP_KMS_CREDENTIALS is set but key-provider is openbao",
        ),
        (
            {**OPENBAO, "SG_KMS_KEY_ID": KEY_ARN},
            "kms-key-id is set but key-provider is openbao",
        ),
        (
            {**OPENBAO, "AYASEE_PLAN_PASSPHRASE": PASSPHRASE},
            "AYASEE_PLAN_PASSPHRASE is set but key-provider is openbao",
        ),
        (
            {**OPENBAO, "SG_KEY_VAULT_KEY_ID": "https://kv-1.vault.azure.net/keys/plan-key"},
            "key-provider openbao with key-vault-key-id set is ambiguous",
        ),
        (
            {**GCP_KMS, "SG_OPENBAO_KEY_URL": BAO_URL},
            "openbao-key-url is set but key-provider is gcp_kms",
        ),
        (
            {**AWS_KMS, "AYASEE_OPENBAO_TOKEN": BAO_TOKEN},
            "AYASEE_OPENBAO_TOKEN is set but key-provider is aws_kms",
        ),
        (
            {
                "SG_KEY_VAULT_KEY_ID": "https://kv-1.vault.azure.net/keys/plan-key",
                "SG_OPENBAO_KEY_URL": BAO_URL,
            },
            "openbao-key-url is set but key-provider is azure_vault",
        ),
        (
            {"SG_OPENBAO_KEY_URL": BAO_URL, "AYASEE_TEST_PASSPHRASE": "t"},
            "both AYASEE_TEST_PASSPHRASE and openbao-key-url are set",
        ),
        (
            {"AYASEE_OPENBAO_TOKEN": BAO_TOKEN, "AYASEE_TEST_PASSPHRASE": "t"},
            "both AYASEE_TEST_PASSPHRASE and AYASEE_OPENBAO_TOKEN are set",
        ),
    ],
)
def test_ambiguous_or_unknown_key_sources_are_refused(env, message):
    proc = _setup(**env)
    assert proc.returncode != 0
    assert message in proc.stderr
    assert proc.stdout == ""
    _no_secret(proc)


def _gcp_block(name: str, credentials: str) -> str:
    return f'''key_provider "gcp_kms" "ayasee" {{
  kms_encryption_key = "{name}"
  key_length         = 32
  credentials        = "{credentials}"
}}

method "aes_gcm" "ayasee" {{
  keys = key_provider.gcp_kms.ayasee
}}

plan {{
  method   = method.aes_gcm.ayasee
  enforced = true
}}'''


def _gcp_setup(tmp_path: Path, **env) -> tuple[subprocess.CompletedProcess, list[str]]:
    """sg_setup_encryption with RUNNER_TEMP=tmp_path; also reports, from inside the same shell,
    the credentials file (mode and content) and whether the secret is still in the environment."""
    script = (
        f'. "{LIB}"; sg_setup_encryption "$1"; printf "%s" "$TF_ENCRYPTION" >"$2/body"; '
        'echo "shell=${AYASEE_GCP_KMS_CREDENTIALS-unset}"; '
        "env | grep -c '^AYASEE_GCP_KMS_CREDENTIALS=' || true; "
        'f="${SG_GCP_CREDENTIALS_FILE:-}"; [ -n "$f" ] && stat -c %a "$f" && '
        'cmp -s "$f" <(printf "%s" "$3") && echo same'
    )
    proc = subprocess.run(
        ["bash", "-c", script, "_", str(TMPL), str(tmp_path), SA_JSON],
        capture_output=True,
        text=True,
        env={**_base_env(), "SG_ENCRYPT": "true", "RUNNER_TEMP": str(tmp_path), **env},
    )
    left = sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("ayasee-gcp-kms."))
    return proc, left


def test_gcp_kms_renders_the_key_and_a_private_credentials_file(tmp_path):
    """The key file goes to a 0600 file under RUNNER_TEMP, removed when the script exits;
    TF_ENCRYPTION carries only its path and the secret leaves the environment."""
    proc, left = _gcp_setup(tmp_path, **GCP_KMS)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["shell=unset", "0", "600", "same"]
    body = (tmp_path / "body").read_text()
    m = re.search(r'credentials        = "([^"]+)"', body)
    assert m and re.fullmatch(rf"{re.escape(str(tmp_path))}/ayasee-gcp-kms\.[A-Za-z0-9]{{8}}", m[1])
    assert body == _gcp_block(GCP_KEY, m[1])
    assert left == []  # removed by the EXIT trap
    _no_secret(proc)
    assert "MIIEvAIBADAN" not in body and SA["private_key_id"] not in body


@pytest.mark.parametrize(
    "var",
    [
        "GOOGLE_OAUTH_ACCESS_TOKEN",
        "GOOGLE_IMPERSONATE_SERVICE_ACCOUNT",
        "GOOGLE_BACKEND_IMPERSONATE_SERVICE_ACCOUNT",
    ],
)
def test_gcp_kms_refuses_a_google_identity_of_the_step(tmp_path, var):
    """OpenTofu would prefer it to the credentials file (checked for the access token)."""
    proc, left = _gcp_setup(tmp_path, **{**GCP_KMS, var: "x"})
    assert proc.returncode != 0
    assert f"{var} is set in this step" in proc.stderr
    assert left == [] and not (tmp_path / "body").exists()
    _no_secret(proc)


@pytest.mark.parametrize("temp", ["relative/dir", "/tmp/it's", "/tmp/a b"])
def test_gcp_kms_refuses_an_unusual_runner_temp(tmp_path, temp):
    proc, _ = _gcp_setup(tmp_path, **{**GCP_KMS, "RUNNER_TEMP": temp})
    assert proc.returncode != 0
    assert "needs RUNNER_TEMP to be an absolute path" in proc.stderr
    _no_secret(proc)


def test_gcp_kms_accepts_a_domain_scoped_project(tmp_path):
    name = "projects/example.com:ayasee-prod/locations/global/keyRings/ring_1/cryptoKeys/plan-key"
    proc, _ = _gcp_setup(tmp_path, **{**GCP_KMS, "SG_GCP_KMS_KEY_NAME": name})
    assert proc.returncode == 0, proc.stderr
    assert f'kms_encryption_key = "{name}"' in (tmp_path / "body").read_text()


@pytest.mark.parametrize(
    "value",
    [
        GCP_KEY + "/cryptoKeyVersions/1",  # a pinned version breaks rotation
        "projects/ayasee-test/locations/europe-west1/keyRings/ayasee",
        "plan-key",
        GCP_KEY.replace("projects/", "project/"),
        GCP_KEY.replace("ayasee-test", "Ayasee-Test"),
        f'{GCP_KEY}" }} evil {{ x = "',
        f"{GCP_KEY}${{var.x}}",
        f"{GCP_KEY}\nmore",
        f" {GCP_KEY}",
        f"//cloudkms.googleapis.com/{GCP_KEY}",
    ],
)
def test_gcp_kms_refuses_a_malformed_key_name(tmp_path, value):
    proc, left = _gcp_setup(tmp_path, **{**GCP_KMS, "SG_GCP_KMS_KEY_NAME": value})
    assert proc.returncode != 0
    assert "gcp-kms-key-name must look like projects/" in proc.stderr
    assert left == [] and not (tmp_path / "body").exists()
    _no_secret(proc)


@pytest.mark.parametrize(
    "credentials",
    [
        "not json",
        SA_JSON[:-10],
        json.dumps([SA]),
        json.dumps({**SA, "type": "authorized_user"}),
        # an external_account file names a URL or command of its own to fetch a token
        json.dumps({**SA, "type": "external_account"}),
        json.dumps({**SA, "token_uri": "https://attacker.example/token"}),
        json.dumps({k: v for k, v in SA.items() if k != "private_key"}),
        json.dumps({**SA, "private_key": "AAAA"}),
        json.dumps({**SA, "client_email": "someone@gmail.com"}),
    ],
)
def test_gcp_kms_refuses_anything_but_a_service_account_key(tmp_path, credentials):
    proc, left = _gcp_setup(tmp_path, **{**GCP_KMS, "AYASEE_GCP_KMS_CREDENTIALS": credentials})
    assert proc.returncode != 0
    assert "AYASEE_GCP_KMS_CREDENTIALS must be a Google service-account key file" in proc.stderr
    assert left == [] and not (tmp_path / "body").exists()
    _no_secret(proc)


@pytest.mark.parametrize(
    ("env", "message"),
    [
        (GCP_ONLY_KEY, "key-provider gcp_kms needs the gcp-kms-key-name input"),
        (GCP_ONLY_NAME, "key-provider gcp_kms needs AYASEE_GCP_KMS_CREDENTIALS"),
    ],
)
def test_gcp_kms_needs_the_key_and_the_credentials(env, message):
    proc = _setup(SG_KEY_PROVIDER="gcp_kms", **env)
    assert proc.returncode != 0
    assert message in proc.stderr
    assert proc.stdout == ""
    _no_secret(proc)


def _bao_block(address: str, path: str, name: str) -> str:
    return f'''key_provider "openbao" "ayasee" {{
  address             = "{address}"
  transit_engine_path = "{path}"
  key_name            = "{name}"
  key_length          = 32
  token               = "{BAO_TOKEN}"
}}

method "aes_gcm" "ayasee" {{
  keys = key_provider.openbao.ayasee
}}

plan {{
  method   = method.aes_gcm.ayasee
  enforced = true
}}'''


@pytest.mark.parametrize(
    ("url", "address", "path", "name"),
    [
        (BAO_URL, "https://bao.example.com:8200", "/team/transit", "plan-key"),
        (
            "https://bao.internal/v1/transit/keys/ayasee_plan",
            "https://bao.internal",
            "/transit",
            "ayasee_plan",
        ),
        ("https://10.0.0.7:8200/v1/keys/keys/k", "https://10.0.0.7:8200", "/keys", "k"),
    ],
)
def test_openbao_renders_the_address_the_mount_the_key_and_the_token(url, address, path, name):
    proc = _setup(**{**OPENBAO, "SG_OPENBAO_KEY_URL": url})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == _bao_block(address, path, name)
    _no_secret(proc)


def test_openbao_unsets_the_token_after_rendering():
    script = (
        f'. "{LIB}"; sg_setup_encryption "$1"; echo "shell=${{AYASEE_OPENBAO_TOKEN-unset}}"; '
        "env | grep -c '^AYASEE_OPENBAO_TOKEN=' || true"
    )
    proc = subprocess.run(
        ["bash", "-c", script, "_", str(TMPL)],
        capture_output=True,
        text=True,
        env={**_base_env(), "SG_ENCRYPT": "true", **OPENBAO},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["shell=unset", "0"]


@pytest.mark.parametrize(
    "value",
    [
        "http://bao.example.com:8200/v1/transit/keys/plan-key",  # no plain HTTP
        "https://bao.example.com:8200/transit/keys/plan-key",
        "https://bao.example.com:8200/v1/transit/keys/plan-key/",
        "https://bao.example.com:8200/v1/transit/keys/",
        "https://bao.example.com:8200/v1/transit/datakey/plaintext/plan-key",
        "https://bao.example.com:8200/v1/transit/keys/plan-key?version=1",
        "https://user@bao.example.com/v1/transit/keys/plan-key",
        "https://bao.example.com:8200/v1/../transit/keys/plan-key",
        f'{BAO_URL}" }} evil {{ x = "',
        f"{BAO_URL}${{var.x}}",
        f"{BAO_URL}\nmore",
        f" {BAO_URL}",
    ],
)
def test_openbao_refuses_a_malformed_key_url(value):
    proc = _setup(**{**OPENBAO, "SG_OPENBAO_KEY_URL": value})
    assert proc.returncode != 0
    assert "openbao-key-url must look like https://<host>" in proc.stderr
    assert proc.stdout == ""
    _no_secret(proc)


@pytest.mark.parametrize(
    ("token", "message"),
    [
        ("", "key-provider openbao needs AYASEE_OPENBAO_TOKEN"),
        ("s.short", "16 to 1024 chars of"),
        (BAO_TOKEN + '"', "16 to 1024 chars of"),
        ("${var.x}" + BAO_TOKEN, "16 to 1024 chars of"),
        (BAO_TOKEN + "\n", "16 to 1024 chars of"),
        (BAO_TOKEN + "é", "16 to 1024 chars of"),
    ],
)
def test_openbao_refuses_a_missing_or_malformed_token(token, message):
    proc = _setup(**{**OPENBAO, "AYASEE_OPENBAO_TOKEN": token})
    assert proc.returncode != 0
    assert message in proc.stderr
    assert proc.stdout == ""
    _no_secret(proc)


@pytest.mark.parametrize("var", ["BAO_SKIP_VERIFY", "VAULT_SKIP_VERIFY"])
def test_openbao_refuses_to_skip_tls_verification(var):
    proc = _setup(**{**OPENBAO, var: "true"})
    assert proc.returncode != 0
    assert f"{var} is set in this step" in proc.stderr
    assert proc.stdout == ""
    _no_secret(proc)


def test_openbao_needs_the_key_url():
    proc = _setup(SG_KEY_PROVIDER="openbao", AYASEE_OPENBAO_TOKEN=BAO_TOKEN)
    assert proc.returncode != 0
    assert "key-provider openbao needs the openbao-key-url input" in proc.stderr


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


def _offline_env(work: Path, body: str = "") -> dict[str, str]:
    """No Google or OpenBao configuration of the developer, and every HTTP(S) or gRPC
    connection forced through a proxy on a closed local port, so nothing leaves the machine."""
    nowhere = "http://127.0.0.1:1"
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(work),
        "TF_IN_AUTOMATION": "1",
        "HTTPS_PROXY": nowhere,
        "HTTP_PROXY": nowhere,
        "https_proxy": nowhere,
        "http_proxy": nowhere,
        "NO_PROXY": "",
        "no_proxy": "",
        "CLOUDSDK_CONFIG": str(work / "no-gcloud"),
    }
    if body:
        env["TF_ENCRYPTION"] = body
    return env


@pytest.mark.integration
def test_tofu_reads_the_rendered_gcp_kms_block_and_its_credentials_file_offline(tmp_path):
    """OpenTofu parses the block, loads the service-account key from the private file and makes
    its first KMS call, which the proxy on a closed local port stops (OpenTofu reports a deadline
    after about a minute). A credentials path that does not exist fails earlier with its own
    message, so the assertion is not vacuous. A real Encrypt/Decrypt round trip is not tested.
    """
    tofu = _tofu()
    if not tofu:
        pytest.skip("tofu 1.13 or later is required (actions/setup pins 1.13.9)")
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("openssl is required to make a throwaway service-account key")
    # a real RSA key, so OpenTofu gets past parsing it; it belongs to no service account
    pem = subprocess.run(
        [openssl, "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    creds = json.dumps({**SA, "private_key": pem})
    work = tmp_path / "work"
    work.mkdir()
    (work / "main.tf").write_text('output "x" {\n  value = 1\n}\n')
    script = (
        f'. "{LIB}"; sg_setup_encryption "$1"; cd "$2"; '
        '"$3" init -input=false -no-color; rc=$?; '
        'printf "%s" "$TF_ENCRYPTION" >"$2/body"; exit "$rc"'
    )
    env = {
        **_offline_env(work),
        "SG_ENCRYPT": "true",
        "RUNNER_TEMP": str(tmp_path),
        **GCP_KMS,
        "AYASEE_GCP_KMS_CREDENTIALS": creds,
    }
    proc = subprocess.run(
        ["bash", "-c", script, "_", str(TMPL), str(work), tofu],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "key_provider.gcp_kms.ayasee failed with error: failed to encrypt key" in out, out
    assert "Unsupported argument" not in out and "Missing required argument" not in out, out
    assert "neither valid json nor a valid file path" not in out, out
    assert PEM_BEGIN not in out
    assert [p for p in tmp_path.iterdir() if p.name.startswith("ayasee-gcp-kms.")] == []

    # the same block with the credentials file gone fails on reading it, before any call
    body = (work / "body").read_text()
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "main.tf").write_text('output "x" {\n  value = 1\n}\n')
    proc = subprocess.run(
        [tofu, "init", "-input=false", "-no-color"],
        cwd=bad,
        capture_output=True,
        text=True,
        env=_offline_env(bad, body),
        timeout=300,
    )
    assert proc.returncode != 0
    assert "neither valid json nor a valid file path" in proc.stdout + proc.stderr


@pytest.mark.integration
def test_tofu_sends_the_rendered_openbao_block_to_the_transit_mount_offline(tmp_path):
    """OpenTofu parses the block and asks OpenBao for a data key at the mount and key name taken
    from the key URL; the address is a closed local port, so the call is refused. A real
    datakey/decrypt round trip against an OpenBao server is not tested."""
    tofu = _tofu()
    if not tofu:
        pytest.skip("tofu 1.13 or later is required (actions/setup pins 1.13.9)")
    url = "https://127.0.0.1:1/v1/team/transit/keys/plan-key"
    body = _setup(**{**OPENBAO, "SG_OPENBAO_KEY_URL": url}).stdout
    assert body
    work = tmp_path / "ok"
    work.mkdir()
    (work / "main.tf").write_text('output "x" {\n  value = 1\n}\n')
    env = {**_offline_env(work, body), "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
    proc = subprocess.run(
        [tofu, "init", "-input=false", "-no-color"],
        cwd=work,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "https://127.0.0.1:1/v1/team/transit/datakey/plaintext/plan-key" in out, out
    assert "connection refused" in out, out
    assert BAO_TOKEN not in out

    # the same check catches a schema error, so the assertion above is not vacuous
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "main.tf").write_text('output "x" {\n  value = 1\n}\n')
    proc = subprocess.run(
        [tofu, "init", "-input=false", "-no-color"],
        cwd=bad,
        capture_output=True,
        text=True,
        env=_offline_env(bad, body.replace("key_name", "key_nam")),
        timeout=300,
    )
    assert proc.returncode != 0
    assert 'An argument named "key_nam" is not expected here' in proc.stdout + proc.stderr
