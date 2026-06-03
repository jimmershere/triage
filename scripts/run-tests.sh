#!/usr/bin/env bash
# Run the TurboHEDI Python test suite.
#
# Discovers every test_*.py under worker_py/ (including sub-packages such as
# validation/, scrubbing/, fhir/) and runs them with the stdlib unittest runner.
#
# Usage:
#   bash scripts/run-tests.sh                 # full suite
#   bash scripts/run-tests.sh validation      # only the validation/ sub-package
#   PYTHON=/usr/bin/python3 bash scripts/run-tests.sh
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

cd "$REPO_ROOT/worker_py"

START_DIR="."
if [ "${1:-}" != "" ]; then
  START_DIR="$1"
fi

echo "[run-tests] python:    $PYTHON"
echo "[run-tests] discover:  worker_py/$START_DIR"
# Run each discovery stage tolerantly so a failure in one stage doesn't
# short-circuit the other under `set -e`. Each unittest exit code is
# captured and merged at the end.
set +e
"$PYTHON" -m unittest discover -s "$START_DIR" -t . -p 'test_*.py' -v
WORKER_EXIT=$?
set -e

# Also run the test file generators and loadtest tests from the repo root.
# When START_DIR is set we assume the caller is targeting a worker_py
# sub-package and skip the repo-root tests for speed.
TESTS_EXIT=0
if [ "${1:-}" = "" ]; then
  echo ""
  echo "[run-tests] discover:  tests/"
  cd "$REPO_ROOT"
  set +e
  "$PYTHON" -m unittest discover -s tests -t . -p 'test_*.py' -v
  TESTS_EXIT=$?
  set -e
fi

if [ $WORKER_EXIT -ne 0 ] || [ $TESTS_EXIT -ne 0 ]; then
  exit 1
fi
