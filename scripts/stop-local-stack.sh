#!/usr/bin/env bash
# Stop every process the host-native TurboHEDI stack starts.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

stop() {
  local label="$1" pattern="$2"
  if pgrep -f "$pattern" >/dev/null 2>&1; then
    echo "stopping $label..."
    pkill -f "$pattern" 2>/dev/null || true
  fi
}

stop "uvicorn (API)"    "uvicorn api.app:app --host 127.0.0.1 --port 8000"
stop "worker (script)"  "python.* worker_py/worker.py"
stop "worker (-m)"      "python -m worker_py.worker"
stop "frontend_go"      ".runtime/frontend_go"
stop "rbac_proxy"       ".runtime/rbac_proxy"
sleep 1

if [[ -f .runtime/stack.pids ]]; then
  rm -f .runtime/stack.pids
fi

echo "stack stopped."
