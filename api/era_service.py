"""Database-backed 835 restore persistence (Workstream 5).

Stores every produced/received 835 immutably together with its parsed structure,
linked to Claimtrace, and provides the three restore operations:

* **re-delivery** — return the stored bytes verbatim (same TRN);
* **reconstruction** — rebuild from stored structured data, balance-verified and
  marked as a reconstruction in Claimtrace lineage;
* **reversal** — derive a CLP02=22 reversal as a new immutable artifact/event.

This module never mutates remittance amounts; era_artifact rows are immutable
(enforced by triggers) and every operation appends an append-only Claimtrace
event so the lineage is complete and tamper-evident.
"""
from __future__ import annotations

import base64
import os
import uuid
from typing import Any, Optional

from psycopg2.extras import Json, RealDictCursor

from claimtrace.era import (
    balance_report,
    build_835,
    build_reversal,
    parse_835,
    reconstruct_835,
)
from claimtrace.era.model import Era835

try:
    from . import claimtrace_service
except ImportError:  # direct script/test invocation
    import claimtrace_service  # type: ignore

ERA_ENABLED = os.getenv("TRIAGE_ERA_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

VALID_DIRECTIONS = {"produced", "received"}
VALID_ORIGINS = {"original", "reconstructed", "reversal", "replacement"}


def era_enabled() -> bool:
    return ERA_ENABLED


def ensure_era_tables(conn) -> None:
    """Create the immutable era_artifact table + immutability triggers."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS era_artifact (
                artifact_id UUID PRIMARY KEY,
                tracking_id UUID REFERENCES claimtrace_claim(tracking_id) ON DELETE SET NULL,
                claim_id TEXT,
                trn TEXT,
                st_control TEXT,
                direction TEXT NOT NULL DEFAULT 'produced',
                origin TEXT NOT NULL DEFAULT 'original',
                content_sha256 TEXT NOT NULL,
                raw_835 BYTEA NOT NULL,
                parsed JSONB NOT NULL DEFAULT '{}',
                bpr_amount NUMERIC,
                bpr_method TEXT,
                payer_id TEXT,
                payee_id TEXT,
                balanced BOOLEAN NOT NULL DEFAULT TRUE,
                balance_report JSONB NOT NULL DEFAULT '{}',
                source_artifact_id UUID REFERENCES era_artifact(artifact_id) ON DELETE SET NULL,
                reverses_artifact_id UUID REFERENCES era_artifact(artifact_id) ON DELETE SET NULL,
                correlation_ids JSONB NOT NULL DEFAULT '{}',
                created_by TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS era_artifact_trn_idx ON era_artifact (trn)")
        cur.execute("CREATE INDEX IF NOT EXISTS era_artifact_claim_idx ON era_artifact (claim_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS era_artifact_tracking_idx ON era_artifact (tracking_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS era_artifact_hash_idx ON era_artifact (content_sha256)")
        cur.execute("CREATE INDEX IF NOT EXISTS era_artifact_origin_idx ON era_artifact (origin)")
        cur.execute(
            """
            CREATE OR REPLACE FUNCTION reject_era_artifact_mutation()
            RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'era_artifact is immutable; UPDATE and DELETE are forbidden';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        cur.execute("DROP TRIGGER IF EXISTS era_artifact_immutable_update ON era_artifact")
        cur.execute(
            """
            CREATE TRIGGER era_artifact_immutable_update
            BEFORE UPDATE ON era_artifact
            FOR EACH ROW EXECUTE FUNCTION reject_era_artifact_mutation()
            """
        )
        cur.execute("DROP TRIGGER IF EXISTS era_artifact_immutable_delete ON era_artifact")
        cur.execute(
            """
            CREATE TRIGGER era_artifact_immutable_delete
            BEFORE DELETE ON era_artifact
            FOR EACH ROW EXECUTE FUNCTION reject_era_artifact_mutation()
            """
        )
    conn.commit()


def _artifact_dict(row: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    result = dict(row)
    for key in ("artifact_id", "tracking_id", "source_artifact_id", "reverses_artifact_id"):
        if result.get(key) is not None:
            result[key] = str(result[key])
    if result.get("created_at") is not None:
        result["created_at"] = result["created_at"].isoformat()
    if result.get("bpr_amount") is not None:
        result["bpr_amount"] = str(result["bpr_amount"])
    raw = result.pop("raw_835", None)
    if include_raw and raw is not None:
        raw_bytes = bytes(raw)
        result["raw_835_b64"] = base64.b64encode(raw_bytes).decode("ascii")
        result["raw_835_text"] = raw_bytes.decode("utf-8", errors="replace")
    return result


def _summarize(era: Era835) -> dict[str, Any]:
    report = balance_report(era)
    return {
        "trn": era.trn,
        "st_control": era.st_control,
        "bpr_amount": era.bpr_amount,
        "bpr_method": era.bpr_method,
        "payer_id": era.payer_id,
        "payee_id": era.payee_id,
        "balanced": report.balanced,
        "balance_report": report.to_dict(),
        "parsed": era.to_dict(),
    }


def _insert_artifact(
    conn,
    *,
    raw_bytes: bytes,
    era: Era835,
    tracking_id: Optional[str],
    claim_id: Optional[str],
    direction: str,
    origin: str,
    created_by: Optional[str],
    source_artifact_id: Optional[str] = None,
    reverses_artifact_id: Optional[str] = None,
    correlation_ids: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if direction not in VALID_DIRECTIONS:
        raise ValueError(f"direction must be one of {sorted(VALID_DIRECTIONS)}")
    if origin not in VALID_ORIGINS:
        raise ValueError(f"origin must be one of {sorted(VALID_ORIGINS)}")

    summary = _summarize(era)
    artifact_id = uuid.uuid4()
    content_sha256 = claimtrace_service.hash_payload(raw_bytes)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO era_artifact (
                artifact_id, tracking_id, claim_id, trn, st_control, direction,
                origin, content_sha256, raw_835, parsed, bpr_amount, bpr_method,
                payer_id, payee_id, balanced, balance_report, source_artifact_id,
                reverses_artifact_id, correlation_ids, created_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING artifact_id, tracking_id, claim_id, trn, st_control, direction,
                      origin, content_sha256, parsed, bpr_amount, bpr_method,
                      payer_id, payee_id, balanced, balance_report,
                      source_artifact_id, reverses_artifact_id, correlation_ids,
                      created_by, created_at
            """,
            (
                str(artifact_id),
                str(tracking_id) if tracking_id else None,
                claim_id,
                summary["trn"],
                summary["st_control"],
                direction,
                origin,
                content_sha256,
                psycopg2_binary(raw_bytes),
                Json(summary["parsed"]),
                summary["bpr_amount"],
                summary["bpr_method"],
                summary["payer_id"],
                summary["payee_id"],
                summary["balanced"],
                Json(summary["balance_report"]),
                source_artifact_id,
                reverses_artifact_id,
                Json(correlation_ids or {}),
                created_by,
            ),
        )
        row = cur.fetchone()

    op_map = {
        "original": "STORE_835",
        "reconstructed": "RECONSTRUCT_835",
        "reversal": "REVERSE_835",
        "replacement": "REPLACE_835",
    }
    if claim_id:
        claimtrace_service.append_claimtrace_event(
            conn,
            tracking_id=tracking_id,
            claim_id=claim_id,
            operation_type=op_map[origin],
            state_hash=content_sha256,
            payload_location=f"era_artifact:{artifact_id}",
            service_name="triage-era",
            correlation_ids={
                "artifact_id": str(artifact_id),
                "trn": summary["trn"],
                "origin": origin,
                "direction": direction,
                "balanced": summary["balanced"],
                "source_artifact_id": source_artifact_id,
                "reverses_artifact_id": reverses_artifact_id,
                **(correlation_ids or {}),
            },
        )
    conn.commit()
    return _artifact_dict(row)


def psycopg2_binary(data: bytes):
    import psycopg2

    return psycopg2.Binary(data)


def store_835(
    conn,
    *,
    content: bytes | str,
    tracking_id: Optional[str] = None,
    claim_id: Optional[str] = None,
    direction: str = "produced",
    created_by: Optional[str] = None,
    correlation_ids: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Store an 835 immutably with its parsed structure + Claimtrace linkage."""
    raw_bytes = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    if not raw_bytes.strip():
        raise ValueError("835 content is empty")
    era = parse_835(raw_bytes.decode("utf-8", errors="replace"))
    if not era.claims:
        raise ValueError("input does not parse as an 835 (no CLP claim loops found)")
    return _insert_artifact(
        conn,
        raw_bytes=raw_bytes,
        era=era,
        tracking_id=tracking_id,
        claim_id=claim_id,
        direction=direction,
        origin="original",
        created_by=created_by,
        correlation_ids=correlation_ids,
    )


def get_artifact(conn, artifact_id: str, *, include_raw: bool = False) -> Optional[dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cols = (
            "artifact_id, tracking_id, claim_id, trn, st_control, direction, origin, "
            "content_sha256, parsed, bpr_amount, bpr_method, payer_id, payee_id, "
            "balanced, balance_report, source_artifact_id, reverses_artifact_id, "
            "correlation_ids, created_by, created_at"
        )
        if include_raw:
            cols += ", raw_835"
        cur.execute(
            f"SELECT {cols} FROM era_artifact WHERE artifact_id = %s",
            (artifact_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _artifact_dict(row, include_raw=include_raw)


def list_artifacts(
    conn,
    *,
    trn: Optional[str] = None,
    claim_id: Optional[str] = None,
    origin: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if trn:
        filters.append("trn ILIKE %s")
        params.append(f"%{trn.strip()}%")
    if claim_id:
        filters.append("claim_id = %s")
        params.append(claim_id.strip())
    if origin:
        filters.append("origin = %s")
        params.append(origin.strip())
    if query:
        like = f"%{query.strip()}%"
        filters.append("(trn ILIKE %s OR claim_id ILIKE %s OR content_sha256 ILIKE %s OR payer_id ILIKE %s)")
        params.extend([like, like, like, like])
    where = "WHERE " + " AND ".join(filters) if filters else ""
    params.append(max(1, min(limit, 200)))
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT artifact_id, tracking_id, claim_id, trn, st_control, direction,
                   origin, content_sha256, bpr_amount, bpr_method, payer_id,
                   payee_id, balanced, source_artifact_id, reverses_artifact_id,
                   created_by, created_at
              FROM era_artifact
              {where}
             ORDER BY created_at DESC
             LIMIT %s
            """,
            params,
        )
        return [_artifact_dict(row) for row in cur.fetchall()]


def summary(conn) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE origin = 'original'), "
            "COUNT(*) FILTER (WHERE origin = 'reconstructed'), "
            "COUNT(*) FILTER (WHERE origin = 'reversal'), "
            "COUNT(*) FILTER (WHERE NOT balanced) FROM era_artifact"
        )
        total, originals, reconstructed, reversals, unbalanced = cur.fetchone()
    return {
        "enabled": True,
        "artifacts": int(total or 0),
        "originals": int(originals or 0),
        "reconstructed": int(reconstructed or 0),
        "reversals": int(reversals or 0),
        "unbalanced": int(unbalanced or 0),
    }


def redeliver(conn, artifact_id: str, *, actor: Optional[str] = None) -> dict[str, Any]:
    """Return the stored 835 bytes verbatim (byte-for-byte re-delivery, same TRN)."""
    artifact = get_artifact(conn, artifact_id, include_raw=True)
    if artifact is None:
        return {"found": False}
    # Log the re-delivery as a Claimtrace event for audit fidelity.
    if artifact.get("claim_id"):
        claimtrace_service.append_claimtrace_event(
            conn,
            tracking_id=artifact.get("tracking_id"),
            claim_id=artifact["claim_id"],
            operation_type="RE_DELIVER_835",
            state_hash=artifact["content_sha256"],
            payload_location=f"era_artifact:{artifact_id}",
            service_name="triage-era",
            correlation_ids={
                "artifact_id": artifact_id,
                "trn": artifact.get("trn"),
                "actor": actor or "",
                "byte_exact": True,
            },
        )
        conn.commit()
    return {
        "found": True,
        "artifact_id": artifact_id,
        "trn": artifact.get("trn"),
        "content_sha256": artifact["content_sha256"],
        "raw_835_b64": artifact["raw_835_b64"],
        "raw_835_text": artifact["raw_835_text"],
        "filename": f"redeliver_{artifact.get('trn') or artifact_id}.835",
    }


def reconstruct(conn, artifact_id: str, *, created_by: Optional[str] = None) -> dict[str, Any]:
    """Rebuild an 835 from the stored parsed structure, balance-verify, and store
    the result as a new immutable artifact marked as a reconstruction."""
    artifact = get_artifact(conn, artifact_id, include_raw=False)
    if artifact is None:
        return {"found": False}
    era = Era835.from_dict(artifact["parsed"])
    rebuilt_text = reconstruct_835(era)
    rebuilt_era = parse_835(rebuilt_text)
    report = balance_report(rebuilt_era)
    stored = _insert_artifact(
        conn,
        raw_bytes=rebuilt_text.encode("utf-8"),
        era=rebuilt_era,
        tracking_id=artifact.get("tracking_id"),
        claim_id=artifact.get("claim_id"),
        direction=artifact.get("direction", "produced"),
        origin="reconstructed",
        created_by=created_by,
        source_artifact_id=artifact_id,
        correlation_ids={"reconstructed_from": artifact_id},
    )
    return {
        "found": True,
        "artifact": stored,
        "balanced": report.balanced,
        "balance_report": report.to_dict(),
        "x12": rebuilt_text,
    }


def reverse(conn, artifact_id: str, *, created_by: Optional[str] = None) -> dict[str, Any]:
    """Derive a CLP02=22 reversal of the stored 835 and store it as a new
    immutable artifact/event, preserving reversal-then-correction ordering."""
    artifact = get_artifact(conn, artifact_id, include_raw=False)
    if artifact is None:
        return {"found": False}
    era = Era835.from_dict(artifact["parsed"])
    reversal_era = build_reversal(era)
    reversal_text = build_835(reversal_era)
    reparsed = parse_835(reversal_text)
    report = balance_report(reparsed)
    stored = _insert_artifact(
        conn,
        raw_bytes=reversal_text.encode("utf-8"),
        era=reparsed,
        tracking_id=artifact.get("tracking_id"),
        claim_id=artifact.get("claim_id"),
        direction=artifact.get("direction", "produced"),
        origin="reversal",
        created_by=created_by,
        reverses_artifact_id=artifact_id,
        correlation_ids={"reverses": artifact_id},
    )
    return {
        "found": True,
        "artifact": stored,
        "balanced": report.balanced,
        "balance_report": report.to_dict(),
        "x12": reversal_text,
    }
