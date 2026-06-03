#!/usr/bin/env bash
# Reject any tracked file that still contains a Git merge conflict marker.
# This is a high-signal hallucination/integration check: an AI assistant that
# fabricates "resolution" without actually editing both sides of a conflict
# usually leaves these markers behind.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Pattern matches the standard 7-character conflict prefix at start of line,
# excluding this script itself so it never self-triggers.
PATTERN='^(<{7}|={7}|>{7})( |$)'

# Use git grep so we only inspect tracked files (ignoring vendored deps and
# generated artifacts in .gitignore).
if matches=$(git grep -n -E "$PATTERN" -- ':(exclude)scripts/ci/check-no-conflict-markers.sh' 2>/dev/null); then
  echo "ERROR: unresolved merge conflict markers found:" >&2
  echo "$matches" >&2
  exit 1
fi

echo "OK: no merge conflict markers found in tracked files."
