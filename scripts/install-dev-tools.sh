#!/usr/bin/env bash
# Install pinned dev tools (linux amd64) into .tools/bin, verifying SHA-256 checksums.
# zizmor is not installed here: run it with `uvx zizmor`.
# Usage: install-dev-tools.sh [--mirror URL] [opa|conftest|terragrunt|actionlint|gitleaks ...]
#   (no tool installs all). --mirror URL (or AYASEE_TOOL_MIRROR) is an https base without a
#   trailing slash that replaces https://github.com in the download URLs; checksums still apply.
set -euo pipefail

OPA_VERSION=1.22.2
OPA_SHA256=24e344d1fc394cd93dc75666f584cfb97c6981f7255af25f8d2979834317317b # opa_linux_amd64_static
CONFTEST_VERSION=0.71.1
CONFTEST_SHA256=c3f6b2a753bd56e377a1e51a95ae3e057f926f2e9139f7d5b5dfbbaf810f7985 # conftest_0.71.1_Linux_x86_64.tar.gz
TERRAGRUNT_VERSION=1.2.1
TERRAGRUNT_SHA256=f2f6238ad2348855a4e876b90fd31f3f5d106bb234794202a00b2bcc139fa753 # terragrunt_linux_amd64
ACTIONLINT_VERSION=1.7.12
ACTIONLINT_SHA256=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8 # actionlint_1.7.12_linux_amd64.tar.gz
GITLEAKS_VERSION=8.30.1
GITLEAKS_SHA256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb # gitleaks_8.30.1_linux_x64.tar.gz

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bin="$root/.tools/bin"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$bin"

# want TOOL: true when no tool was requested, or TOOL was.
want() {
  [ "$#" -eq 1 ] || return 1
  [ "${#requested[@]}" -eq 0 ] && return 0
  local t
  for t in "${requested[@]}"; do [ "$t" = "$1" ] && return 0; done
  return 1
}
mirror="${AYASEE_TOOL_MIRROR:-}"
if [ "${1:-}" = --mirror ]; then
  [ "$#" -ge 2 ] || { echo "--mirror needs a URL" >&2; exit 2; }
  mirror="$2"
  shift 2
fi
# shellcheck source=../actions/lib/ayasee-lib.sh
. "$root/actions/lib/ayasee-lib.sh"
sg_check_mirror "$mirror"
requested=("$@")
for t in "${requested[@]}"; do
  case "$t" in
    opa|conftest|terragrunt|actionlint|gitleaks) ;;
    *) echo "unknown tool: $t (expected opa, conftest, terragrunt, actionlint or gitleaks)" >&2; exit 2 ;;
  esac
done

# fetch URL SHA256 FILE: download and verify before anything is used.
fetch() {
  curl -fsSL --retry 3 -o "$tmp/$3" "$(sg_mirror_url "$mirror" "$1")"
  echo "$2  $tmp/$3" | sha256sum --check --quiet - || { echo "checksum mismatch: $3" >&2; exit 1; }
}

if want opa; then
  fetch "https://github.com/open-policy-agent/opa/releases/download/v${OPA_VERSION}/opa_linux_amd64_static" \
    "$OPA_SHA256" opa
  install -m 0755 "$tmp/opa" "$bin/opa"
fi

if want conftest; then
  fetch "https://github.com/open-policy-agent/conftest/releases/download/v${CONFTEST_VERSION}/conftest_${CONFTEST_VERSION}_Linux_x86_64.tar.gz" \
    "$CONFTEST_SHA256" conftest.tgz
  tar -xzf "$tmp/conftest.tgz" -C "$tmp" conftest
  install -m 0755 "$tmp/conftest" "$bin/conftest"
fi

if want terragrunt; then
  fetch "https://github.com/gruntwork-io/terragrunt/releases/download/v${TERRAGRUNT_VERSION}/terragrunt_linux_amd64" \
    "$TERRAGRUNT_SHA256" terragrunt
  install -m 0755 "$tmp/terragrunt" "$bin/terragrunt"
fi

if want actionlint; then
  fetch "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz" \
    "$ACTIONLINT_SHA256" actionlint.tgz
  tar -xzf "$tmp/actionlint.tgz" -C "$tmp" actionlint
  install -m 0755 "$tmp/actionlint" "$bin/actionlint"
fi

if want gitleaks; then
  fetch "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
    "$GITLEAKS_SHA256" gitleaks.tgz
  tar -xzf "$tmp/gitleaks.tgz" -C "$tmp" gitleaks
  install -m 0755 "$tmp/gitleaks" "$bin/gitleaks"
fi

echo "installed into $bin"
