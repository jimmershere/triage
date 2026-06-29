"""FastAPI routes for the advisory mapping-suggestion service (Workstream 1).

These endpoints are the human-approval surface for the ``mapping_advisor``
module. The advisor proposes candidate X12⇄flat-file mappings and 999-derived
validation rules; a human reviews the queue and approves/rejects each one.

Governance: there is intentionally **no** endpoint that applies a mapping to a
submission. Approval only records a *versioned* rule (config), it never mutates
claim/submission data. All write endpoints sit behind ``require_secret`` (the
``frontend_go`` reverse proxy injects ``X-TRIAGE-SECRET`` server-side after
OAuth/RBAC), matching every other privileged route in this API.
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

# Make ``worker_py`` importable when uvicorn loads ``api.app:app`` from the repo
# root (mirrors turbo_routes; the advisor lives in the isolated worker package).
_WORKER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "worker_py"))
if _WORKER_DIR not in sys.path:
    sys.path.insert(0, _WORKER_DIR)

from mapping_advisor import MappingAdvisor  # noqa: E402
from mapping_advisor.repository import rule_key_for, summarize  # noqa: E402

logger = logging.getLogger("api.mapping")

router = APIRouter(prefix="/mapping/advisor", tags=["mapping-advisor"])

GET_DB: Optional[Callable[[], Any]] = None

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"


def require_secret(header_value: Optional[str] = Header(None, alias="X-TRIAGE-SECRET")) -> None:
    """Guard mirroring ``api.app.require_secret``.

    The ``frontend_go`` reverse proxy injects ``X-TRIAGE-SECRET`` server-side
    after OAuth/RBAC, so requiring it here keeps the advisory queue behind the
    same access controls as the rest of the privileged API. Reads the shared
    secret from the environment directly to avoid an import cycle with ``app``.
    """
    shared = os.getenv("TRIAGE_SHARED_SECRET", "").strip()
    if not shared:
        return
    if header_value is None or not secrets.compare_digest(header_value, shared):
        raise HTTPException(status_code=401, detail="unauthorized")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class SuggestRequest(BaseModel):
    x12_text: str = Field(..., description="X12 transaction text (de-identified).")
    flat_file_text: Optional[str] = Field(
        None, description="Corresponding flat-file sample (one record is enough)."
    )
    delimiter: Optional[str] = Field(None, description="Delimiter for delimited files.")
    header: bool = Field(False, description="First flat-file row holds field names.")
    layout: Optional[list[tuple[str, int, int]]] = Field(
        None, description="Fixed-width layout as [(name, start, length), ...]."
    )
    ack_999_text: Optional[str] = Field(
        None, description="The 999 acknowledgement, for validation-rule inference."
    )
    partner_id: Optional[str] = None
    top_k: int = Field(25, ge=1, le=200)
    min_confidence: float = Field(0.0, ge=0.0, le=1.0)
    persist: bool = Field(
        True, description="Enqueue the suggestions for human review."
    )


class DecisionRequest(BaseModel):
    approver: str = Field(..., min_length=1, max_length=128)
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Persistence (Postgres) — mirrors mapping_advisor.repository contract.
# ---------------------------------------------------------------------------
def ensure_mapping_tables(conn) -> None:
    """Create the advisory queue + versioned-rule tables if absent.

    Mirrors ``migrations/007_mapping.sql`` so a fresh DB is bootstrapped by the
    application's startup migration the same way the other tables are.
    """
    logger.info("Ensuring mapping advisor tables exist")
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS mapping_suggestion (
                id BIGSERIAL PRIMARY KEY,
                transaction_set TEXT,
                partner_id TEXT,
                rule_type TEXT NOT NULL,
                rule_key TEXT NOT NULL,
                summary TEXT,
                score DOUBLE PRECISION NOT NULL DEFAULT 0,
                confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
                payload JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                decided_at TIMESTAMPTZ,
                decided_by TEXT,
                decision_reason TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS mapping_rule (
                id BIGSERIAL PRIMARY KEY,
                suggestion_id BIGINT REFERENCES mapping_suggestion(id) ON DELETE SET NULL,
                transaction_set TEXT,
                partner_id TEXT,
                rule_type TEXT NOT NULL,
                rule_key TEXT NOT NULL,
                version INTEGER NOT NULL,
                definition JSONB NOT NULL,
                approved_by TEXT NOT NULL,
                approved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                active BOOLEAN NOT NULL DEFAULT TRUE,
                UNIQUE (rule_key, version)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS mapping_suggestion_status_idx ON mapping_suggestion (status)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS mapping_suggestion_rule_key_idx ON mapping_suggestion (rule_key)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS mapping_rule_active_idx ON mapping_rule (rule_key, active)"
        )
    conn.commit()


def _Json(value):
    from psycopg2.extras import Json

    return Json(value)


def _dict_cursor(conn):
    from psycopg2.extras import RealDictCursor

    return conn.cursor(cursor_factory=RealDictCursor)


def _with_db(callback):
    if GET_DB is None:
        raise HTTPException(status_code=503, detail="Mapping advisor database is not configured")
    try:
        with GET_DB() as conn:
            return callback(conn)
    except HTTPException:
        raise
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Mapping advisor database error")
        raise HTTPException(status_code=500, detail=f"mapping advisor error: {exc}") from exc


def _enqueue(conn, suggestion_set) -> list[int]:
    ts = suggestion_set.transaction_set
    partner = suggestion_set.partner_id
    rows: list[dict[str, Any]] = []
    for cand in suggestion_set.candidates:
        payload = cand.to_dict()
        rows.append((payload.get("rule_type", "element_map"), payload.get("score", 0.0), payload.get("confidence", 0.0), payload))
    for vr in suggestion_set.validation_rules:
        payload = vr.to_dict()
        rows.append(("validation_rule", payload.get("confidence", 0.0), payload.get("confidence", 0.0), payload))

    ids: list[int] = []
    with conn.cursor() as cur:
        for rule_type, score, confidence, payload in rows:
            cur.execute(
                """
                INSERT INTO mapping_suggestion
                    (transaction_set, partner_id, rule_type, rule_key, summary,
                     score, confidence, payload, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                RETURNING id
                """,
                (
                    ts,
                    partner,
                    rule_type,
                    rule_key_for(payload, ts, partner),
                    summarize(payload),
                    float(score),
                    float(confidence),
                    _Json(payload),
                ),
            )
            ids.append(cur.fetchone()[0])
    conn.commit()
    return ids


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/summary")
def advisor_summary(_: None = Depends(require_secret)) -> dict[str, Any]:
    def run(conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, COUNT(*) FROM mapping_suggestion GROUP BY status"
            )
            counts = {row[0]: int(row[1]) for row in cur.fetchall()}
            cur.execute("SELECT COUNT(*) FROM mapping_rule WHERE active")
            active_rules = int(cur.fetchone()[0])
        return {
            "advisory": True,
            "governance": "human-approved, versioned; never auto-applied",
            "pending": counts.get(PENDING, 0),
            "approved": counts.get(APPROVED, 0),
            "rejected": counts.get(REJECTED, 0),
            "active_rules": active_rules,
        }

    return _with_db(run)


@router.post("/suggest")
def advisor_suggest(payload: SuggestRequest, _: None = Depends(require_secret)) -> dict[str, Any]:
    advisor = MappingAdvisor()
    layout = [tuple(item) for item in payload.layout] if payload.layout else None
    suggestion_set = advisor.suggest(
        payload.x12_text,
        flat_file_text=payload.flat_file_text,
        layout=layout,
        delimiter=payload.delimiter,
        header=payload.header,
        ack_999_text=payload.ack_999_text,
        partner_id=payload.partner_id,
        top_k=payload.top_k,
        min_confidence=payload.min_confidence,
    )
    result = suggestion_set.to_dict()
    if payload.persist:
        result["enqueued_ids"] = _with_db(lambda conn: _enqueue(conn, suggestion_set))
    return result


@router.get("/suggestions")
def list_suggestions(
    status: Optional[str] = Query(None, pattern="^(pending|approved|rejected)$"),
    limit: int = Query(100, ge=1, le=500),
    _: None = Depends(require_secret),
) -> dict[str, Any]:
    def run(conn):
        with _dict_cursor(conn) as cur:
            if status:
                cur.execute(
                    """
                    SELECT * FROM mapping_suggestion WHERE status = %s
                    ORDER BY confidence DESC, id DESC LIMIT %s
                    """,
                    (status, limit),
                )
            else:
                cur.execute(
                    "SELECT * FROM mapping_suggestion ORDER BY confidence DESC, id DESC LIMIT %s",
                    (limit,),
                )
            rows = [dict(r) for r in cur.fetchall()]
        return {"suggestions": rows, "count": len(rows)}

    return _with_db(run)


@router.post("/suggestions/{suggestion_id}/approve")
def approve_suggestion(
    suggestion_id: int, body: DecisionRequest, _: None = Depends(require_secret)
) -> dict[str, Any]:
    def run(conn):
        with _dict_cursor(conn) as cur:
            cur.execute(
                "SELECT * FROM mapping_suggestion WHERE id = %s FOR UPDATE",
                (suggestion_id,),
            )
            rec = cur.fetchone()
            if rec is None:
                raise KeyError(f"suggestion {suggestion_id} not found")
            if rec["status"] == APPROVED:
                raise ValueError(f"suggestion {suggestion_id} already approved")
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM mapping_rule WHERE rule_key = %s",
                (rec["rule_key"],),
            )
            next_version = int(cur.fetchone()["v"]) + 1
            cur.execute(
                "UPDATE mapping_rule SET active = FALSE WHERE rule_key = %s AND active",
                (rec["rule_key"],),
            )
            cur.execute(
                """
                INSERT INTO mapping_rule
                    (suggestion_id, transaction_set, partner_id, rule_type, rule_key,
                     version, definition, approved_by, active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                RETURNING id, version
                """,
                (
                    rec["id"],
                    rec["transaction_set"],
                    rec["partner_id"],
                    rec["rule_type"],
                    rec["rule_key"],
                    next_version,
                    _Json(rec["payload"]),
                    body.approver,
                ),
            )
            rule = dict(cur.fetchone())
            cur.execute(
                "UPDATE mapping_suggestion SET status = 'approved', decided_at = NOW(), decided_by = %s WHERE id = %s",
                (body.approver, suggestion_id),
            )
        conn.commit()
        return {
            "approved": True,
            "suggestion_id": suggestion_id,
            "rule_id": rule["id"],
            "version": rule["version"],
            "rule_key": rec["rule_key"],
        }

    return _with_db(run)


@router.post("/suggestions/{suggestion_id}/reject")
def reject_suggestion(
    suggestion_id: int, body: DecisionRequest, _: None = Depends(require_secret)
) -> dict[str, Any]:
    def run(conn):
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE mapping_suggestion SET status = 'rejected', decided_at = NOW(), decided_by = %s, decision_reason = %s WHERE id = %s",
                (body.approver, body.reason or "", suggestion_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"suggestion {suggestion_id} not found")
        conn.commit()
        return {"rejected": True, "suggestion_id": suggestion_id}

    return _with_db(run)


@router.get("/rules")
def list_rules(
    active_only: bool = True,
    limit: int = Query(100, ge=1, le=500),
    _: None = Depends(require_secret),
) -> dict[str, Any]:
    def run(conn):
        with _dict_cursor(conn) as cur:
            if active_only:
                cur.execute(
                    "SELECT * FROM mapping_rule WHERE active ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
            else:
                cur.execute(
                    "SELECT * FROM mapping_rule ORDER BY id DESC LIMIT %s", (limit,)
                )
            rows = [dict(r) for r in cur.fetchall()]
        return {"rules": rows, "count": len(rows)}

    return _with_db(run)


def register(app, get_db_func: Optional[Callable[[], Any]] = None) -> None:
    global GET_DB
    GET_DB = get_db_func
    app.include_router(router)
