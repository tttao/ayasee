#!/usr/bin/env bash
# Tell whether a new cockpit snapshot differs from the committed one, ignoring generated_at.
#   snapshot-changed.sh <committed.json> <new.json>
# Prints "changed" or "unchanged" and exits 0. A missing committed file counts as changed.
# Exits 2 on a missing or invalid new file.
set -euo pipefail

[ "$#" -eq 2 ] || { echo "usage: $0 <committed.json> <new.json>" >&2; exit 2; }
old="$1"
new="$2"
[ -f "$new" ] && jq -e . "$new" >/dev/null || { echo "snapshot-changed: invalid new snapshot: $new" >&2; exit 2; }
if [ ! -f "$old" ] || ! jq -e . "$old" >/dev/null 2>&1; then
  echo changed
  exit 0
fi
if [ "$(jq -S 'del(.generated_at)' "$old")" = "$(jq -S 'del(.generated_at)' "$new")" ]; then
  echo unchanged
else
  echo changed
fi
