"""FastAPI routes for normalized trading-partner management (Workstream 6).

Exposes idempotent CRUD over the WS6 partner model. Mounted under
``/v1/partners`` so it sits alongside (and does not disturb) the legacy
``/partners`` partner-config endpoints in ``api/app.py``.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import sys
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

# Make ``worker_py`` importable for the SNIP policy resolver.
_WORKER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "worker_py"))
if _WORKER_DIR not in sys.path:
    sys.path.insert(0, _WORKER_DIR)

try:
    from . import partners_service
except ImportError:  # direct script/test invocation
    import partners_service  # type: ignore

router = APIRouter(prefix="/v1/partners", tags=["partners"])
GET_DB: Optional[Callable[[], Any]] = None
logger = logging.getLogger("api.partners")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class IdentifierModel(BaseModel):
    interchange_qualifier: str
    interchange_id: str
    application_id: Optional[str] = None
    direction: str = Field("sender", pattern="^(sender|receiver)$")
    usage: str = Field("P", pattern="^(P|T)$")


class TransactionModel(BaseModel):
    transaction_type: str
    direction: str = Field("inbound", pattern="^(inbound|outbound)$")
    enabled: bool = True


class SnipPolicyModel(BaseModel):
    snip_type: int = Field(..., ge=1, le=7)
    severity: str = Field(..., pattern="^(enforce-reject|warn|off)$")
    transaction_type: str = "*"
    policy_name: str = "edig-parity-v1"
    effective_from: Optional[_dt.date] = None
    effective_to: Optional[_dt.date] = None


class AckProfileModel(BaseModel):
    ack_profile: str = Field("999_only", pattern="^(999_only|999_plus_277CA)$")
    effective_from: Optional[_dt.date] = None
    effective_to: Optional[_dt.date] = None


class ContactModel(BaseModel):
    contact_type: str = Field("technical", pattern="^(technical|billing|administrative|enrollment)$")
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class AgreementModel(BaseModel):
    agreement_version: str
    status: str = Field("pending", pattern="^(pending|active|terminated)$")
    signed_date: Optional[_dt.date] = None
    effective_from: Optional[_dt.date] = None
    effective_to: Optional[_dt.date] = None
    document_ref: Optional[str] = None


class PartnerUpsert(BaseModel):
    external_id: str
    name: Optional[str] = None
    status: str = Field("active", pattern="^(active|inactive|suspended|test)$")
    notes: Optional[str] = None
    identifiers: Optional[list[IdentifierModel]] = None
    transactions: Optional[list[TransactionModel]] = None
    snip_policies: Optional[list[SnipPolicyModel]] = None
    ack_profiles: Optional[list[AckProfileModel]] = None
    contacts: Optional[list[ContactModel]] = None
    agreements: Optional[list[AgreementModel]] = None


# ---------------------------------------------------------------------------
# DB plumbing
# ---------------------------------------------------------------------------

def _with_db(callback):
    if GET_DB is None:
        raise HTTPException(status_code=503, detail="Partner database is not configured")
    try:
        with GET_DB() as conn:
            return callback(conn)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Partner database error")
        raise HTTPException(status_code=500, detail=f"Partner database error: {exc}") from exc


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.put("")
@router.post("")
def upsert_partner(payload: PartnerUpsert) -> dict[str, Any]:
    data = payload.model_dump(mode="json", exclude_none=False)

    def run(conn):
        return partners_service.upsert_partner(conn, data)

    return _with_db(run)


@router.get("")
def list_partners() -> dict[str, Any]:
    def run(conn):
        return {"partners": partners_service.list_partners(conn)}

    return _with_db(run)


@router.get("/{external_id}")
def get_partner(external_id: str) -> dict[str, Any]:
    def run(conn):
        partner = partners_service.get_partner(conn, external_id)
        if partner is None:
            raise HTTPException(status_code=404, detail="partner not found")
        return partner

    return _with_db(run)


@router.delete("/{external_id}")
def delete_partner(external_id: str) -> dict[str, Any]:
    def run(conn):
        deleted = partners_service.delete_partner(conn, external_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="partner not found")
        return {"deleted": True, "external_id": external_id}

    return _with_db(run)


@router.get("/{external_id}/snip-policy")
def resolve_snip_policy(
    external_id: str,
    as_of: Optional[_dt.date] = Query(None),
    transaction_type: Optional[str] = Query(None),
    base: str = Query("edig-parity-v1"),
) -> dict[str, Any]:
    """Resolve the partner's effective SNIP severity policy as a serializable map."""
    from validation.policy import policy_from_rows  # type: ignore

    def run(conn):
        partner = partners_service.get_partner(conn, external_id)
        if partner is None:
            raise HTTPException(status_code=404, detail="partner not found")
        rows = partners_service.snip_policy_rows(conn, external_id, as_of=as_of)
        snip_policy = policy_from_rows(base, rows, as_of=as_of, base=None)
        return {
            "external_id": external_id,
            "policy_name": snip_policy.name,
            "as_of": (as_of or _dt.date.today()).isoformat(),
            "transaction_type": transaction_type,
            "default_modes": {str(k): v.value for k, v in snip_policy.default_modes.items()},
            "overrides": {
                f"{tx}:{snip}": mode.value
                for (tx, snip), mode in snip_policy.overrides.items()
            },
            "rows": rows,
        }

    return _with_db(run)


@router.get("/{external_id}/ack-profile")
def resolve_ack_profile(
    external_id: str, as_of: Optional[_dt.date] = Query(None)
) -> dict[str, Any]:
    def run(conn):
        partner = partners_service.get_partner(conn, external_id)
        if partner is None:
            raise HTTPException(status_code=404, detail="partner not found")
        profile = partners_service.resolve_ack_profile(conn, external_id, as_of=as_of)
        return {"external_id": external_id, "ack_profile": profile}

    return _with_db(run)


def register(app, get_db_func: Optional[Callable[[], Any]] = None) -> None:
    global GET_DB
    GET_DB = get_db_func
    app.include_router(router)
