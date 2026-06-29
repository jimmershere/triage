"""FastAPI routes for the Operational Helpdesk (Workstream 4).

Mounts an :class:`APIRouter` under ``/helpdesk`` mirroring the Claimtrace
registration pattern. Exposes the failed-claim queue, case CRUD, status
transitions, "notify submitter" packaging, and the resubmission hook used by the
RabbitMQ consumer.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

try:
    from . import helpdesk_service
except ImportError:  # direct script/test invocation
    import helpdesk_service  # type: ignore

router = APIRouter(prefix="/helpdesk", tags=["helpdesk"])
GET_DB: Optional[Callable[[], Any]] = None
logger = logging.getLogger("api.helpdesk")


class CaseCreate(BaseModel):
    claim_id: Optional[str] = None
    tracking_id: Optional[str] = None
    source_event_id: Optional[str] = None
    trading_partner_id: Optional[str] = None
    submitter_id: Optional[str] = None
    ack_type: Optional[str] = None
    reason_code: Optional[str] = None
    reason_text: Optional[str] = None
    segment_id: Optional[str] = None
    element_position: Optional[str] = None
    loop_id: Optional[str] = None
    priority: str = "normal"
    created_by: Optional[str] = None
    correlation_ids: dict[str, Any] = Field(default_factory=dict)


class StatusUpdate(BaseModel):
    status: str
    actor: Optional[str] = None
    note: Optional[str] = None


class NoteRequest(BaseModel):
    note: str
    actor: Optional[str] = None


class NotifyRequest(BaseModel):
    actor: Optional[str] = None
    extra_items: list[dict[str, Any]] = Field(default_factory=list)


class ResubmissionRequest(BaseModel):
    resubmission_claim_id: str
    original_claim_id: Optional[str] = None
    case_id: Optional[str] = None
    passed: bool = True
    actor: Optional[str] = None


def _with_db(callback):
    if GET_DB is None:
        raise HTTPException(status_code=503, detail="Helpdesk database is not configured")
    try:
        with GET_DB() as conn:
            return callback(conn)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Helpdesk database error")
        raise HTTPException(status_code=500, detail=f"Helpdesk database error: {exc}") from exc


@router.get("/summary")
def summary() -> dict[str, Any]:
    return _with_db(helpdesk_service.summary)


@router.get("/queue")
def queue(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    def run(conn):
        rows = helpdesk_service.queue(conn, limit=limit)
        return {"queue": rows, "count": len(rows)}

    return _with_db(run)


@router.get("/cases")
def list_cases(
    status: Optional[str] = None,
    trading_partner_id: Optional[str] = None,
    submitter_id: Optional[str] = None,
    claim_id: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    def run(conn):
        rows = helpdesk_service.list_cases(
            conn,
            status=status,
            trading_partner_id=trading_partner_id,
            submitter_id=submitter_id,
            claim_id=claim_id,
            query=query,
            limit=limit,
        )
        return {"cases": rows, "count": len(rows)}

    return _with_db(run)


@router.post("/cases")
def create_case(body: CaseCreate) -> dict[str, Any]:
    def run(conn):
        return helpdesk_service.create_case(conn, **body.model_dump())

    return _with_db(run)


@router.get("/cases/{case_id}")
def case_detail(case_id: str) -> dict[str, Any]:
    def run(conn):
        detail = helpdesk_service.get_case(conn, case_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="case not found")
        return detail

    return _with_db(run)


@router.post("/cases/{case_id}/status")
def update_status(case_id: str, body: StatusUpdate) -> dict[str, Any]:
    def run(conn):
        result = helpdesk_service.transition_status(
            conn, case_id=case_id, target=body.status, actor=body.actor, note=body.note
        )
        if not result.get("updated"):
            raise HTTPException(status_code=404, detail="case not found")
        return result

    return _with_db(run)


@router.post("/cases/{case_id}/note")
def add_note(case_id: str, body: NoteRequest) -> dict[str, Any]:
    def run(conn):
        result = helpdesk_service.add_note(conn, case_id=case_id, note=body.note, actor=body.actor)
        if not result.get("updated"):
            raise HTTPException(status_code=404, detail="case not found")
        return result

    return _with_db(run)


@router.post("/cases/{case_id}/notify")
def notify_submitter(case_id: str, body: NotifyRequest | None = None) -> dict[str, Any]:
    def run(conn):
        result = helpdesk_service.notify_submitter(
            conn,
            case_id=case_id,
            actor=(body.actor if body else None),
            extra_items=(body.extra_items if body else None),
        )
        if not result.get("updated"):
            raise HTTPException(status_code=404, detail="case not found")
        return result

    return _with_db(run)


@router.post("/resubmission")
def resubmission(body: ResubmissionRequest) -> dict[str, Any]:
    def run(conn):
        return helpdesk_service.apply_resubmission(
            conn,
            resubmission_claim_id=body.resubmission_claim_id,
            original_claim_id=body.original_claim_id,
            case_id=body.case_id,
            passed=body.passed,
            actor=body.actor,
        )

    return _with_db(run)


def register(app: FastAPI, get_db_func: Optional[Callable[[], Any]] = None) -> None:
    global GET_DB
    GET_DB = get_db_func
    app.include_router(router)
