#!/usr/bin/env bash
# Reject obvious placeholder strings that should never land in source-controlled
# code or config. AI-generated edits frequently leave these behind when they
# stub out values they did not actually compute.
#
# Allow-list: .env.example and docs explicitly call out CHANGE_ME_BEFORE_PRODUCTION
# as the expected default placeholder; those files are excluded.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Patterns we treat as forbidden when they appear in code, scripts, workflows,
# or compose files (not docs). Each pattern is a fixed string for clarity.
PATTERNS=(
  'CHANGE_ME_BEFORE_PRODUCTION'
  'REPLACE_ME'
  'TODO: secret'
  'FIXME: secret'
  '<your-secret-here>'
)

# Source/config locations the rule applies to.
SEARCH_PATHS=(
  'api'
  'claimtrace'
  'worker_py'
  'frontend_go'
  'rbac_proxy'
  'scripts'
  '.github'
  'docker-compose.yml'
)

# Files we deliberately allow to mention placeholders (templates / docs / installer).
# Placeholders are expected in *.sample / *.example template files because they
# are operator instructions, not committed runtime configuration.
EXCLUDE_PATHSPECS=(
  ':(exclude).env.example'
  ':(exclude)**/.env.example'
  ':(exclude)README.md'
  ':(exclude)**/*.md'
  ':(exclude)**/*.sample'
  ':(exclude)**/*.example'
  ':(exclude)scripts/ci/check-placeholders.sh'
  ':(exclude)scripts/triage-installer.py'
)

fail=0
for pat in "${PATTERNS[@]}"; do
  if matches=$(git grep -n -F -- "$pat" "${SEARCH_PATHS[@]}" "${EXCLUDE_PATHSPECS[@]}" 2>/dev/null); then
    echo "ERROR: forbidden placeholder \"$pat\" found:" >&2
    echo "$matches" >&2
    fail=1
  fi
done

if [ "$fail" -ne 0 ]; then
  exit 1
fi

echo "OK: no forbidden placeholder strings found in code or config."
