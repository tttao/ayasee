"""``ayasee provider-env``: more provider credentials for ``identity: secrets`` (issue #11).

The reusable workflows declare their ``workflow_call`` secrets statically and never take
``secrets: inherit``, so a provider other than Cloudflare and S3 (OVH, Hetzner, Scaleway, a
second Cloudflare account) cannot get a named secret of its own. Instead the caller passes one
more environment secret, ``AYASEE_PROVIDER_ENV``: a JSON object of variable name to string value.
``.ayasee.yaml`` declares the names it may hold (``provider_env``), so the set of variables that
reaches the engine is reviewed in a pull request, not chosen by whoever writes the secret.

The plan and apply scripts call this command at their start. It:

1. refuses a name that is not declared, or that is reserved (:func:`refusal`): a variable such
   as ``LD_PRELOAD``, ``BASH_ENV``, ``NODE_OPTIONS``, ``GITHUB_*``, ``TF_CLI_ARGS`` or
   ``HTTPS_PROXY`` would change what the job runs, where its traffic goes or what it logs;
2. registers every value with ``::add-mask::``, whole and line by line, before anything else;
3. writes the pairs, NUL-separated, to a private file that the script reads and deletes, so the
   values never pass through a command line, ``$GITHUB_ENV`` or a later step.

No message names a value; an undeclared or malformed key is counted, never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path

from ayasee import cli

SECRET_ENV = "AYASEE_PROVIDER_ENV"
NAMES_ENV = "AYASEE_PROVIDER_ENV_NAMES"

# Upper-case names, or TF_VAR_<variable> where the variable name keeps its case (fullmatch only).
NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}")
TF_VAR = re.compile(r"TF_VAR_[A-Za-z_][A-Za-z0-9_]{0,120}")

# Names that change how the job, the shell, the engine or its plugins behave, or that ayasee or
# the workflows set themselves. Matching is exact.
RESERVED = frozenset(
    {
        # shell and process
        "PATH", "HOME", "USER", "LOGNAME", "SHELL", "ENV", "IFS", "PS4", "CDPATH", "GLOBIGNORE",
        "SHELLOPTS", "PROMPT_COMMAND", "TMPDIR", "TMP", "TEMP", "PWD", "OLDPWD", "SHLVL",
        "TERM", "HOSTNAME", "CI", "EDITOR", "VISUAL", "PAGER",
        # interpreters and loaders
        "PERL5LIB", "PERL5OPT", "PERLLIB", "RUBYOPT", "RUBYLIB", "JAVA_TOOL_OPTIONS",
        "JDK_JAVA_OPTIONS", "CLASSPATH", "GODEBUG", "GOFLAGS", "GOROOT", "GOPATH",
        "GOTRACEBACK", "OPENSSL_CONF", "OPENSSL_MODULES", "OPENSSL_ENGINES", "GCONV_PATH",
        "LOCPATH", "NLSPATH", "MALLOC_CHECK_",
        # bash special variables: the scripts export into their own shell, where these are
        # read-only (UID, EUID, PPID: the export fails) or change how bash runs the script
        "UID", "EUID", "PPID", "GROUPS", "POSIXLY_CORRECT", "FUNCNEST", "TMOUT", "EXECIGNORE",
        "OPTIND", "OPTARG", "OPTERR", "SECONDS", "RANDOM", "SRANDOM", "LINENO", "REPLY",
        "PIPESTATUS", "DIRSTACK", "FIGNORE", "FCEDIT", "GLOBSORT", "HOSTFILE", "HOSTTYPE",
        "MACHTYPE", "OSTYPE", "EPOCHSECONDS", "EPOCHREALTIME", "CHILD_MAX", "COLUMNS", "LINES",
        "TIMEFORMAT", "IGNOREEOF", "INPUTRC", "MAIL", "MAILPATH", "MAILCHECK", "MAPFILE",
        "COMPREPLY", "COPROC", "PS0", "PS1", "PS2", "PS3",
        # other shells' start-up files, libc and resolver tuning, TLS key logs, stored passwords
        "KSH_ENV", "ZDOTDIR", "GLIBC_TUNABLES", "HOSTALIASES", "RES_OPTIONS", "LOCALDOMAIN",
        "SSLKEYLOGFILE", "NETRC", "CURL_HOME",
        # TLS trust and proxies: where the traffic goes and whom it trusts
        "SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "FTP_PROXY",
        # Terraform's legacy name for TF_CLI_CONFIG_FILE (provider mirrors, credentials)
        "TERRAFORM_CONFIG",
        # set by the reusable workflows for the following steps
        "STAGE", "RUN_URL",
    }
)  # fmt: skip

# Prefixes reserved as a whole. TF_ and the Terragrunt prefixes cover TF_CLI_ARGS*, TF_LOG
# (would write provider requests, credentials included, to the log), TF_ENCRYPTION (the plan
# key), TF_CLI_CONFIG_FILE and TG_TF_PATH; TF_VAR_<variable> (an input variable of the unit)
# is the one exception. AWS_ and CLOUDFLARE_ belong to the named tokens and the state backend:
# AWS_ENDPOINT_URL*, AWS_CONFIG_FILE (credential_process runs a command), AWS_PROFILE or
# CLOUDFLARE_BASE_URL would send the named credentials elsewhere or change the backend. A second
# Cloudflare account goes through TF_VAR_<variable>.
RESERVED_PREFIXES = (
    "GITHUB_", "RUNNER_", "ACTIONS_", "INPUT_", "STATE_",
    "LD_", "DYLD_", "BASH", "PYTHON", "NODE_", "NPM_CONFIG_", "UV_", "PIP_",
    "VIRTUAL_ENV", "CONDA_", "GIT_", "SSH_", "GNUPG", "XDG_", "LC_", "LANG",
    "COMP_", "READLINE_", "HIST", "PROMPT_",
    "TF_", "TG_", "TERRAGRUNT_", "TERRAFORM_", "TOFU_", "OPENTOFU_", "CHECKPOINT_",
    "AWS_", "CLOUDFLARE_",
    "SG_", "AYASEE_",
)  # fmt: skip

# The shortest value accepted, and the shortest line of a multi-line value masked on its own.
# A mask hides every occurrence of the string in the job log AND makes the runner drop any job
# output that contains it ("Skip output ... may contain secret"): a value such as `true` would
# empty the has-changes output of prod-plan and skip prod without an error. Provider credentials
# are longer than this; a non-secret identifier (endpoint, account or zone ID) belongs in the
# unit's inputs, not in this secret.
MIN_LENGTH = 16
# The shortest line of a multi-line value masked on its own. The runner matches masks one log
# line at a time, so the lines are what hides such a value; random base64 lines of this length
# do not collide with ayasee's outputs (true, false, stage and unit names).
MIN_LINE_LENGTH = 8


class ProviderEnvError(Exception):
    """The provider environment is refused. Messages never contain a value."""


def refusal(name: str) -> str | None:
    """Why ``name`` cannot be a provider variable, or None when it can."""
    if TF_VAR.fullmatch(name):
        return None
    if not NAME.fullmatch(name):
        return (
            "not an environment variable name ([A-Z][A-Z0-9_]* or TF_VAR_<variable>, at most "
            "128 characters)"
        )
    if name in RESERVED:
        return "reserved: it changes how the job runs or is set by ayasee"
    if name.startswith(RESERVED_PREFIXES):
        return "reserved prefix: it changes how the job, the engine or ayasee runs"
    return None


def _names(text: str) -> list[str]:
    names = [n for n in (p.strip() for p in text.split(",")) if n]
    for n in names:
        reason = refusal(n)
        if reason:
            raise ProviderEnvError(f"{NAMES_ENV}: {n}: {reason}")
    return names


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ProviderEnvError(f"{SECRET_ENV} repeats a key; each name must appear once")
    return dict(pairs)


def parse(blob: str, allowed: Iterable[str]) -> dict[str, str]:
    """Parse the secret: a JSON object of declared (``allowed``) names to string values."""
    allowed = set(allowed)
    if not allowed:
        raise ProviderEnvError(
            f"{SECRET_ENV} is set but .ayasee.yaml declares no provider_env names; "
            "declare the variable names it may hold"
        )
    try:
        data = json.loads(blob, object_pairs_hook=_no_duplicates)
    except json.JSONDecodeError as exc:
        # the position only: exc.msg and exc.doc could quote the secret
        raise ProviderEnvError(
            f"{SECRET_ENV} is not valid JSON (line {exc.lineno}, column {exc.colno})"
        ) from None
    if not isinstance(data, dict):
        raise ProviderEnvError(f"{SECRET_ENV} must be a JSON object of name to string value")
    undeclared = 0
    out: dict[str, str] = {}
    for key, value in data.items():
        if key not in allowed:
            undeclared += 1
            continue
        if not isinstance(value, str):
            raise ProviderEnvError(f"{SECRET_ENV}: {key}: the value must be a JSON string")
        if "\0" in value:
            raise ProviderEnvError(f"{SECRET_ENV}: {key}: the value contains a NUL character")
        try:
            value.encode()
        except UnicodeEncodeError:
            raise ProviderEnvError(
                f"{SECRET_ENV}: {key}: the value is not valid Unicode (a lone surrogate)"
            ) from None
        if len(value.strip()) < MIN_LENGTH:
            raise ProviderEnvError(
                f"{SECRET_ENV}: {key}: the value is shorter than {MIN_LENGTH} characters; its "
                "mask would hide job outputs (a non-secret identifier belongs in the unit inputs)"
            )
        # a multi-line value is hidden by its lines only: every line must be maskable, except
        # one (the short tail of a base64 block, which in a PEM key sits before the END line)
        lines = value.rstrip("\r\n").splitlines()
        if len(lines) > 1 and sum(len(ln.strip()) < MIN_LINE_LENGTH for ln in lines) > 1:
            raise ProviderEnvError(
                f"{SECRET_ENV}: {key}: more than one line of the multi-line value is shorter "
                f"than {MIN_LINE_LENGTH} characters and could not be masked (write the value on "
                "one line, e.g. minified JSON)"
            )
        out[key] = value
    if undeclared:
        # never print the key: a malformed secret can put a value where a name should be
        raise ProviderEnvError(
            f"{SECRET_ENV} holds {undeclared} key(s) that provider_env in .ayasee.yaml does "
            "not declare; declare them or remove them from the secret"
        )
    return out


def _escape(data: str) -> str:
    """Escape workflow-command data (the runner unescapes %25, %0D and %0A)."""
    return data.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def mask_commands(value: str) -> list[str]:
    """``::add-mask::`` lines for ``value``: the value, and each line of a multi-line value.

    The runner matches a mask against one log line at a time, so a mask that contains a newline
    never matches: a multi-line value (a PEM key) is hidden by its lines only. A line shorter
    than MIN_LINE_LENGTH (the tail of a base64 block) is not masked, since such a mask would
    drop job outputs; it can appear in the log.
    """
    pieces: list[str] = []
    for piece in [value, *value.splitlines()]:
        minimum = MIN_LENGTH if piece == value else MIN_LINE_LENGTH
        for candidate in (piece, piece.strip()):
            if len(candidate.strip()) >= minimum and candidate not in pieces:
                pieces.append(candidate)
    return [f"::add-mask::{_escape(p)}" for p in pieces]


def _run(args: argparse.Namespace) -> int:
    blob = os.environ.get(SECRET_ENV, "")
    try:
        names = _names(os.environ.get(NAMES_ENV, ""))
        pairs = parse(blob, names) if blob.strip() else {}
    except ProviderEnvError as exc:
        print(f"ayasee: provider-env: {exc}", file=sys.stderr)
        return 1
    # masks first, so a value is hidden before anything can print it
    for value in pairs.values():
        for line in mask_commands(value):
            print(line, flush=True)
    out = Path(args.out)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        for key, value in pairs.items():
            fh.write(key.encode() + b"\0" + value.encode() + b"\0")
    if pairs:
        print(f"ayasee: provider-env: exporting {', '.join(sorted(pairs))}", file=sys.stderr)
    return 0


def _setup(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        f"Read {SECRET_ENV} (JSON) and {NAMES_ENV} (declared names) from the environment, "
        "mask every value and write NUL-separated name/value pairs to --out (a new file)."
    )
    parser.add_argument("--out", required=True, help="file to create (mode 600, must not exist)")
    parser.set_defaults(handler=_run)


cli.register("provider-env", _setup)
