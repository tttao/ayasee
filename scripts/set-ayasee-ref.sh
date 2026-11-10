#!/usr/bin/env bash
# Point the own-action references of the iac-staged workflow at a concrete place.
#   set-ayasee-ref.sh <owner/repo> <source-sha>   release: OWNER/ayasee/actions/x@<sha>
#   set-ayasee-ref.sh --local                     in-repo e2e: ./actions/x
# Only lines marked "# AYASEE_REF" change, plus (cockpit-collect) the checkout pair marked
# "# AYASEE_REPO" (repository:) and "# AYASEE_SHA" (ref:), which select the ayasee source. The release form refuses unless actions/, policy/
# and src/ at HEAD are identical to <source-sha>: the pinned actions then run exactly the code
# that is released, and only the workflow file differs between the two commits.
# Optional last argument: the workflow file (default .github/workflows/iac-staged.yml).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
die() { echo "set-ayasee-ref: $*" >&2; exit 1; }

if [ "${1:-}" = --local ]; then
  mode=local
  file="${2:-$root/.github/workflows/iac-staged.yml}"
else
  [ "$#" -ge 2 ] || die "usage: $0 <owner/repo> <source-sha> [file] | --local [file]"
  mode=release
  repo="$1"
  sha="$2"
  file="${3:-$root/.github/workflows/iac-staged.yml}"
  [[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "invalid repository: $repo"
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "source-sha must be a full 40-character commit SHA"
  git -C "$root" cat-file -e "$sha^{commit}" 2>/dev/null || die "unknown commit: $sha"
  git -C "$root" diff --quiet "$sha" HEAD -- actions/ policy/ src/ pyproject.toml uv.lock ||
    die "actions/, policy/, src/, pyproject.toml or uv.lock differ between $sha and HEAD; use the commit that holds the released code"
fi
[ -f "$file" ] || die "no such file: $file"

tmp="$(mktemp "$file.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
pat='[^ ]*/ayasee/actions/\([a-z-]*\)@[0-9a-f]\{40\} # AYASEE_REF'
if [ "$mode" = local ]; then
  sed -e "s|$pat|./actions/\\1 # AYASEE_REF|" \
    -e 's|\(repository:\) [^ ]* # AYASEE_REPO|\1 ${{ github.repository }} # AYASEE_REPO|' \
    -e 's|\(ref:\) [^ ]* # AYASEE_SHA|\1 ${{ github.sha }} # AYASEE_SHA|' "$file" >"$tmp"
else
  sed -e "s|$pat|$repo/actions/\\1@$sha # AYASEE_REF|" \
    -e "s|\\(repository:\\) [^ ]* # AYASEE_REPO|\\1 $repo # AYASEE_REPO|" \
    -e "s|\\(ref:\\) [^ ]* # AYASEE_SHA|\\1 $sha # AYASEE_SHA|" "$file" >"$tmp"
fi
chmod 644 "$tmp"
mv "$tmp" "$file"
