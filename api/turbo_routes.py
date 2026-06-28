"""TurboHEDI HTTP API surface — validation, scrubbing, FHIR, swarms.

Mounts a single FastAPI :class:`APIRouter` exposing the engines built in
Phases 1-5. Drop one line into ``api/app.py``::

    from .turbo_routes import register as register_turbo_routes
    register_turbo_routes(app)

and these endpoints become available under ``/turbo``.
"""
from __future__ import annotations

import os
import sys
from typing import Any

# Make ``worker_py`` importable when this module is loaded by ``uvicorn
# api.app:app`` from the repository root.
_WORKER_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "worker_py")
)
if _WORKER_DIR not in sys.path:
    sys.path.insert(0, _WORKER_DIR)

from fastapi import APIRouter, FastAPI, HTTPException, Response  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from fhir import fhir_claim_to_837  # noqa: E402  (after path mutation)
from turbo_pipeline import run_pipeline  # noqa: E402
from validation import validate_document  # noqa: E402
from reporting import (  # noqa: E402
    build_report,
    position_map_from_flatfile,
    position_map_from_x12,
)

router = APIRouter(prefix="/turbo", tags=["turbohedi"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class X12Body(BaseModel):
    """Plain X12 text payload."""

    x12: str = Field(..., description="Raw X12 transaction text (ISA..IEA).")


class PipelineRequest(X12Body):
    """X12 payload + per-phase pipeline switches."""

    scrub: bool = True
    to_fhir: bool = False
    generate_acks: bool = False
    # Per-partner SNIP severity policy (Workstream 7). Either a built-in policy
    # name (e.g. "edig-parity-v1") or null for strict validation.
    snip_policy: str | None = None
    # Per-partner acknowledgement profile: "999_only" (default) or
    # "999_plus_277CA" to opt in to the 277CA generator.
    ack_profile: str = "999_only"


class ValidateRequest(X12Body):
    """X12 payload + optional per-partner SNIP severity policy."""

    snip_policy: str | None = None


class FlatFilePosition(BaseModel):
    """One flat-file claim's ingest position + identity."""

    claim_id: str
    position: int | None = None
    patient_control_number: str | None = None
    offset: int | None = None
    claimtrace_id: str | None = None


class RejectionReportRequest(X12Body):
    """X12 payload + (optional) flat-file position map for a rejection report."""

    snip_policy: str | None = None
    # Optional ingest position records. When omitted, the claim ordinal within
    # the 837 is used as the flat-file position (claim order mirrors the file).
    positions: list[FlatFilePosition] | None = None
    source: str = "<flatfile>"


class FhirClaimRequest(BaseModel):
    """A FHIR R4 Claim resource (or Bundle containing one)."""

    resource: dict[str, Any] = Field(
        ..., description="FHIR R4 Claim resource or Bundle wrapping one."
    )
    sender_id: str = "TURBOHEDI"
    receiver_id: str = "RECEIVER"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/capability")
def capability() -> dict[str, Any]:
    """Lightweight capability statement — what this server can do."""
    return {
        "resourceType": "CapabilityStatement",
        "status": "active",
        "kind": "instance",
        "software": {"name": "TurboHEDI", "version": "0.3"},
        "format": ["application/json", "application/fhir+json"],
        "transactionSetsSupported": [
            "837P (005010X222A1)",
            "837I (005010X223A2)",
            "837D (005010X224A2)",
            "835  (005010X221A1)",
            "270/271 (005010X279A1)",
            "276/277 (005010X212)",
            "278  (005010X217)",
        ],
        "fhirResourcesSupported": [
            "Patient", "Practitioner", "Organization", "Coverage",
            "Claim", "ExplanationOfBenefit",
            "CoverageEligibilityRequest", "CoverageEligibilityResponse",
            "Bundle",
        ],
        "snipLevelsValidated": list(range(1, 8)),
        "implementationGuides": [
            "ASC X12N 005010X222A1 (837P)",
            "ASC X12N 005010X223A2 (837I)",
            "ASC X12N 005010X224A2 (837D)",
            "ASC X12N 005010X221A1 (835)",
            "ASC X12N 005010X279A1 (270/271)",
            "ASC X12N 005010X212 (276/277)",
            "ASC X12N 005010X217 (278)",
            "ASC X12N 005010X214 (277CA)",
            "ASC X12N 005010X231A1 (999)",
            "CARIN BB (consumer claims)",
            "Da Vinci PAS (prior authorization)",
        ],
    }


@router.post("/validate")
def validate(body: ValidateRequest) -> dict[str, Any]:
    """Run SNIP 1-7 validation over an X12 payload.

    Accepts an optional ``snip_policy`` (a built-in policy name such as
    ``edig-parity-v1``) which applies the per-partner SNIP severity toggles to
    the report so accept/reject mirrors the partner's configured leniency.
    """
    if not body.x12.strip():
        raise HTTPException(status_code=400, detail="x12 body is empty")
    return validate_document(body.x12, policy=body.snip_policy).to_dict()


@router.post("/pipeline")
def pipeline(body: PipelineRequest) -> dict[str, Any]:
    """Run validate -> scrub -> (FHIR) -> (acks) in one call."""
    if not body.x12.strip():
        raise HTTPException(status_code=400, detail="x12 body is empty")
    result = run_pipeline(
        body.x12,
        scrub=body.scrub,
        to_fhir=body.to_fhir,
        generate_acks=body.generate_acks,
        policy=body.snip_policy,
        ack_profile=body.ack_profile,
    )
    return result.to_dict()


@router.post("/rejection-report")
def rejection_report(body: RejectionReportRequest, fmt: str = "json") -> Any:
    """Build a flat-file human-readable rejection report from an X12 payload.

    ``fmt`` selects the rendering: ``json`` (position-keyed, what mainframe
    automation consumes — the default), ``csv`` (one row per error), or ``pdf``.
    Triage never edits the flat file — this only *detects and reports*.
    """
    if not body.x12.strip():
        raise HTTPException(status_code=400, detail="x12 body is empty")

    if body.positions:
        position_map = position_map_from_flatfile(
            [p.model_dump(mode="json") for p in body.positions]
        )
    else:
        position_map = position_map_from_x12(body.x12)

    report = validate_document(body.x12, policy=body.snip_policy)
    rejection = build_report(position_map, report, source=body.source)

    fmt = (fmt or "json").lower()
    if fmt == "csv":
        return Response(content=rejection.to_csv(), media_type="text/csv")
    if fmt == "pdf":
        try:
            pdf = rejection.render_pdf()
        except RuntimeError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return Response(content=pdf, media_type="application/pdf")
    if fmt != "json":
        raise HTTPException(status_code=400, detail="fmt must be json, csv or pdf")
    payload = rejection.to_json_dict()
    payload["claimtrace_event"] = rejection.to_claimtrace_event()
    return payload


@router.post("/fhir/from-x12")
def fhir_from_x12(body: X12Body) -> dict[str, Any]:
    """Validate the X12, then return a FHIR Bundle (837 -> Claim, 835 -> EOB)."""
    result = run_pipeline(body.x12, scrub=False, to_fhir=True)
    if result.fhir is None:
        raise HTTPException(
            status_code=415,
            detail=(
                f"FHIR mapping is not yet supported for transaction set "
                f"{result.transaction_set or 'unknown'}."
            ),
        )
    return {"validation": result.validation, "bundle": result.fhir}


@router.post("/fhir/Claim/$submit")
def fhir_claim_submit(body: FhirClaimRequest) -> dict[str, Any]:
    """Accept a FHIR Claim, convert to 837 and validate.

    Returns the generated X12 text plus the validation report so a FHIR-first
    client can confirm the round-trip is conformant before transmitting.
    """
    try:
        x12 = fhir_claim_to_837(
            body.resource,
            sender_id=body.sender_id,
            receiver_id=body.receiver_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    report = validate_document(x12)
    return {"x12": x12, "validation": report.to_dict()}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(app: FastAPI) -> None:
    """Mount the TurboHEDI router on ``app``."""
    app.include_router(router)
