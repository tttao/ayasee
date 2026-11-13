#!/usr/bin/env bash
# Scan for leaked secrets with gitleaks and the repository's .gitleaks.toml. Findings are redacted:
# the log shows the rule, file, line and commit, never the value.
# Usage: check-secrets.sh --staged        the staged changes (the pre-commit hook)
#        check-secrets.sh BASE..HEAD      the commits of a pull request (two full commit SHAs)
#        check-secrets.sh                 the whole history of every fetched ref
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# --verbose prints each finding (rule, file, line, commit); --redact replaces the value in it.
# Exceptions live only in .gitleaks.toml and .gitleaksignore (fingerprints of findings already
# rotated), both at the root and reviewed as files: an inline `gitleaks:allow` comment is ignored.
args=(git --config "$root/.gitleaks.toml" --gitleaks-ignore-path "$root" --ignore-gitleaks-allow
  --redact --verbose --no-banner --exit-code 1)

case "${1:-}" in
  "") ;;
  --staged) args+=(--staged) ;;
  *)
    # Only a SHA range reaches git log: no option or revision syntax from outside.
    [[ "$1" =~ ^[0-9a-f]{40}\.\.[0-9a-f]{40}$ ]] \
      || { echo "check-secrets: expected BASE..HEAD as two full commit SHAs, got: $1" >&2; exit 2; }
    args+=(--log-opts="$1")
    ;;
esac
[ "$#" -le 1 ] || { echo "check-secrets: at most one argument" >&2; exit 2; }

exec "$root/scripts/dev-tool.sh" gitleaks "${args[@]}" "$root"
