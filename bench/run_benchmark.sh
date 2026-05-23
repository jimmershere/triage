#!/bin/bash
# TurboHEDI 837P Performance Benchmark
# Uploads progressively larger files and captures processing timing.

set -euo pipefail

ENDPOINT="http://localhost:8000/ingest"
TEST_DIR="/app/giles/turbohedi/bench/test_files"
RESULTS_FILE="/app/giles/turbohedi/bench/benchmark_results.txt"
CONTAINER="turboedi-starter_worker_py_1"

echo "=== TurboHEDI 837P Performance Benchmark ===" | tee "$RESULTS_FILE"
echo "Date: $(date -u '+%Y-%m-%d %H:%M:%S UTC')" | tee -a "$RESULTS_FILE"
echo "" | tee -a "$RESULTS_FILE"

FILES=("837p_500.x12" "837p_1000.x12" "837p_5000.x12" "837p_10000.x12" "837p_25000.x12")
CLAIMS=(500 1000 5000 10000 25000)

for i in "${!FILES[@]}"; do
    FILE="${TEST_DIR}/${FILES[$i]}"
    CLAIM_COUNT="${CLAIMS[$i]}"
    FILE_SIZE=$(stat -c%s "$FILE")
    FILE_SIZE_KB=$(echo "scale=1; $FILE_SIZE / 1024" | bc)
    SEG_COUNT=$(tr -cd '~' < "$FILE" | wc -c)

    echo "--- Test $((i+1)): ${CLAIM_COUNT} claims (${FILE_SIZE_KB} KB, ${SEG_COUNT} segments) ---" | tee -a "$RESULTS_FILE"

    # Get current log line count to know where to look after processing
    LOG_BEFORE=$(sudo docker logs "$CONTAINER" 2>&1 | wc -l)

    # Upload
    UPLOAD_START=$(date +%s%N)
    RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$ENDPOINT" \
        -F "file=@${FILE}" \
        -F "uploaded_by=benchmark" \
        -F "trading_partner_id=BENCHPAYER01")
    UPLOAD_END=$(date +%s%N)

    HTTP_CODE=$(echo "$RESPONSE" | tail -1)
    BODY=$(echo "$RESPONSE" | head -n -1)
    UPLOAD_MS=$(( (UPLOAD_END - UPLOAD_START) / 1000000 ))

    echo "  Upload: HTTP ${HTTP_CODE} in ${UPLOAD_MS}ms" | tee -a "$RESULTS_FILE"

    if [ "$HTTP_CODE" != "200" ] && [ "$HTTP_CODE" != "202" ] && [ "$HTTP_CODE" != "201" ]; then
        echo "  ERROR: Upload failed with HTTP $HTTP_CODE" | tee -a "$RESULTS_FILE"
        echo "  Response: $BODY" | tee -a "$RESULTS_FILE"
        continue
    fi

    # Extract job_id from response
    JOB_ID=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('job_id','unknown'))" 2>/dev/null || echo "unknown")
    echo "  Job ID: $JOB_ID" | tee -a "$RESULTS_FILE"

    # Wait for processing to complete — poll worker logs for "Finished processing"
    echo "  Waiting for processing..." | tee -a "$RESULTS_FILE"
    MAX_WAIT=600  # 10 minutes max
    ELAPSED=0
    FOUND=0
    while [ $ELAPSED -lt $MAX_WAIT ]; do
        sleep 2
        ELAPSED=$((ELAPSED + 2))

        # Check if processing finished for this job
        NEW_LOGS=$(sudo docker logs "$CONTAINER" 2>&1 | tail -n +$((LOG_BEFORE + 1)))
        if echo "$NEW_LOGS" | grep -q "Finished processing job $JOB_ID"; then
            FOUND=1
            break
        fi
    done

    if [ $FOUND -eq 0 ]; then
        echo "  TIMEOUT: Processing did not complete within ${MAX_WAIT}s" | tee -a "$RESULTS_FILE"
        # Dump recent logs anyway
        sudo docker logs "$CONTAINER" 2>&1 | tail -20 | tee -a "$RESULTS_FILE"
        continue
    fi

    # Extract timing from logs for this job
    echo "  Worker logs:" | tee -a "$RESULTS_FILE"
    LOGS=$(sudo docker logs "$CONTAINER" 2>&1 | tail -n +$((LOG_BEFORE + 1)))

    PROFILE_LINE=$(echo "$LOGS" | grep "Profiled file $JOB_ID" || true)
    ROUTE_LINE=$(echo "$LOGS" | grep "Routed file $JOB_ID" || true)
    ROUTING_LINE=$(echo "$LOGS" | grep "Routing decision for job $JOB_ID" || true)
    TIER_LINE=$(echo "$LOGS" | grep "Tier.*execution for $JOB_ID" || true)
    CLAIMS_LINE=$(echo "$LOGS" | grep "Inserted.*claims for" || true)
    FINISH_LINE=$(echo "$LOGS" | grep "Finished processing job $JOB_ID" || true)

    if [ -n "$PROFILE_LINE" ]; then
        echo "    $PROFILE_LINE" | tee -a "$RESULTS_FILE"
    fi
    if [ -n "$ROUTE_LINE" ]; then
        echo "    $ROUTE_LINE" | tee -a "$RESULTS_FILE"
    fi
    if [ -n "$ROUTING_LINE" ]; then
        echo "    $ROUTING_LINE" | tee -a "$RESULTS_FILE"
    fi
    if [ -n "$TIER_LINE" ]; then
        echo "    $TIER_LINE" | tee -a "$RESULTS_FILE"
    fi
    if [ -n "$CLAIMS_LINE" ]; then
        echo "    $CLAIMS_LINE" | tee -a "$RESULTS_FILE"
    fi
    if [ -n "$FINISH_LINE" ]; then
        echo "    $FINISH_LINE" | tee -a "$RESULTS_FILE"
    fi

    # Extract key timings
    PROFILE_TIME=$(echo "$PROFILE_LINE" | grep -oP '\([\d.]+s\)' | tr -d '()s' || echo "?")
    TOTAL_TIME=$(echo "$FINISH_LINE" | grep -oP 'in [\d.]+s' | grep -oP '[\d.]+' || echo "?")
    TIER_NUM=$(echo "$ROUTING_LINE" | grep -oP 'tier=\d+' | grep -oP '\d+' || echo "?")
    TIER_LABEL=$(echo "$ROUTING_LINE" | grep -oP '\(\w+\)' | tr -d '()' || echo "?")

    echo "  Summary: tier=$TIER_NUM, profile=${PROFILE_TIME}s, total=${TOTAL_TIME}s" | tee -a "$RESULTS_FILE"
    echo "" | tee -a "$RESULTS_FILE"
done

echo "=== Benchmark Complete ===" | tee -a "$RESULTS_FILE"
