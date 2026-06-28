"""Database-backed Operational Helpdesk persistence (Workstream 4).

Implements case CRUD, the status machine (open -> submitter-notified ->
awaiting-resubmission -> resolved), the "notify submitter" packaging, and the
RabbitMQ-driven resubmission auto-linking. Every case action is appended to an
append-only ``helpdesk_case_event`` audit and (when linked to a claim) mirrored
to the Claimtrace journal so helpdesk activity is tied to claim lineage.

Cases REFERENCE claim data — they never edit it — and store only reference
identifiers and error locations (PHI-safe).
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Optional

from psycopg2.extras import Json, RealDictCursor

from claimtrace.helpdesk import (
    STATUS_AWAITING,
    STATUS_NOTIFIED,
    STATUS_OPEN,
    STATUS_RESOLVED,
    CaseStatusError,
    validate_transition,
)
from claimtrace.helpdesk.rejection_report import RejectionContext, RejectionItem, build_rejection_report

try:
    from . import claimtrace_service
except ImportError:  # direct script/test invocation
    import claimtrace_service  # type: ignore

logger = logging.getLogger("api.helpdesk")

HELPDESK_ENABLED = os.getenv("TRIAGE_HELPDESK_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

QUEUE_STATUSES = (STATUS_OPEN, STATUS_NOTIFIED, STATUS_AWAITING)


def helpdesk_enabled() -> bool:
    return HELPDESK_ENABLED


def ensure_helpdesk_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE SEQUENCE IF NOT EXISTS helpdesk_case_number_seq")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS helpdesk_case (
                case_id UUID PRIMARY KEY,
                case_number TEXT UNIQUE NOT NULL,
                tracking_id UUID REFERENCES claimtrace_claim(tracking_id) ON DELETE SET NULL,
                claim_id TEXT,
                source_event_id UUID REFERENCES claimtrace_event(event_id) ON DELETE SET NULL,
                trading_partner_id TEXT,
                submitter_id TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                priority TEXT NOT NULL DEFAULT 'normal',
                ack_type TEXT,
                reason_code TEXT,
                reason_text TEXT,
                segment_id TEXT,
                element_position TEXT,
                loop_id TEXT,
                resubmission_claim_id TEXT,
                assigned_to TEXT,
                correlation_ids JSONB NOT NULL DEFAULT '{}',
                created_by TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS helpdesk_case_status_idx ON helpdesk_case (status)")
        cur.execute("CREATE INDEX IF NOT EXISTS helpdesk_case_claim_idx ON helpdesk_case (claim_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS helpdesk_case_partner_idx ON helpdesk_case (trading_partner_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS helpdesk_case_tracking_idx ON helpdesk_case (tracking_id)")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS helpdesk_case_event (
                event_id UUID PRIMARY KEY,
                case_id UUID NOT NULL REFERENCES helpdesk_case(case_id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT,
                detail JSONB NOT NULL DEFAULT '{}',
                correlation_ids JSONB NOT NULL DEFAULT '{}',
                actor TEXT,
                ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS helpdesk_case_event_case_ts_idx ON helpdesk_case_event (case_id, ts)")
        cur.execute(
            """
            CREATE OR REPLACE FUNCTION reject_helpdesk_case_event_mutation()
            RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'helpdesk_case_event is append-only; UPDATE and DELETE are forbidden';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        cur.execute("DROP TRIGGER IF EXISTS helpdesk_case_event_append_only_update ON helpdesk_case_event")
        cur.execute(
            """
            CREATE TRIGGER helpdesk_case_event_append_only_update
            BEFORE UPDATE ON helpdesk_case_event
            FOR EACH ROW EXECUTE FUNCTION reject_helpdesk_case_event_mutation()
            """
        )
        cur.execute("DROP TRIGGER IF EXISTS helpdesk_case_event_append_only_delete ON helpdesk_case_event")
        cur.execute(
            """
            CREATE TRIGGER helpdesk_case_event_append_only_delete
            BEFORE DELETE ON helpdesk_case_event
            FOR EACH ROW EXECUTE FUNCTION reject_helpdesk_case_event_mutation()
            """
        )
    conn.commit()


def _case_dict(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in ("case_id", "tracking_id", "source_event_id"):
        if result.get(key) is not None:
            result[key] = str(result[key])
    for key in ("created_at", "updated_at"):
        if result.get(key) is not None:
            result[key] = result[key].isoformat()
    return result


def _event_dict(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in ("event_id", "case_id"):
        if result.get(key) is not None:
            result[key] = str(result[key])
    if result.get("ts") is not None:
        result["ts"] = result["ts"].isoformat()
    return result


def _append_case_event(
    conn,
    *,
    case_id: str,
    event_type: str,
    from_status: Optional[str] = None,
    to_status: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
    correlation_ids: Optional[dict[str, Any]] = None,
    actor: Optional[str] = None,
) -> dict[str, Any]:
    event_id = uuid.uuid4()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO helpdesk_case_event (
                event_id, case_id, event_type, from_status, to_status, detail,
                correlation_ids, actor
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING event_id, case_id, event_type, from_status, to_status, detail,
                      correlation_ids, actor, ts
            """,
            (
                str(event_id),
                case_id,
                event_type,
                from_status,
                to_status,
                Json(detail or {}),
                Json(correlation_ids or {}),
                actor,
            ),
        )
        row = cur.fetchone()
    return _event_dict(row)


def _mirror_to_claimtrace(conn, case: dict[str, Any], operation: str, correlation: dict[str, Any]) -> None:
    """Tie a helpdesk action into Claimtrace lineage (PHI-safe correlation only)."""
    if not case.get("claim_id"):
        return
    claimtrace_service.append_claimtrace_event(
        conn,
        tracking_id=case.get("tracking_id"),
        claim_id=case["claim_id"],
        operation_type=operation,
        state_hash=claimtrace_service.sha256_hex(case["case_id"].encode("utf-8")),
        payload_location=f"helpdesk_case:{case['case_id']}",
        service_name="triage-helpdesk",
        correlation_ids={"case_id": case["case_id"], "case_number": case.get("case_number"), **correlation},
    )


def create_case(
    conn,
    *,
    claim_id: Optional[str] = None,
    tracking_id: Optional[str] = None,
    source_event_id: Optional[str] = None,
    trading_partner_id: Optional[str] = None,
    submitter_id: Optional[str] = None,
    ack_type: Optional[str] = None,
    reason_code: Optional[str] = None,
    reason_text: Optional[str] = None,
    segment_id: Optional[str] = None,
    element_position: Optional[str] = None,
    loop_id: Optional[str] = None,
    priority: str = "normal",
    created_by: Optional[str] = None,
    correlation_ids: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    case_id = uuid.uuid4()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT nextval('helpdesk_case_number_seq')")
        seq = cur.fetchone()["nextval"]
        case_number = f"HD-{int(seq):06d}"
        cur.execute(
            """
            INSERT INTO helpdesk_case (
                case_id, case_number, tracking_id, claim_id, source_event_id,
                trading_partner_id, submitter_id, status, priority, ack_type,
                reason_code, reason_text, segment_id, element_position, loop_id,
                correlation_ids, created_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,'open',%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING case_id, case_number, tracking_id, claim_id, source_event_id,
                      trading_partner_id, submitter_id, status, priority, ack_type,
                      reason_code, reason_text, segment_id, element_position, loop_id,
                      resubmission_claim_id, assigned_to, correlation_ids, created_by,
                      created_at, updated_at
            """,
            (
                str(case_id),
                case_number,
                str(tracking_id) if tracking_id else None,
                claim_id,
                source_event_id,
                trading_partner_id,
                submitter_id,
                priority,
                ack_type,
                reason_code,
                reason_text,
                segment_id,
                element_position,
                loop_id,
                Json(correlation_ids or {}),
                created_by,
            ),
        )
        case = _case_dict(cur.fetchone())
    _append_case_event(
        conn,
        case_id=case["case_id"],
        event_type="CASE_OPENED",
        to_status=STATUS_OPEN,
        detail={
            "reason_code": reason_code,
            "reason_text": reason_text,
            "segment_id": segment_id,
            "element_position": element_position,
            "loop_id": loop_id,
            "ack_type": ack_type,
        },
        correlation_ids=correlation_ids,
        actor=created_by,
    )
    _mirror_to_claimtrace(conn, case, "HELPDESK_CASE_OPENED", {"reason_code": reason_code or ""})
    conn.commit()
    return case


def get_case(conn, case_id: str) -> Optional[dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT case_id, case_number, tracking_id, claim_id, source_event_id,
                   trading_partner_id, submitter_id, status, priority, ack_type,
                   reason_code, reason_text, segment_id, element_position, loop_id,
                   resubmission_claim_id, assigned_to, correlation_ids, created_by,
                   created_at, updated_at
              FROM helpdesk_case
             WHERE case_id::text = %s OR case_number = %s
             LIMIT 1
            """,
            (case_id, case_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        case = _case_dict(row)
        cur.execute(
            """
            SELECT event_id, case_id, event_type, from_status, to_status, detail,
                   correlation_ids, actor, ts
              FROM helpdesk_case_event
             WHERE case_id = %s
             ORDER BY ts ASC, event_id ASC
            """,
            (case["case_id"],),
        )
        events = [_event_dict(r) for r in cur.fetchall()]
    return {"case": case, "events": events}


def list_cases(
    conn,
    *,
    status: Optional[str] = None,
    trading_partner_id: Optional[str] = None,
    submitter_id: Optional[str] = None,
    claim_id: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if status:
        filters.append("status = %s")
        params.append(status.strip())
    for column, value in [
        ("trading_partner_id", trading_partner_id),
        ("submitter_id", submitter_id),
        ("claim_id", claim_id),
    ]:
        if value:
            filters.append(f"{column} ILIKE %s")
            params.append(f"%{value.strip()}%")
    if query:
        like = f"%{query.strip()}%"
        filters.append(
            "(case_number ILIKE %s OR claim_id ILIKE %s OR reason_code ILIKE %s "
            "OR reason_text ILIKE %s OR trading_partner_id ILIKE %s)"
        )
        params.extend([like, like, like, like, like])
    where = "WHERE " + " AND ".join(filters) if filters else ""
    params.append(max(1, min(limit, 200)))
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT case_id, case_number, tracking_id, claim_id, source_event_id,
                   trading_partner_id, submitter_id, status, priority, ack_type,
                   reason_code, reason_text, segment_id, element_position, loop_id,
                   resubmission_claim_id, assigned_to, correlation_ids, created_by,
                   created_at, updated_at
              FROM helpdesk_case
              {where}
             ORDER BY created_at DESC
             LIMIT %s
            """,
            params,
        )
        return [_case_dict(row) for row in cur.fetchall()]


def queue(conn, *, limit: int = 100) -> list[dict[str, Any]]:
    """The operator work queue — every non-resolved case, newest first."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT case_id, case_number, tracking_id, claim_id, trading_partner_id,
                   submitter_id, status, priority, ack_type, reason_code, reason_text,
                   segment_id, element_position, loop_id, resubmission_claim_id,
                   created_at, updated_at
              FROM helpdesk_case
             WHERE status <> 'resolved'
             ORDER BY (priority = 'high') DESC, created_at ASC
             LIMIT %s
            """,
            (max(1, min(limit, 500)),),
        )
        return [_case_dict(row) for row in cur.fetchall()]


def summary(conn) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*), "
            "COUNT(*) FILTER (WHERE status = 'open'), "
            "COUNT(*) FILTER (WHERE status = 'submitter-notified'), "
            "COUNT(*) FILTER (WHERE status = 'awaiting-resubmission'), "
            "COUNT(*) FILTER (WHERE status = 'resolved') FROM helpdesk_case"
        )
        total, open_, notified, awaiting, resolved = cur.fetchone()
    return {
        "enabled": True,
        "total": int(total or 0),
        "open": int(open_ or 0),
        "submitter_notified": int(notified or 0),
        "awaiting_resubmission": int(awaiting or 0),
        "resolved": int(resolved or 0),
        "queue_depth": int((open_ or 0) + (notified or 0) + (awaiting or 0)),
    }


def _set_status(
    conn,
    *,
    case: dict[str, Any],
    target: str,
    event_type: str,
    actor: Optional[str],
    detail: Optional[dict[str, Any]] = None,
    resubmission_claim_id: Optional[str] = None,
) -> dict[str, Any]:
    from_status = case["status"]
    validate_transition(from_status, target)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            UPDATE helpdesk_case
               SET status = %s,
                   resubmission_claim_id = COALESCE(%s, resubmission_claim_id),
                   updated_at = NOW()
             WHERE case_id = %s
            RETURNING case_id, case_number, tracking_id, claim_id, source_event_id,
                      trading_partner_id, submitter_id, status, priority, ack_type,
                      reason_code, reason_text, segment_id, element_position, loop_id,
                      resubmission_claim_id, assigned_to, correlation_ids, created_by,
                      created_at, updated_at
            """,
            (target, resubmission_claim_id, case["case_id"]),
        )
        updated = _case_dict(cur.fetchone())
    _append_case_event(
        conn,
        case_id=updated["case_id"],
        event_type=event_type,
        from_status=from_status,
        to_status=target,
        detail=detail,
        actor=actor,
    )
    _mirror_to_claimtrace(
        conn,
        updated,
        "HELPDESK_STATUS_CHANGE",
        {"from": from_status, "to": target, "event_type": event_type},
    )
    conn.commit()
    return updated


def transition_status(conn, *, case_id: str, target: str, actor: Optional[str] = None, note: Optional[str] = None) -> dict[str, Any]:
    detail = get_case(conn, case_id)
    if not detail:
        return {"updated": False}
    updated = _set_status(
        conn,
        case=detail["case"],
        target=target,
        event_type="STATUS_CHANGE",
        actor=actor,
        detail={"note": note} if note else None,
    )
    return {"updated": True, "case": updated}


def add_note(conn, *, case_id: str, note: str, actor: Optional[str] = None) -> dict[str, Any]:
    detail = get_case(conn, case_id)
    if not detail:
        return {"updated": False}
    event = _append_case_event(
        conn,
        case_id=detail["case"]["case_id"],
        event_type="NOTE",
        detail={"note": note},
        actor=actor,
    )
    conn.commit()
    return {"updated": True, "event": event}


def notify_submitter(conn, *, case_id: str, actor: Optional[str] = None, extra_items: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """Package a human-readable rejection report and move the case to
    submitter-notified. The report is PHI-safe (reference ids + error locations)."""
    detail = get_case(conn, case_id)
    if not detail:
        return {"updated": False}
    case = detail["case"]

    items: list[RejectionItem] = []
    if case.get("reason_code") or case.get("segment_id"):
        items.append(
            RejectionItem(
                code=case.get("reason_code") or "",
                message=case.get("reason_text") or "",
                segment_id=case.get("segment_id") or "",
                element_position=case.get("element_position") or "",
                loop_id=case.get("loop_id") or "",
            )
        )
    for item in extra_items or []:
        items.append(RejectionItem.from_dict(item))

    ctx = RejectionContext(
        claim_id=case.get("claim_id") or "",
        trading_partner_id=case.get("trading_partner_id") or "",
        submitter_id=case.get("submitter_id") or "",
        ack_type=case.get("ack_type") or "999",
        correlation_id=case["case_id"],
        items=items,
    )
    report = build_rejection_report(ctx)

    # Only transition if currently open (idempotent re-notify keeps the report).
    if case["status"] == STATUS_OPEN:
        updated = _set_status(
            conn,
            case=case,
            target=STATUS_NOTIFIED,
            event_type="SUBMITTER_NOTIFIED",
            actor=actor,
            detail={"report_summary": report["summary"], "item_count": report["item_count"]},
        )
    else:
        updated = case
        _append_case_event(
            conn,
            case_id=case["case_id"],
            event_type="SUBMITTER_NOTIFIED",
            from_status=case["status"],
            to_status=case["status"],
            detail={"report_summary": report["summary"], "item_count": report["item_count"], "re_notify": True},
            actor=actor,
        )
        conn.commit()
    return {"updated": True, "case": updated, "report": report}


def apply_resubmission(
    conn,
    *,
    resubmission_claim_id: str,
    original_claim_id: Optional[str] = None,
    case_id: Optional[str] = None,
    passed: bool,
    actor: Optional[str] = None,
) -> dict[str, Any]:
    """RabbitMQ-driven: link a corrected resubmission to its open case(s) and
    resolve when it passes validation, or re-notify when it fails.

    Cases are matched by ``case_id`` if given, otherwise by the original claim id
    (Claimtrace deterministic identity ties the resubmission back to the case)."""
    target_cases: list[dict[str, Any]] = []
    if case_id:
        detail = get_case(conn, case_id)
        if detail:
            target_cases = [detail["case"]]
    else:
        match_claim = original_claim_id or resubmission_claim_id
        for case in list_cases(conn, claim_id=match_claim, limit=200):
            if case["status"] != STATUS_RESOLVED:
                target_cases.append(case)

    updated_cases: list[dict[str, Any]] = []
    for case in target_cases:
        if passed:
            # Move toward resolved; awaiting/notified/open can all resolve.
            try:
                updated = _set_status(
                    conn,
                    case=case,
                    target=STATUS_RESOLVED,
                    event_type="RESUBMISSION_PASSED",
                    actor=actor or "rabbitmq",
                    detail={"resubmission_claim_id": resubmission_claim_id},
                    resubmission_claim_id=resubmission_claim_id,
                )
                updated_cases.append(updated)
            except CaseStatusError:
                continue
        else:
            # Failed resubmission: ensure the case is awaiting another attempt.
            current = case["status"]
            if current == STATUS_NOTIFIED:
                target = STATUS_AWAITING
            elif current == STATUS_AWAITING:
                target = STATUS_NOTIFIED
            else:
                target = None
            if target:
                try:
                    updated = _set_status(
                        conn,
                        case=case,
                        target=target,
                        event_type="RESUBMISSION_FAILED",
                        actor=actor or "rabbitmq",
                        detail={"resubmission_claim_id": resubmission_claim_id},
                        resubmission_claim_id=resubmission_claim_id,
                    )
                    updated_cases.append(updated)
                except CaseStatusError:
                    continue
    return {"matched": len(target_cases), "updated": updated_cases}
