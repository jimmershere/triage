#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
source .env.localhost
FILE="${1:-samples/x12_837_large_valid.x12}"
UPLOADED_BY="${2:-demo.operator}"
PARTNER="${3:-TPDEMO001}"

curl -sS -X POST "$HEDI_API_BASE/ingest" \
  -F "file=@${FILE}" \
  -F "uploaded_by=${UPLOADED_BY}" \
  -F "trading_partner_id=${PARTNER}"
