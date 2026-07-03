"""FastAPI routes for the 835 restore feature set (Workstream 5).

Mounts an :class:`APIRouter` under ``/era`` mirroring the ``/turbo`` registration
pattern. Surfaces immutable 835 storage, byte-exact re-delivery, balance-verified
reconstruction, and reversal modelling, plus RabbitMQ job enqueue for
``era.redeliver`` / ``era.reconstruct``.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

try:
    from . import era_service
except ImportError:  # direct script/test invocation
    import era_service  # type: ignore

router = APIRouter(prefix="/era", tags=["era-restore"])
GET_DB: Optional[Callable[[], Any]] = None
PUBLISH_JOB: Optional[Callable[[dict[str, Any]], None]] = None
logger = logging.getLogger("api.era")


class StoreRequest(BaseModel):
    x12: str = Field(..., description="Raw 835 transaction text (ISA..IEA).")
    claim_id: Optional[str] = None
    tracking_id: Optional[str] = None
    direction: str = "produced"
    created_by: Optional[str] = None
    correlation_ids: dict[str, Any] = Field(default_factory=dict)


class ActorRequest(BaseModel):
    created_by: Optional[str] = None


class JobRequest(BaseModel):
    job: str = Field(..., description="redeliver | reconstruct")
    artifact_id: str
    created_by: Optional[str] = None


def _with_db(callback):
    if GET_DB is None:
        raise HTTPException(status_code=503, detail="ERA database is not configured")
    try:
        with GET_DB() as conn:
            return callback(conn)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("ERA database error")
        raise HTTPException(status_code=500, detail=f"ERA database error: {exc}") from exc


@router.get("/summary")
def summary() -> dict[str, Any]:
    return _with_db(era_service.summary)


@router.get("/artifacts")
def list_artifacts(
    trn: Optional[str] = None,
    claim_id: Optional[str] = None,
    origin: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    def run(conn):
        rows = era_service.list_artifacts(
            conn, trn=trn, claim_id=claim_id, origin=origin, query=query, limit=limit
        )
        return {"artifacts": rows, "count": len(rows)}

    return _with_db(run)


@router.get("/artifacts/{artifact_id}")
def artifact_detail(artifact_id: str) -> dict[str, Any]:
    def run(conn):
        artifact = era_service.get_artifact(conn, artifact_id, include_raw=True)
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        return artifact

    return _with_db(run)


@router.post("/store")
def store(body: StoreRequest) -> dict[str, Any]:
    if not body.x12.strip():
        raise HTTPException(status_code=400, detail="x12 body is empty")

    def run(conn):
        return era_service.store_835(
            conn,
            content=body.x12,
            tracking_id=body.tracking_id,
            claim_id=body.claim_id,
            direction=body.direction,
            created_by=body.created_by,
            correlation_ids=body.correlation_ids,
        )

    return _with_db(run)


# POST, not GET: redeliver appends an append-only RE_DELIVER_835 Claimtrace
# event and commits. A GET would be replayed by browser prefetch / proxy retry
# and pollute the audit trail with spurious re-delivery events.
@router.post("/artifacts/{artifact_id}/redeliver")
def redeliver(artifact_id: str, actor: Optional[str] = None) -> dict[str, Any]:
    def run(conn):
        result = era_service.redeliver(conn, artifact_id, actor=actor)
        if not result.get("found"):
            raise HTTPException(status_code=404, detail="artifact not found")
        return result

    return _with_db(run)


@router.post("/artifacts/{artifact_id}/reconstruct")
def reconstruct(artifact_id: str, body: ActorRequest | None = None) -> dict[str, Any]:
    def run(conn):
        result = era_service.reconstruct(
            conn, artifact_id, created_by=(body.created_by if body else None)
        )
        if not result.get("found"):
            raise HTTPException(status_code=404, detail="artifact not found")
        return result

    return _with_db(run)


@router.post("/artifacts/{artifact_id}/reverse")
def reverse(artifact_id: str, body: ActorRequest | None = None) -> dict[str, Any]:
    def run(conn):
        result = era_service.reverse(
            conn, artifact_id, created_by=(body.created_by if body else None)
        )
        if not result.get("found"):
            raise HTTPException(status_code=404, detail="artifact not found")
        return result

    return _with_db(run)


@router.post("/jobs")
def enqueue_job(body: JobRequest) -> dict[str, Any]:
    job = body.job.strip().lower()
    if job not in {"redeliver", "reconstruct"}:
        raise HTTPException(status_code=400, detail="job must be redeliver or reconstruct")
    if PUBLISH_JOB is None:
        raise HTTPException(status_code=503, detail="ERA job queue is not configured")
    message = {
        "job": f"era.{job}",
        "artifact_id": body.artifact_id,
        "created_by": body.created_by,
    }
    try:
        PUBLISH_JOB(message)
    except Exception as exc:  # pragma: no cover - broker failure path
        raise HTTPException(status_code=502, detail=f"failed to enqueue job: {exc}") from exc
    return {"enqueued": True, **message}


def register(
    app: FastAPI,
    get_db_func: Optional[Callable[[], Any]] = None,
    publish_func: Optional[Callable[[dict[str, Any]], None]] = None,
) -> None:
    global GET_DB, PUBLISH_JOB
    GET_DB = get_db_func
    PUBLISH_JOB = publish_func
    app.include_router(router)
