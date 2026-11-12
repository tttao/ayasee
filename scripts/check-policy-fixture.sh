#!/usr/bin/env bash
# Prove the packs fire through conftest, with the same arguments as actions/policy/policy.sh: a
# public workspace (Databricks) and a proxied record switched to DNS-only (Cloudflare) must warn
# without stage data and fail with the prod data file. Fails the job otherwise.
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
conftest="$root/.tools/bin/conftest"
data="$root/tests/fixtures/policy/prod.data.json"
args=(test --no-color --policy "$root/policy" --namespace ayasee.databricks --namespace ayasee.cloudflare)

check() {
  local fixture="$root/tests/fixtures/policy/$1" id="$2" out rc
  out="$("$conftest" "${args[@]}" "$fixture" 2>&1)"; rc=$?
  if [ "$rc" -ne 0 ] || ! grep -q "WARN.*$id" <<<"$out"; then
    echo "expected a $id warning (exit 0) without --data, got exit $rc:" >&2
    echo "$out" >&2
    exit 1
  fi
  out="$("$conftest" "${args[@]}" --data "$data" "$fixture" 2>&1)"; rc=$?
  if [ "$rc" -ne 1 ] || ! grep -q "FAIL.*$id" <<<"$out"; then
    echo "expected a $id failure (exit 1) with --data prod.data.json, got exit $rc:" >&2
    echo "$out" >&2
    exit 1
  fi
}

check public_workspace.json SG-DBX-003
check proxy_off.json SG-CF-001

echo "policy fixture ok"
