#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x "$REPO_ROOT/.venv/bin/python3" ]; then
    PYTHON="$REPO_ROOT/.venv/bin/python3"
  else
    PYTHON="python3"
  fi
fi

cd "$REPO_ROOT"

if command -v psql >/dev/null 2>&1 && [ "${CLAIMTRACE_APPLY_SQL:-0}" = "1" ]; then
  psql "${CLAIMTRACE_DATABASE_URL:-postgresql://edi:edi@localhost:15432/edi?sslmode=disable}" -f migrations/001_journal.sql
fi

if command -v cypher-shell >/dev/null 2>&1 && [ "${CLAIMTRACE_APPLY_CYPHER:-0}" = "1" ]; then
  cypher-shell -a "${CLAIMTRACE_NEO4J_URI:-bolt://localhost:7687}" -u "${CLAIMTRACE_NEO4J_USER:-neo4j}" -p "${CLAIMTRACE_NEO4J_PASSWORD:-claimtrace123}" -f claimtrace/lineage/schema.cypher
fi

exec "$PYTHON" -m unittest discover -s tests -t . -p 'test_claimtrace.py' -v
