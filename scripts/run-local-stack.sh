#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r api/requirements.txt -r worker_py/requirements.txt
mkdir -p .runtime/archive .runtime/logs
set -a
source .env.localhost
set +a

API_LOG=.runtime/logs/api.log
WORKER_LOG=.runtime/logs/worker.log
FRONTEND_LOG=.runtime/logs/frontend.log

pkill -f "uvicorn api.app:app --host 127.0.0.1 --port 8000" 2>/dev/null || true
pkill -f "python -m worker_py.worker" 2>/dev/null || true
pkill -f ".runtime/frontend_go" 2>/dev/null || true

go build -o .runtime/frontend_go ./frontend_go
nohup env PUBLIC_DIR="$ROOT/frontend_go/public" HEDI_API_BASE="$HEDI_API_BASE" HEDI_SHARED_SECRET="$HEDI_SHARED_SECRET" HEDI_SESSION_SECRET="$HEDI_SESSION_SECRET" ./.runtime/frontend_go >"$FRONTEND_LOG" 2>&1 &
nohup uvicorn api.app:app --host 127.0.0.1 --port 8000 >"$API_LOG" 2>&1 &
nohup python -m worker_py.worker >"$WORKER_LOG" 2>&1 &

echo "frontend log: $FRONTEND_LOG"
echo "api log: $API_LOG"
echo "worker log: $WORKER_LOG"
echo "TurboHEDI local stack started: http://127.0.0.1:8080"
