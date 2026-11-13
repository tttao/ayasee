"""`ayasee provider-env` (issue #11): declared provider variables from one JSON secret."""

import json

import pytest

from ayasee import cli, providerenv  # noqa: F401  (registers the subcommand)
from ayasee.providerenv import ProviderEnvError, mask_commands, parse, refusal

SECRET = "s3cr3t-value-1234"
LONG = "0123456789abcdef-long-enough"  # >= MIN_LENGTH


@pytest.mark.parametrize(
    "name",
    [
        "HCLOUD_TOKEN",
        "OVH_ENDPOINT",
        "OVH_APPLICATION_KEY",
        "SCW_SECRET_KEY",
        "TF_VAR_cloudflare_api_token_2",
        "TF_VAR_hcloud_token",
        "TF_VAR_HCLOUD_TOKEN",
        "ARM_CLIENT_SECRET",
    ],
)
def test_provider_names_are_allowed(name):
    assert refusal(name) is None


@pytest.mark.parametrize(
    "name",
    [
        # loaders, shells and interpreters: code execution in the job
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "BASH_ENV",
        "BASHOPTS",
        "ENV",
        "PATH",
        "IFS",
        "PS4",
        "PROMPT_COMMAND",
        "SHELLOPTS",
        "NODE_OPTIONS",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PERL5OPT",
        "RUBYOPT",
        "JAVA_TOOL_OPTIONS",
        "GODEBUG",
        "OPENSSL_CONF",
        "HOME",
        "TMPDIR",
        # the runner and the workflows
        "GITHUB_ENV",
        "GITHUB_TOKEN",
        "GITHUB_OUTPUT",
        "RUNNER_TEMP",
        "ACTIONS_RUNTIME_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        "INPUT_ENV",
        "CI",
        "STAGE",
        # the engine and Terragrunt: arguments, logs, plan encryption, binaries
        "TF_CLI_ARGS",
        "TF_CLI_ARGS_apply",
        "TF_LOG",
        "TF_LOG_PATH",
        "TF_ENCRYPTION",
        "TF_CLI_CONFIG_FILE",
        "TF_PLUGIN_CACHE_DIR",
        "TF_VAR_",
        "TG_TF_PATH",
        "TERRAGRUNT_TFPATH",
        # ayasee itself
        "SG_ENV",
        "SG_BINDING_MODE",
        "AYASEE_PLAN_PASSPHRASE",
        "AYASEE_TEST_PASSPHRASE",
        "AYASEE_PROVIDER_ENV",
        # git, ssh, TLS trust and proxies
        "GIT_CONFIG_COUNT",
        "GIT_SSH_COMMAND",
        "SSH_AUTH_SOCK",
        "SSL_CERT_FILE",
        "CURL_CA_BUNDLE",
        "AWS_CA_BUNDLE",
        "HTTPS_PROXY",
        "NO_PROXY",
        "XDG_CONFIG_HOME",
        "LANG",
        "LC_ALL",
        # the named identity: secrets tokens and the state backend keep their own families
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_API_TOKEN_2",
        "CLOUDFLARE_BASE_URL",
        "CLOUDFLARE_API_HOSTNAME",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_ENDPOINT_URL",
        "AWS_ENDPOINT_URL_S3",
        "AWS_CONFIG_FILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_PROFILE",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        # Terraform's legacy CLI configuration variable
        "TERRAFORM_CONFIG",
        # bash special variables: read-only, or change how the script runs
        "UID",
        "EUID",
        "PPID",
        "GROUPS",
        "POSIXLY_CORRECT",
        "FUNCNEST",
        "TMOUT",
        "EXECIGNORE",
        "OPTIND",
        "OPTARG",
        "OPTERR",
        "SECONDS",
        "RANDOM",
        "SRANDOM",
        "LINENO",
        "HISTFILE",
        "HISTSIZE",
        "INPUTRC",
        "PS0",
        "PS1",
        "PS3",
        "MAILPATH",
        "COMP_WORDBREAKS",
        "COMPREPLY",
        "READLINE_LINE",
        "PROMPT_DIRTRIM",
        # other shells, libc and resolver, TLS key logs, stored passwords
        "KSH_ENV",
        "ZDOTDIR",
        "GLIBC_TUNABLES",
        "HOSTALIASES",
        "RES_OPTIONS",
        "LOCALDOMAIN",
        "SSLKEYLOGFILE",
        "NETRC",
        "CURL_HOME",
        # not a variable name
        "",
        "lower_case",
        "1ABC",
        "_JAVA_OPTIONS",
        "A-B",
        "A B",
        "A=B",
        "A\nB",
        "A\n",
        "TF_VAR_x\n",
        "TF_VAR_a-b",
        "TF_VAR_1x",
        "A" * 129,
    ],
)
def test_reserved_and_malformed_names_are_refused(name):
    assert refusal(name)


def test_parse_keeps_declared_string_values():
    blob = json.dumps({"HCLOUD_TOKEN": SECRET, "OVH_APPLICATION_SECRET": LONG})
    assert parse(blob, ["HCLOUD_TOKEN", "OVH_APPLICATION_SECRET", "UNUSED"]) == {
        "HCLOUD_TOKEN": SECRET,
        "OVH_APPLICATION_SECRET": LONG,
    }


def test_parse_keeps_multi_line_values_with_a_short_last_line():
    pem = f"-----BEGIN KEY-----\n{LONG}\nZw==\n-----END KEY-----\n"
    assert parse(json.dumps({"K": pem}), ["K"]) == {"K": pem}
    # trailing newlines do not count as short lines
    tail = f"{LONG}\n{LONG}\nZw==\r\n\n"
    assert parse(json.dumps({"K": tail}), ["K"]) == {"K": tail}
    # one short line anywhere is the base64 tail; 8-character lines are maskable
    assert parse(json.dumps({"K": "ab\nabcdefgh\nijklmnop"}), ["K"])


@pytest.mark.parametrize(
    ("blob", "names", "message"),
    [
        ('{"A": "x"}', [], "declares no provider_env names"),
        ("not json " + SECRET, ["A"], "not valid JSON (line 1, column 1)"),
        ('["x"]', ["A"], "must be a JSON object"),
        (json.dumps({"A": 1}), ["A"], "A: the value must be a JSON string"),
        (json.dumps({"A": {"v": SECRET}}), ["A"], "A: the value must be a JSON string"),
        (json.dumps({"A": LONG + "\0y"}), ["A"], "A: the value contains a NUL character"),
        ('{"A": "' + LONG + '\\ud800"}', ["A"], "A: the value is not valid Unicode"),
        # a mask hides every job output that contains it: `true` would empty has-changes
        (json.dumps({"A": "true"}), ["A"], "A: the value is shorter than 16 characters"),
        (json.dumps({"A": "0"}), ["A"], "shorter than 16"),
        (json.dumps({"A": ""}), ["A"], "shorter than 16"),
        (json.dumps({"A": "ovh-eu"}), ["A"], "shorter than 16"),
        (json.dumps({"A": "   short value  \n"}), ["A"], "shorter than 16"),
        # every line but one must be maskable on its own (>= 8 characters)
        (json.dumps({"A": "{\n" + LONG + "\n}"}), ["A"], "more than one line"),
        (json.dumps({"A": "abc\ndef\nghi\njkl\nmnopq"}), ["A"], "shorter than 8 characters"),
        (json.dumps({"A": "a\n\n" + LONG}), ["A"], "more than one line"),
        (json.dumps({SECRET.upper(): "x", "A": LONG}), ["A"], "holds 1 key(s)"),
        (json.dumps({"LD_PRELOAD": "/tmp/x.so"}), ["A"], "holds 1 key(s)"),
        ('{"A": "x", "A": "y"}', ["A"], "repeats a key"),
    ],
)
def test_parse_refuses_without_quoting_the_secret(blob, names, message):
    with pytest.raises(ProviderEnvError) as exc:
        parse(blob, names)
    assert message in str(exc.value)
    assert SECRET not in str(exc.value) and SECRET.upper() not in str(exc.value)
    assert "/tmp/x.so" not in str(exc.value)


def test_masks_cover_the_value_and_each_long_line():
    assert mask_commands(LONG) == [f"::add-mask::{LONG}"]
    one, two, pct = "line-one-0123456789", "line-two-0123456789", "%0A-literal-0123456789"
    cmds = mask_commands(f"{one}\r\n  {two}  \n\n{pct}\nZw==\nabcd1234")
    assert (
        f"::add-mask::{one}%0D%0A  {two}  %0A%0A%250A-literal-0123456789%0AZw==%0Aabcd1234" in cmds
    )
    assert f"::add-mask::{one}" in cmds
    assert f"::add-mask::  {two}  " in cmds
    assert f"::add-mask::{two}" in cmds
    # a literal %0A is escaped, so the runner masks those characters, not a newline
    assert "::add-mask::%250A-literal-0123456789" in cmds
    # lines of 8 characters or more are masked; a shorter one would drop job outputs
    assert "::add-mask::abcd1234" in cmds
    assert "::add-mask::Zw==" not in cmds
    assert all("\n" not in c and "\r" not in c for c in cmds)
    assert mask_commands("") == [] and mask_commands("true") == []


def _cli(monkeypatch, tmp_path, blob, names):
    monkeypatch.setenv("AYASEE_PROVIDER_ENV", blob)
    monkeypatch.setenv("AYASEE_PROVIDER_ENV_NAMES", names)
    out = tmp_path / "pairs"
    return cli.main(["provider-env", "--out", str(out)]), out


def test_cli_masks_first_then_writes_private_nul_pairs(monkeypatch, tmp_path, capsys):
    blob = json.dumps({"HCLOUD_TOKEN": SECRET, "PEM": f"{LONG}\n{SECRET}"})
    rc, out = _cli(monkeypatch, tmp_path, blob, "HCLOUD_TOKEN, PEM,")
    assert rc == 0
    cap = capsys.readouterr()
    assert cap.out.splitlines() == [
        f"::add-mask::{SECRET}",
        f"::add-mask::{LONG}%0A{SECRET}",
        f"::add-mask::{LONG}",
        f"::add-mask::{SECRET}",  # a line of PEM; the runner ignores a repeated mask
    ]
    assert cap.err == "ayasee: provider-env: exporting HCLOUD_TOKEN, PEM\n"
    assert out.read_bytes() == f"HCLOUD_TOKEN\0{SECRET}\0PEM\0{LONG}\n{SECRET}\0".encode()
    assert out.stat().st_mode & 0o777 == 0o600


def test_cli_refuses_an_existing_out_file(monkeypatch, tmp_path):
    (tmp_path / "pairs").write_text("")
    with pytest.raises(FileExistsError):
        _cli(monkeypatch, tmp_path, json.dumps({"A": LONG}), "A")


def test_cli_with_an_empty_secret_writes_an_empty_file(monkeypatch, tmp_path, capsys):
    rc, out = _cli(monkeypatch, tmp_path, "", "HCLOUD_TOKEN")
    assert rc == 0 and out.read_bytes() == b""
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    ("blob", "names", "message"),
    [
        (json.dumps({"A": SECRET}), "LD_PRELOAD", "ENV_NAMES: LD_PRELOAD: reserved"),
        (json.dumps({"A": SECRET}), "", "declares no provider_env names"),
        (json.dumps({"B": SECRET}), "A", "holds 1 key(s)"),
    ],
)
def test_cli_refusal_writes_nothing_and_masks_nothing(
    monkeypatch, tmp_path, capsys, blob, names, message
):
    rc, out = _cli(monkeypatch, tmp_path, blob, names)
    cap = capsys.readouterr()
    assert rc == 1 and not out.exists()
    assert cap.out == "" and message in cap.err and SECRET not in cap.err
