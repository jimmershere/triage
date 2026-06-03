#!/usr/bin/env bash
# Push representative X12 / EDI samples through the running Triage ingest
# pipeline. Use after `scripts/run-local-stack.sh` and a quick sanity check
# that the API is reachable.
#
# Usage:
#   bash scripts/ingest-samples.sh                  # standard fixture set
#   bash scripts/ingest-samples.sh path/to/x12.txt  # one specific file
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

API="${TRIAGE_API:-${TURBOHEDI_API:-http://127.0.0.1:8000}}"
PARTNER="${TRIAGE_PARTNER:-${TURBOHEDI_PARTNER:-DEMO_PARTNER}}"
SECRET="${TRIAGE_SHARED_SECRET:-}"
HEADER_ARGS=()
if [[ -n "$SECRET" ]]; then
  HEADER_ARGS=(-H "X-TRIAGE-SECRET: $SECRET")
fi

if ! curl -s -o /dev/null -w "%{http_code}" "$API/openapi.json" | grep -q 200; then
  echo "[ingest-samples] API not reachable at $API — start the stack first." >&2
  exit 1
fi

if [[ $# -gt 0 ]]; then
  files=("$@")
else
  files=(
    samples/x12_837_small.txt
    samples/x12_837_large_valid.x12
    samples/x12_835_small.txt
    samples/x12_270_small.txt
    samples/x12_271_small.txt
    samples/x12_276_small.txt
    samples/x12_837d_small.txt
    demo-data/demo-837p-clean.edi
    demo-data/demo-837p-exceptions.edi
    demo-data/demo-837i-institutional.edi
    demo-data/demo-837d-dental.edi
    demo-data/demo-835-remittance.edi
  )
fi

ingested=0
for path in "${files[@]}"; do
  if [[ ! -f "$path" ]]; then
    printf "  %-50s SKIPPED (missing)\n" "$path"
    continue
  fi
  size=$(stat -c%s "$path")
  resp=$(curl -s -X POST "$API/ingest" \
    "${HEADER_ARGS[@]}" \
    -F "file=@$path" \
    -F "trading_partner_id=$PARTNER")
  job=$(printf "%s" "$resp" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('job_id') or d.get('detail') or '-')" 2>/dev/null || echo "?")
  printf "  %-50s -> %s (%s bytes)\n" "$path" "$job" "$size"
  ingested=$((ingested + 1))
done

echo
echo "[ingest-samples] queued $ingested file(s). Watch the worker process them with:"
echo "  tail -f .runtime/logs/worker.log"
echo "or browse jobs in the UI:"
echo "  http://127.0.0.1:8080/processed.html"
