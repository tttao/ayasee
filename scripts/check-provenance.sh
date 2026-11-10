#!/usr/bin/env bash
# Release gate: exit 1 unless the licence stated in docs/governance/PROVENANCE.md, the license in
# pyproject.toml and the LICENSE file of the repository agree, and a NOTICE file is present.
#   check-provenance.sh [file [repo-root]]
# The "## Licence" section of the file must hold one bullet "- Licence: <id>", where <id> is exactly
# Apache-2.0 or MIT.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
file="${1:-$root/docs/governance/PROVENANCE.md}"
repo="${2:-$root}"
[ -f "$file" ] || { echo "check-provenance: no such file: $file" >&2; exit 1; }

section="$(awk '
  /^## / { insec = ($0 == "## Licence"); next }
  insec { print }
' "$file")"
[ -n "$section" ] || { echo "check-provenance: no 'Licence' section in $file" >&2; exit 1; }

line="$(printf '%s\n' "$section" | grep -F -m1 -- "- Licence:" || true)"
[ -n "$line" ] || { echo "check-provenance: field missing: Licence" >&2; exit 1; }
value="$(printf '%s' "${line#*"- Licence:"}" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"

[[ "$value" == "Apache-2.0" || "$value" == "MIT" ]] || {
  echo "check-provenance: licence must be exactly Apache-2.0 or MIT: $value" >&2
  exit 1
}
toml="$(sed -n 's/^license = { text = "\(.*\)" }$/\1/p' "$repo/pyproject.toml" 2>/dev/null || true)"
[ "$toml" = "$value" ] || {
  echo "check-provenance: pyproject.toml license ('$toml') differs from the stated licence $value" >&2
  exit 1
}
first="$(grep -m1 -v '^[[:space:]]*$' "$repo/LICENSE" 2>/dev/null | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' || true)"
case "$value:$first" in
  "Apache-2.0:Apache License" | "MIT:MIT License") ;;
  *)
    echo "check-provenance: the LICENSE file ('$first') is not the stated licence $value" >&2
    exit 1
    ;;
esac
[ -s "$repo/NOTICE" ] || { echo "check-provenance: no NOTICE file in $repo" >&2; exit 1; }
echo "check-provenance: $value in PROVENANCE.md, pyproject.toml and LICENSE; NOTICE present"
