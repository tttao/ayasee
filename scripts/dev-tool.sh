#!/usr/bin/env bash
# Run a pinned dev tool from .tools/bin (scripts/install-dev-tools.sh), falling back to PATH, and
# fail with the install command when it is missing. Used by the local hooks of
# .pre-commit-config.yaml so a missing tool is a clear failure, never a silent skip.
# Usage: dev-tool.sh TOOL [ARG ...]
set -euo pipefail

[ "$#" -ge 1 ] || { echo "usage: dev-tool.sh TOOL [ARG ...]" >&2; exit 2; }
tool="$1"
shift
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -x "$root/.tools/bin/$tool" ]; then
  exec "$root/.tools/bin/$tool" "$@"
fi
if command -v "$tool" >/dev/null 2>&1; then
  exec "$tool" "$@"
fi
case "$tool" in
  tofu) hint="install OpenTofu (https://opentofu.org/docs/intro/install/) and put tofu on PATH" ;;
  *) hint="run: scripts/install-dev-tools.sh $tool" ;;
esac
echo "dev-tool: $tool not found in .tools/bin or on PATH; $hint" >&2
exit 1
