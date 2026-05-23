#!/usr/bin/env bash
# TurboHEDI host-native installer.
#
# Installs and configures every host-side dependency the stack expects when
# running outside Docker: PostgreSQL, RabbitMQ, the Go toolchain, the Python
# venv, the database schema and the RabbitMQ users / permissions.
#
# Idempotent — re-running the script after a partial setup is safe; it only
# creates objects that are missing.
#
# Pre-reqs: an Ubuntu/Debian-family host with apt-get, a non-root user who
# can ``sudo`` (the script does prompt for the password unless you have
# passwordless sudo configured for apt-get / systemctl / su).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() { printf "\n[install-native] %s\n" "$*"; }

# ---------------------------------------------------------------------------
# 0. sanity
# ---------------------------------------------------------------------------
if ! command -v apt-get >/dev/null 2>&1; then
  echo "[install-native] apt-get not found. Only Debian/Ubuntu hosts are supported by this script." >&2
  exit 1
fi
if ! sudo -v >/dev/null 2>&1; then
  echo "[install-native] sudo is required (passwordless sudo is recommended for apt-get / systemctl / su)." >&2
  exit 1
fi

ENV_FILE="${TURBOHEDI_ENV_FILE:-.env.localhost}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "[install-native] $ENV_FILE not found. Copy .env.example to $ENV_FILE first." >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

PG_USER="edi"
PG_DB="edi"
PG_PASS="edi"
RMQ_USER="${RMQ_USER:-ediapp}"
RMQ_PASS="${RMQ_PASS:-3wm078uu}"
RMQ_VHOST="${RMQ_VHOST:-/}"

# ---------------------------------------------------------------------------
# 1. apt packages
# ---------------------------------------------------------------------------
log "installing postgres, rabbitmq-server, golang-go via apt"
sudo apt-get update -q
sudo apt-get install -y -q postgresql rabbitmq-server golang-go

log "ensuring services are enabled and running"
sudo systemctl enable --now postgresql
sudo systemctl enable --now rabbitmq-server

# ---------------------------------------------------------------------------
# 2. PostgreSQL — edi/edi user + edi database + schema
# ---------------------------------------------------------------------------
psql_postgres() {
  sudo su - postgres -c "psql -tAc \"$1\""
}
psql_postgres_run() {
  sudo su - postgres -c "psql -c \"$1\""
}

log "configuring postgres role $PG_USER and database $PG_DB"
if [[ "$(psql_postgres "SELECT 1 FROM pg_roles WHERE rolname='$PG_USER'")" != "1" ]]; then
  psql_postgres_run "CREATE USER $PG_USER WITH PASSWORD '$PG_PASS' CREATEDB;" >/dev/null
fi
if [[ "$(psql_postgres "SELECT 1 FROM pg_database WHERE datname='$PG_DB'")" != "1" ]]; then
  psql_postgres_run "CREATE DATABASE $PG_DB OWNER $PG_USER;" >/dev/null
fi
psql_postgres_run "GRANT ALL PRIVILEGES ON DATABASE $PG_DB TO $PG_USER;" >/dev/null

log "loading db/init.sql and db/schema.sql into $PG_DB"
PGPASSWORD="$PG_PASS" psql -h 127.0.0.1 -U "$PG_USER" -d "$PG_DB" -q -f db/init.sql
PGPASSWORD="$PG_PASS" psql -h 127.0.0.1 -U "$PG_USER" -d "$PG_DB" -q -f db/schema.sql

# ---------------------------------------------------------------------------
# 3. RabbitMQ — ediapp user + permissions + management plugin
# ---------------------------------------------------------------------------
log "configuring rabbitmq user $RMQ_USER"
sudo su -c "rabbitmqctl list_users 2>/dev/null | awk '{print \$1}' | grep -qx '$RMQ_USER' || rabbitmqctl add_user '$RMQ_USER' '$RMQ_PASS'"
sudo su -c "rabbitmqctl set_user_tags '$RMQ_USER' administrator"
sudo su -c "rabbitmqctl set_permissions -p '$RMQ_VHOST' '$RMQ_USER' '.*' '.*' '.*'"
sudo su -c "rabbitmq-plugins enable rabbitmq_management" >/dev/null

# ---------------------------------------------------------------------------
# 4. Python venv + dependencies
# ---------------------------------------------------------------------------
log "preparing python virtualenv and installing dependencies"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
VENV_PY="$ROOT/.venv/bin/python3"
"$VENV_PY" -m pip install -q --upgrade pip 2>/dev/null || true
"$VENV_PY" -m pip install -q -r api/requirements.txt -r worker_py/requirements.txt

# ---------------------------------------------------------------------------
# 5. Build the Go binaries (frontend + rbac_proxy)
# ---------------------------------------------------------------------------
if command -v go >/dev/null 2>&1; then
  log "building the frontend_go and rbac_proxy binaries"
  mkdir -p .runtime
  (cd frontend_go && go build -buildvcs=false -o "$ROOT/.runtime/frontend_go" .)
  (cd rbac_proxy  && go build -buildvcs=false -o "$ROOT/.runtime/rbac_proxy"  .)
fi

# ---------------------------------------------------------------------------
# 6. Done
# ---------------------------------------------------------------------------
cat <<'EOF'

[install-native] All host dependencies are installed and configured.
                 Run the stack with:

  bash scripts/run-local-stack.sh

                 Then open http://127.0.0.1:8080 for the canvas UI.
EOF
