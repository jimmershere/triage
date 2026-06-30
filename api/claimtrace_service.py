"""Database-backed Claimtrace persistence and dashboard helpers."""

from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from typing import Any, Optional

from psycopg2.extras import RealDictCursor

from claimtrace.common.canonical import canonical_json
from claimtrace.common.hashing import hash_payload, sha256_hex

CLAIMTRACE_ENABLED = os.getenv("TRIAGE_CLAIMTRACE_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}


def claimtrace_enabled() -> bool:
    return CLAIMTRACE_ENABLED


def ensure_claimtrace_tables(conn) -> None:
    """Create persistent Claimtrace tables used by ingest and the dashboard."""

    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS claimtrace_claim (
                tracking_id UUID PRIMARY KEY,
                claim_id TEXT NOT NULL,
                claim_hash_id TEXT NOT NULL,
                import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL,
                job_id UUID UNIQUE,
                filename TEXT,
                uploaded_by TEXT,
                trading_partner_id TEXT,
                submitter_id TEXT,
                trace_id TEXT,
                state_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'tracked',
                action_state TEXT NOT NULL DEFAULT 'none',
                recommended_next_steps JSONB NOT NULL DEFAULT '[]',
                raw_excerpt TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS claimtrace_claim_claim_id_idx ON claimtrace_claim (claim_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS claimtrace_claim_hash_idx ON claimtrace_claim (claim_hash_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS claimtrace_claim_partner_idx ON claimtrace_claim (trading_partner_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS claimtrace_claim_submitter_idx ON claimtrace_claim (submitter_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS claimtrace_claim_action_idx ON claimtrace_claim (action_state)")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS claimtrace_event (
                event_id UUID PRIMARY KEY,
                tracking_id UUID REFERENCES claimtrace_claim(tracking_id) ON DELETE SET NULL,
                claim_id TEXT NOT NULL,
                bundle_id TEXT,
                operation_type TEXT NOT NULL,
                state_hash TEXT NOT NULL,
                payload_location TEXT NOT NULL,
                service_name TEXT NOT NULL,
                ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                correlation_ids JSONB NOT NULL DEFAULT '{}'
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS claimtrace_event_claim_ts_idx ON claimtrace_event (claim_id, ts)")
        cur.execute("CREATE INDEX IF NOT EXISTS claimtrace_event_tracking_ts_idx ON claimtrace_event (tracking_id, ts)")
        cur.execute("CREATE INDEX IF NOT EXISTS claimtrace_event_corr_gin_idx ON claimtrace_event USING GIN (correlation_ids)")
        cur.execute(
            """
            CREATE OR REPLACE FUNCTION reject_claimtrace_event_mutation()
            RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'claimtrace_event is append-only; UPDATE and DELETE are forbidden';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        cur.execute("DROP TRIGGER IF EXISTS claimtrace_event_append_only_update ON claimtrace_event")
        cur.execute(
            """
            CREATE TRIGGER claimtrace_event_append_only_update
            BEFORE UPDATE ON claimtrace_event
            FOR EACH ROW EXECUTE FUNCTION reject_claimtrace_event_mutation()
            """
        )
        cur.execute("DROP TRIGGER IF EXISTS claimtrace_event_append_only_delete ON claimtrace_event")
        cur.execute(
            """
            CREATE TRIGGER claimtrace_event_append_only_delete
            BEFORE DELETE ON claimtrace_event
            FOR EACH ROW EXECUTE FUNCTION reject_claimtrace_event_mutation()
            """
        )
    conn.commit()


def _safe_text(value: Optional[str]) -> str:
    return (value or "").strip()


def extract_submitter_id(content: bytes, uploaded_by: Optional[str], trading_partner_id: Optional[str]) -> str:
    text = content[:4096].decode("utf-8", errors="ignore").replace("\n", "")
    for segment in text.split("~"):
        parts = segment.split("*")
        if parts and parts[0] == "ISA" and len(parts) > 6 and parts[6].strip():
            return parts[6].strip()
        if parts and parts[0] == "GS" and len(parts) > 2 and parts[2].strip():
            return parts[2].strip()
        if parts and parts[0] == "NM1" and len(parts) > 9 and parts[1] in {"41", "85"} and parts[9].strip():
            return parts[9].strip()
    return _safe_text(uploaded_by) or _safe_text(trading_partner_id) or "UNKNOWN"


def derive_ingest_identity(
    *,
    content: bytes,
    filename: str,
    uploaded_by: Optional[str],
    trading_partner_id: Optional[str],
) -> dict[str, str]:
    claim_hash_id = hash_payload(content)
    submitter_id = extract_submitter_id(content, uploaded_by, trading_partner_id)
    claim_id = sha256_hex(
        canonical_json(
            {
                "claim_hash_id": claim_hash_id,
                "submitter_id": submitter_id,
                "trading_partner_id": trading_partner_id or "",
                "filename": filename,
            }
        )
    )
    return {
        "claim_id": claim_id,
        "claim_hash_id": claim_hash_id,
        "submitter_id": submitter_id,
        "state_hash": claim_hash_id,
    }


def _recommendations_for(row: dict[str, Any], events: list[dict[str, Any]]) -> list[str]:
    recommendations: list[str] = []
    ops = {event.get("operation_type") for event in events}
    if row.get("action_state") == "repair":
        recommendations.append("Repair requested: route this claim to an analyst workqueue and preserve the original payload.")
    if row.get("action_state") == "delete":
        recommendations.append("Deletion requested: require supervisor approval before removing downstream artifacts.")
    if "INGEST" in ops and "VALIDATE" not in ops:
        recommendations.append("Validation has not been recorded yet; run SNIP validation and attach the report.")
    if "MERKLE_ROOT" not in ops:
        recommendations.append("No Merkle root has been recorded; add batch integrity proof before downstream dispute handling.")
    if not recommendations:
        recommendations.append("Trace is complete enough for review; monitor acknowledgements and payment lineage.")
    return recommendations


def _event_dict(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in ("event_id", "tracking_id"):
        if result.get(key) is not None:
            result[key] = str(result[key])
    if result.get("ts") is not None:
        result["ts"] = result["ts"].isoformat()
    return result


def _claim_dict(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in ("tracking_id", "job_id"):
        if result.get(key) is not None:
            result[key] = str(result[key])
    for key in ("created_at", "updated_at"):
        if result.get(key) is not None:
            result[key] = result[key].isoformat()
    return result


def append_claimtrace_event(
    conn,
    *,
    tracking_id: uuid.UUID | str | None,
    claim_id: str,
    operation_type: str,
    state_hash: str,
    payload_location: str,
    service_name: str,
    bundle_id: Optional[str] = None,
    correlation_ids: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    event_id = uuid.uuid4()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO claimtrace_event (
                event_id, tracking_id, claim_id, bundle_id, operation_type, state_hash,
                payload_location, service_name, correlation_ids
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            RETURNING event_id, tracking_id, claim_id, bundle_id, operation_type, state_hash,
                      payload_location, service_name, ts, correlation_ids
            """,
            (
                str(event_id),
                str(tracking_id) if tracking_id else None,
                claim_id,
                bundle_id,
                operation_type,
                state_hash,
                payload_location,
                service_name,
                json.dumps(correlation_ids or {}, sort_keys=True),
            ),
        )
        row = cur.fetchone()
    return _event_dict(row)


def record_ingested_file(
    conn,
    *,
    import_id: int,
    job_id: str,
    filename: str,
    content: bytes,
    uploaded_by: Optional[str],
    trading_partner_id: Optional[str],
) -> dict[str, Any]:
    if not claimtrace_enabled():
        return {"enabled": False}
    identity = derive_ingest_identity(
        content=content,
        filename=filename,
        uploaded_by=uploaded_by,
        trading_partner_id=trading_partner_id,
    )
    tracking_id = uuid.uuid4()
    raw_excerpt = content[:2000].decode("utf-8", errors="replace")
    recommendations = ["Run validation and review acknowledgements once worker processing completes."]
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO claimtrace_claim (
                tracking_id, claim_id, claim_hash_id, import_id, job_id, filename,
                uploaded_by, trading_partner_id, submitter_id, trace_id, state_hash,
                status, action_state, recommended_next_steps, raw_excerpt
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'tracked','none',%s::jsonb,%s)
            ON CONFLICT (job_id) DO UPDATE SET
                filename = EXCLUDED.filename,
                uploaded_by = EXCLUDED.uploaded_by,
                trading_partner_id = EXCLUDED.trading_partner_id,
                submitter_id = EXCLUDED.submitter_id,
                state_hash = EXCLUDED.state_hash,
                updated_at = NOW()
            RETURNING tracking_id, claim_id, claim_hash_id, import_id, job_id, filename,
                      uploaded_by, trading_partner_id, submitter_id, trace_id, state_hash,
                      status, action_state, recommended_next_steps, raw_excerpt, created_at, updated_at
            """,
            (
                str(tracking_id),
                identity["claim_id"],
                identity["claim_hash_id"],
                import_id,
                job_id,
                filename,
                uploaded_by,
                trading_partner_id,
                identity["submitter_id"],
                job_id,
                identity["state_hash"],
                json.dumps(recommendations),
                raw_excerpt,
            ),
        )
        claim = _claim_dict(cur.fetchone())
    event = append_claimtrace_event(
        conn,
        tracking_id=claim["tracking_id"],
        claim_id=claim["claim_id"],
        operation_type="INGEST",
        state_hash=claim["state_hash"],
        payload_location=f"imports:{import_id}",
        service_name="triage-ingest",
        correlation_ids={
            "job_id": job_id,
            "import_id": import_id,
            "filename": filename,
            "uploaded_by": uploaded_by,
            "trading_partner_id": trading_partner_id,
            "submitter_id": identity["submitter_id"],
            "claim_hash_id": identity["claim_hash_id"],
        },
    )
    return {"enabled": True, "claim": claim, "event": event}


def summary(conn) -> dict[str, Any]:
    if not claimtrace_enabled():
        return {"enabled": False, "events": 0, "claims": 0, "bundles": 0, "append_only": True}
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT claim_id), COUNT(*) FILTER (WHERE action_state = 'repair'), COUNT(*) FILTER (WHERE action_state = 'delete') FROM claimtrace_claim")
        total, distinct_claims, repair_count, delete_count = cur.fetchone()
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT bundle_id) FILTER (WHERE bundle_id IS NOT NULL) FROM claimtrace_event")
        events, bundles = cur.fetchone()
    return {
        "enabled": True,
        "components": ["identity", "correlation", "journal", "merkle", "lineage", "dashboard"],
        "events": int(events or 0),
        "claims": int(total or 0),
        "distinct_claims": int(distinct_claims or 0),
        "bundles": int(bundles or 0),
        "repair_count": int(repair_count or 0),
        "delete_count": int(delete_count or 0),
        "append_only": True,
    }


def search_claims(
    conn,
    *,
    query: Optional[str] = None,
    trading_partner_id: Optional[str] = None,
    submitter_id: Optional[str] = None,
    claim_id: Optional[str] = None,
    claim_hash_id: Optional[str] = None,
    action_state: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if query:
        like = f"%{query.strip()}%"
        filters.append(
            "(claim_id ILIKE %s OR claim_hash_id ILIKE %s OR trading_partner_id ILIKE %s OR submitter_id ILIKE %s OR filename ILIKE %s OR uploaded_by ILIKE %s)"
        )
        params.extend([like, like, like, like, like, like])
    for column, value in [
        ("trading_partner_id", trading_partner_id),
        ("submitter_id", submitter_id),
        ("claim_id", claim_id),
        ("claim_hash_id", claim_hash_id),
        ("action_state", action_state),
    ]:
        if value:
            filters.append(f"{column} ILIKE %s" if column != "action_state" else f"{column} = %s")
            params.append(f"%{value.strip()}%" if column != "action_state" else value.strip())
    where = "WHERE " + " AND ".join(filters) if filters else ""
    params.append(max(1, min(limit, 200)))
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT tracking_id, claim_id, claim_hash_id, import_id, job_id, filename,
                   uploaded_by, trading_partner_id, submitter_id, trace_id, state_hash,
                   status, action_state, recommended_next_steps, raw_excerpt, created_at, updated_at
              FROM claimtrace_claim
              {where}
             ORDER BY created_at DESC
             LIMIT %s
            """,
            params,
        )
        return [_claim_dict(row) for row in cur.fetchall()]


def claim_detail(conn, claim_id: str) -> Optional[dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT tracking_id, claim_id, claim_hash_id, import_id, job_id, filename,
                   uploaded_by, trading_partner_id, submitter_id, trace_id, state_hash,
                   status, action_state, recommended_next_steps, raw_excerpt, created_at, updated_at
              FROM claimtrace_claim
             WHERE claim_id = %s OR claim_hash_id = %s OR tracking_id::text = %s
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (claim_id, claim_id, claim_id),
        )
        claim_row = cur.fetchone()
        if not claim_row:
            return None
        claim = _claim_dict(claim_row)
        cur.execute(
            """
            SELECT event_id, tracking_id, claim_id, bundle_id, operation_type, state_hash,
                   payload_location, service_name, ts, correlation_ids
              FROM claimtrace_event
             WHERE tracking_id = %s OR claim_id = %s
             ORDER BY ts ASC, event_id ASC
            """,
            (claim["tracking_id"], claim["claim_id"]),
        )
        events = [_event_dict(row) for row in cur.fetchall()]
    recommendations = _recommendations_for(claim, events)
    return {
        "claim": claim,
        "events": events,
        "analysis": {
            "event_count": len(events),
            "operations": [event["operation_type"] for event in events],
            "recommended_next_steps": recommendations,
            "risk_level": "high" if claim.get("action_state") in {"repair", "delete"} else "normal",
        },
    }


def mark_claim(conn, *, claim_id: str, action: str, note: Optional[str] = None) -> dict[str, Any]:
    action = action.strip().lower()
    state_map = {
        "repair": ("repair", "repair_requested", "MARK_REPAIR"),
        "delete": ("delete", "deletion_requested", "MARK_DELETE"),
        "reviewed": ("reviewed", "reviewed", "MARK_REVIEWED"),
        "clear": ("none", "tracked", "CLEAR_MARK"),
    }
    if action not in state_map:
        raise ValueError("action must be one of: repair, delete, reviewed, clear")
    action_state, status, operation = state_map[action]
    detail = claim_detail(conn, claim_id)
    if not detail:
        return {"updated": False}
    claim = detail["claim"]
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Lock the target row and re-read recommended_next_steps under the lock so
        # concurrent mark_claim calls serialize instead of clobbering each other's
        # appended notes (read-modify-write lost update).
        cur.execute(
            "SELECT recommended_next_steps FROM claimtrace_claim "
            "WHERE tracking_id = %s FOR UPDATE",
            (claim["tracking_id"],),
        )
        locked = cur.fetchone()
        if locked is None:
            return {"updated": False}
        existing_steps = list(locked.get("recommended_next_steps") or [])
        if note:
            existing_steps.insert(0, note)
        cur.execute(
            """
            UPDATE claimtrace_claim
               SET action_state = %s,
                   status = %s,
                   recommended_next_steps = %s::jsonb,
                   updated_at = NOW()
             WHERE tracking_id = %s
             RETURNING tracking_id, claim_id, claim_hash_id, import_id, job_id, filename,
                       uploaded_by, trading_partner_id, submitter_id, trace_id, state_hash,
                       status, action_state, recommended_next_steps, raw_excerpt, created_at, updated_at
            """,
            (action_state, status, json.dumps(existing_steps), claim["tracking_id"]),
        )
        updated = _claim_dict(cur.fetchone())
    event = append_claimtrace_event(
        conn,
        tracking_id=updated["tracking_id"],
        claim_id=updated["claim_id"],
        operation_type=operation,
        state_hash=updated["state_hash"],
        payload_location=f"claimtrace://action/{updated['tracking_id']}",
        service_name="claimtrace-dashboard",
        correlation_ids={"action": action, "note": note or ""},
    )
    conn.commit()
    return {"updated": True, "claim": updated, "event": event}
