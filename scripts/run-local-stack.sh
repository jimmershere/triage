#!/usr/bin/env bash
# Bring up the host-native Triage stack (frontend_go + FastAPI + worker)
# against PostgreSQL + RabbitMQ already running on the host.
#
# Pre-reqs:
#   - postgres + rabbitmq installed and configured (see scripts/install-native.sh)
#   - go toolchain available
#   - .venv/ created with api + worker dependencies installed
#
# This script is idempotent: previously running processes are killed before
# the new ones start. Logs land under .runtime/logs/.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# --- venv -----------------------------------------------------------------
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# Resolve the venv interpreter; we use ``python3 -m pip`` because some
# installations (notably ``python3 -m venv --without-pip``) do not ship a
# pip executable on the PATH after activation.
VENV_PY="$ROOT/.venv/bin/python3"
if [[ ! -x "$VENV_PY" ]]; then
  echo "[run-local-stack] missing $VENV_PY — recreate the venv with python3 -m venv .venv" >&2
  exit 1
fi
"$VENV_PY" -m pip install -q --upgrade pip 2>/dev/null || true
"$VENV_PY" -m pip install -q -r api/requirements.txt -r worker_py/requirements.txt

# --- runtime dirs ---------------------------------------------------------
mkdir -p .runtime/archive .runtime/logs

# --- env ------------------------------------------------------------------
ENV_FILE="${TRIAGE_ENV_FILE:-${TURBOHEDI_ENV_FILE:-.env.localhost}}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "[run-local-stack] env file $ENV_FILE not found" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

API_LOG=.runtime/logs/api.log
WORKER_LOG=.runtime/logs/worker.log
FRONTEND_LOG=.runtime/logs/frontend.log
RBAC_LOG=.runtime/logs/rbac.log

# --- stop anything that's already running --------------------------------
pkill -f "uvicorn api.app:app --host 127.0.0.1 --port 8000" 2>/dev/null || true
pkill -f "python.* worker_py/worker.py" 2>/dev/null || true
pkill -f "python -m worker_py.worker" 2>/dev/null || true
pkill -f ".runtime/frontend_go" 2>/dev/null || true
pkill -f ".runtime/rbac_proxy" 2>/dev/null || true
sleep 1

# --- build the Go binaries if missing or stale ---------------------------
if command -v go >/dev/null 2>&1; then
  (cd frontend_go && go build -buildvcs=false -o "$ROOT/.runtime/frontend_go" .)
  (cd rbac_proxy  && go build -buildvcs=false -o "$ROOT/.runtime/rbac_proxy"  .)
elif [[ ! -x .runtime/frontend_go || ! -x .runtime/rbac_proxy ]]; then
  echo "[run-local-stack] go toolchain not found and the binaries have not been built — run scripts/install-native.sh first" >&2
  exit 1
fi

# --- start the four services --------------------------------------------
nohup env \
  PUBLIC_DIR="$ROOT/frontend_go/public" \
  TRIAGE_API_BASE="$TRIAGE_API_BASE" \
  TRIAGE_OAUTH2_PROXY_URL="${TRIAGE_OAUTH2_PROXY_URL:-http://127.0.0.1:4180}" \
  TRIAGE_OAUTH2_PROXY_INTERNAL_URL="${TRIAGE_OAUTH2_PROXY_INTERNAL_URL:-http://127.0.0.1:4180}" \
  TRIAGE_SHARED_SECRET="$TRIAGE_SHARED_SECRET" \
  TRIAGE_SESSION_SECRET="$TRIAGE_SESSION_SECRET" \
  ./.runtime/frontend_go >"$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!

nohup env \
  RBAC_LISTEN_ADDR=":4180" \
  RBAC_API_BASE="$TRIAGE_API_BASE" \
  RBAC_SHARED_SECRET="$TRIAGE_SHARED_SECRET" \
  TRIAGE_SESSION_SECRET="$TRIAGE_SESSION_SECRET" \
  ./.runtime/rbac_proxy >"$RBAC_LOG" 2>&1 &
RBAC_PID=$!

nohup "$VENV_PY" -m uvicorn api.app:app --host 127.0.0.1 --port 8000 >"$API_LOG" 2>&1 &
API_PID=$!

# Run the worker as a script (not -m worker_py.worker); worker.py's import
# fallback expects its own directory on sys.path, which `python file.py`
# provides automatically.
nohup "$VENV_PY" worker_py/worker.py >"$WORKER_LOG" 2>&1 &
WORKER_PID=$!

echo "$FRONTEND_PID $RBAC_PID $API_PID $WORKER_PID" > .runtime/stack.pids

echo "frontend log: $FRONTEND_LOG  (pid $FRONTEND_PID)"
echo "rbac log:     $RBAC_LOG  (pid $RBAC_PID)"
echo "api log:      $API_LOG  (pid $API_PID)"
echo "worker log:   $WORKER_LOG  (pid $WORKER_PID)"
echo
echo "Triage local stack started:"
echo "  Frontend UI:        http://127.0.0.1:8080         (sign in: admin / 3wm078uu)"
echo "  Mapping canvas:     http://127.0.0.1:8080/mapping.html"
echo "  API (Swagger):      http://127.0.0.1:8000/docs"
echo "  Turbo capability:   http://127.0.0.1:8000/turbo/capability"
echo "  Command center:     http://127.0.0.1:8080/"
echo "  RabbitMQ mgmt UI:   http://127.0.0.1:15672         (ediapp / 3wm078uu)"
echo
echo "Stop the stack with: bash scripts/stop-local-stack.sh"
echo "Seed data with:      bash scripts/ingest-samples.sh"
