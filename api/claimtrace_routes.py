"""FastAPI routes exposing Claimtrace functions to the Triage website."""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from claimtrace.common.canonical import line_items_signature
from claimtrace.common.hashing import hash_payload
from claimtrace.correlation.segments import extract_trace, stamp_trace
from claimtrace.correlation.transport import TraceContext, to_headers
from claimtrace.identity.ids import ClaimKey, derive_bundle_id, derive_claim_id
from claimtrace.journal.store import ClaimEvent, InMemoryJournalStore
from claimtrace.journal.trace_api import get_trace, reconstruct
from claimtrace.lineage.projector import InMemoryGraphSink, LineageProjector
from claimtrace.lineage.queries import claims_for_payment, lineage_835_to_837
from claimtrace.merkle.tree import build_batch_mapping, build_merkle_tree, get_proof_path, merkle_root, verify_proof

try:
    from . import claimtrace_service
except ImportError:  # direct script/test invocation
    import claimtrace_service  # type: ignore


router = APIRouter(prefix="/claimtrace", tags=["claimtrace"])
DEMO_STORE = InMemoryJournalStore()
GET_DB: Optional[Callable[[], Any]] = None
logger = logging.getLogger("api.claimtrace")


class LineItemRequest(BaseModel):
    proc_code: str
    dos: _dt.date
    units: int = 1
    charge_amount_cents: int = 0


class IdentityRequest(BaseModel):
    submitter_id: str
    subscriber_id: str
    patient_dob: _dt.date
    dos_start: _dt.date
    charge_amount_cents: int
    payer_id: str
    line_items: list[LineItemRequest] = Field(default_factory=list)


class BundleRequest(BaseModel):
    claim_ids: list[str]


class StampRequest(BaseModel):
    x12_text: str
    trace_context: TraceContext


class JournalAppendRequest(BaseModel):
    claim_id: str
    bundle_id: Optional[str] = None
    prior_state_hash: Optional[str] = None
    payload: str = "{}"
    payload_location: str = "claimtrace://ui/payload"
    operation_type: str
    service_name: str = "claimtrace-ui"
    correlation_ids: dict[str, Any] = Field(default_factory=dict)


class MerkleRequest(BaseModel):
    claim_hashes: dict[str, str]


class ClaimActionRequest(BaseModel):
    action: str
    note: Optional[str] = None


def _with_db(callback):
    if GET_DB is None:
        raise HTTPException(status_code=503, detail="Claimtrace database is not configured")
    try:
        with GET_DB() as conn:
            return callback(conn)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Claimtrace database error")
        raise HTTPException(status_code=500, detail=f"Claimtrace database error: {exc}") from exc


@router.get("/summary")
def summary() -> dict[str, Any]:
    if GET_DB is not None:
        return _with_db(claimtrace_service.summary)
    events = DEMO_STORE.events()
    claims = {event.claim_id for event in events}
    bundles = {event.bundle_id for event in events if event.bundle_id}
    return {
        "enabled": True,
        "components": ["identity", "correlation", "journal", "merkle", "lineage"],
        "events": len(events),
        "claims": len(claims),
        "distinct_claims": len(claims),
        "bundles": len(bundles),
        "repair_count": 0,
        "delete_count": 0,
        "append_only": True,
    }


@router.get("/claims")
def search_claims(
    query: Optional[str] = None,
    trading_partner_id: Optional[str] = None,
    submitter_id: Optional[str] = None,
    claim_id: Optional[str] = None,
    claim_hash_id: Optional[str] = None,
    action_state: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    def run(conn):
        claims = claimtrace_service.search_claims(
            conn,
            query=query,
            trading_partner_id=trading_partner_id,
            submitter_id=submitter_id,
            claim_id=claim_id,
            claim_hash_id=claim_hash_id,
            action_state=action_state,
            limit=limit,
        )
        return {"claims": claims, "count": len(claims)}

    return _with_db(run)


@router.get("/claims/{claim_key}")
def claim_detail(claim_key: str) -> dict[str, Any]:
    def run(conn):
        detail = claimtrace_service.claim_detail(conn, claim_key)
        if detail is None:
            raise HTTPException(status_code=404, detail="claim not found")
        return detail

    return _with_db(run)


@router.post("/claims/{claim_key}/action")
def claim_action(claim_key: str, payload: ClaimActionRequest) -> dict[str, Any]:
    def run(conn):
        result = claimtrace_service.mark_claim(conn, claim_id=claim_key, action=payload.action, note=payload.note)
        if not result.get("updated"):
            raise HTTPException(status_code=404, detail="claim not found")
        return result

    return _with_db(run)


@router.post("/identity/claim")
def claim_identity(payload: IdentityRequest) -> dict[str, Any]:
    signature = line_items_signature([item.model_dump(mode="json") for item in payload.line_items])
    key = ClaimKey(
        submitter_id=payload.submitter_id,
        subscriber_id=payload.subscriber_id,
        patient_dob=payload.patient_dob,
        dos_start=payload.dos_start,
        charge_amount_cents=payload.charge_amount_cents,
        line_items_signature=signature,
        payer_id=payload.payer_id,
    )
    return {"claim_id": derive_claim_id(key), "line_items_signature": signature, "key": key.model_dump(mode="json")}


@router.post("/identity/bundle")
def bundle_identity(payload: BundleRequest) -> dict[str, str]:
    return {"bundle_id": derive_bundle_id(payload.claim_ids)}


@router.post("/correlation/extract")
def correlation_extract(payload: dict[str, str]) -> dict[str, Any]:
    text = payload.get("x12_text", "")
    if not text:
        raise HTTPException(status_code=400, detail="x12_text is required")
    return extract_trace(text).model_dump(mode="json")


@router.post("/correlation/stamp")
def correlation_stamp(payload: StampRequest) -> dict[str, Any]:
    stamped = stamp_trace(payload.x12_text, payload.trace_context)
    return {
        "x12_text": stamped,
        "headers": to_headers(payload.trace_context),
        "trace_context": extract_trace(stamped).model_dump(mode="json"),
    }


@router.post("/journal/events")
def append_journal_event(payload: JournalAppendRequest) -> dict[str, Any]:
    event = ClaimEvent(
        claim_id=payload.claim_id,
        bundle_id=payload.bundle_id,
        prior_state_hash=payload.prior_state_hash,
        new_state_hash=hash_payload(payload.payload.encode("utf-8")),
        payload_location=payload.payload_location,
        operation_type=payload.operation_type,  # type: ignore[arg-type]
        service_name=payload.service_name,
        correlation_ids=payload.correlation_ids,
    )
    DEMO_STORE.append_event(event)
    return event.model_dump(mode="json")


@router.get("/journal/trace")
def journal_trace(claim_id: Optional[str] = None, bundle_id: Optional[str] = None) -> dict[str, Any]:
    events = get_trace(DEMO_STORE, claim_id=claim_id, bundle_id=bundle_id)
    return {
        "events": [event.model_dump(mode="json") for event in events],
        "timeline": reconstruct(DEMO_STORE, claim_id) if claim_id else [],
    }


@router.post("/merkle/batch")
def merkle_batch(payload: MerkleRequest) -> dict[str, Any]:
    mapping = build_batch_mapping(payload.claim_hashes)
    ordered = sorted(payload.claim_hashes)
    tree = build_merkle_tree([payload.claim_hashes[claim_id] for claim_id in ordered])
    proofs = {
        claim_id: {
            "proof": get_proof_path(tree, index),
            "valid": verify_proof(payload.claim_hashes[claim_id], get_proof_path(tree, index), merkle_root(tree)),
        }
        for index, claim_id in enumerate(ordered)
    }
    return {**mapping, "proofs": proofs}


@router.get("/lineage/payment/{payment_id}")
def lineage_payment(payment_id: str) -> dict[str, Any]:
    sink = InMemoryGraphSink()
    LineageProjector(sink).project(DEMO_STORE.events())
    return {"payment_id": payment_id, "claim_ids": sorted(claims_for_payment(payment_id, sink))}


@router.get("/lineage/835/{trn}")
def lineage_835(trn: str) -> dict[str, Any]:
    sink = InMemoryGraphSink()
    LineageProjector(sink).project(DEMO_STORE.events())
    return {"trn": trn, "origin_claim_ids": sorted(lineage_835_to_837(trn, sink))}


def register(app, get_db_func: Optional[Callable[[], Any]] = None) -> None:
    global GET_DB
    GET_DB = get_db_func
    app.include_router(router)
