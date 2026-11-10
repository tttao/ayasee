#!/usr/bin/env bash
# Build the content of a sandbox repository in <dir>:
#   seed.sh <dir> <canonical-owner/repo> <release-sha>
# The fixture (units, .ayasee.yaml, caller workflows), plus an in-repo copy of the ayasee
# actions, CLI source and the pr/staged reusable workflows, rewritten to ./actions (R33 option ii).
# nightly.yml and breakglass.yml cannot use ./actions, so they call <canonical-owner/repo> at
# <release-sha>, which must be a commit whose workflows carry release-rewritten references.
# Nothing is pushed: commit and push the result to the *-sandbox repository yourself.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
die() { echo "seed: $*" >&2; exit 1; }
[ "$#" -eq 3 ] || die "usage: $0 <dir> <owner/repo> <release-sha>"
dir="$1"; canonical="$2"; sha="$3"
[[ "$canonical" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "invalid repository: $canonical"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "release-sha must be a full 40-character commit SHA"
mkdir -p "$dir"
[ -z "$(ls -A "$dir")" ] || die "$dir is not empty"

cp -a "$root/e2e/fixture/." "$dir/"
cp -a "$root/actions" "$root/src" "$root/rules" "$root/policy" "$dir/"
cp "$root/pyproject.toml" "$root/uv.lock" "$root/README.md" "$root/LICENSE" "$root/.python-version" "$dir/"
for wf in iac-pr iac-staged; do
  cp "$root/.github/workflows/$wf.yml" "$dir/.github/workflows/$wf.yml"
  "$root/scripts/set-ayasee-ref.sh" --local "$dir/.github/workflows/$wf.yml"
done
for wf in nightly breakglass; do
  sed -i "s|OWNER/ayasee/\(.github/workflows/[a-z-]*.yml\)@[0-9a-f]\{40\} # AYASEE_WORKFLOW_REF|$canonical/\1@$sha # AYASEE_WORKFLOW_REF|" \
    "$dir/.github/workflows/$wf.yml"
done
echo "seeded $dir"
