#!/usr/bin/env bash
# Exit 1 unless the source archive of a commit is one a GitHub runner can stage as an action (#49).
#   check-archive.sh [rev [repo-root]]      (rev defaults to HEAD)
# A caller of the reusable workflows downloads ayasee as a tarball of the commit, which never holds
# submodules. The runner fails in "Set up job" when a symlink in that tarball points at nothing, so
# this refuses:
#   - a submodule (gitlink) anywhere in the tree;
#   - a symlink whose target does not exist in the archive, or resolves outside it.
set -euo pipefail

rev="${1:-HEAD}"
repo="${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

commit="$(git -C "$repo" rev-parse --verify --quiet "$rev^{commit}")" ||
  { echo "check-archive: not a commit: $rev" >&2; exit 1; }

fail=0
while IFS= read -r path; do
  echo "check-archive: submodule in the tree (missing from the action download): $path" >&2
  fail=1
done < <(git -C "$repo" ls-tree -r "$commit" | awk -F'\t' '$1 ~ /^160000 / { print $2 }')

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git -C "$repo" archive --format=tar "$commit" | tar -x -C "$tmp"
root="$(cd "$tmp" && pwd -P)"

while IFS= read -r -d '' link; do
  rel="${link#"$tmp"/}"
  target="$(readlink -f -- "$link" 2>/dev/null || true)"
  if [ -z "$target" ] || [ ! -e "$target" ]; then
    echo "check-archive: dangling symlink: $rel -> $(readlink -- "$link")" >&2
    fail=1
  elif [ "$target" != "$root" ] && [[ "$target" != "$root"/* ]]; then
    echo "check-archive: symlink leaves the archive: $rel -> $(readlink -- "$link")" >&2
    fail=1
  fi
done < <(find "$tmp" -type l -print0)

[ "$fail" -eq 0 ] || exit 1
echo "check-archive: $(git -C "$repo" rev-parse --short "$commit"): no submodule, every symlink resolves inside the archive"
