#!/usr/bin/env bash
# Make the release commit of <tag> on a new local branch release/<tag>: HEAD (the source commit)
# plus one commit that points the "# AYASEE_*" lines of every reusable workflow at
# <owner/repo>@<source SHA> (scripts/set-ayasee-ref.sh). Prints source-sha=... and release-sha=...
#
#   pin-release.sh <owner/repo> <tag>
#
# The maintainer runs it and pushes release/<tag> before pushing the tag: a GitHub Actions token
# can never push a commit that changes .github/workflows/, and a stored token that could is a
# standing secret. The release workflow runs the same script and refuses unless the pushed
# release/<tag> has the source commit as its parent and the same tree (docs/guides/releasing.md).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
die() { echo "pin-release: $*" >&2; exit 1; }

[ "$#" -eq 2 ] || die "usage: $0 <owner/repo> <tag>"
repo="$1"
tag="$2"
[[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "invalid repository: $repo"
version="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$root/src/ayasee/__init__.py")"
[ "$tag" = "v$version" ] || die "tag $tag does not match ayasee.__version__ ($version)"
cd "$root"
git diff --quiet && git diff --cached --quiet || die "the working tree has changes; commit or stash them"
! git rev-parse --verify --quiet "refs/heads/release/$tag" >/dev/null ||
  die "a local branch release/$tag already exists"

src="$(git rev-parse HEAD)"
git checkout -q -b "release/$tag"
files="$(grep -lE '#[ ]AYASEE_(REF|REPO|SHA)' .github/workflows/*.yml)"
for f in $files; do
  scripts/set-ayasee-ref.sh "$repo" "$src" "$f"
done
# shellcheck disable=SC2086  # $files is a newline-free list of workflow paths
if grep -nE 'OWNER/ayasee|0{40} # AYASEE_(REF|SHA)' $files; then
  die "a AYASEE_REF placeholder is left behind"
fi
git commit -q -a -m "chore: pin ayasee actions to $src for $tag"
echo "source-sha=$src"
echo "release-sha=$(git rev-parse HEAD)"
