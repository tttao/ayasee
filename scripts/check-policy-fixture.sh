#!/usr/bin/env bash
# Prove the Databricks pack fires through conftest: a public workspace must warn
# without stage data and fail with the prod data file. Fails the job otherwise.
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
conftest="$root/.tools/bin/conftest"
fixture="$root/tests/fixtures/policy/public_workspace.json"
data="$root/tests/fixtures/policy/prod.data.json"
args=(test --no-color --policy "$root/policy/databricks" --namespace ayasee.databricks)

out="$("$conftest" "${args[@]}" "$fixture" 2>&1)"; rc=$?
if [ "$rc" -ne 0 ] || ! grep -q 'WARN.*SG-DBX-003' <<<"$out"; then
  echo "expected a SG-DBX-003 warning (exit 0) without --data, got exit $rc:" >&2
  echo "$out" >&2
  exit 1
fi

out="$("$conftest" "${args[@]}" --data "$data" "$fixture" 2>&1)"; rc=$?
if [ "$rc" -ne 1 ] || ! grep -q 'FAIL.*SG-DBX-003' <<<"$out"; then
  echo "expected a SG-DBX-003 failure (exit 1) with --data prod.data.json, got exit $rc:" >&2
  echo "$out" >&2
  exit 1
fi

echo "policy fixture ok"
