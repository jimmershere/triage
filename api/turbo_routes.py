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

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from fhir import fhir_claim_to_837  # noqa: E402  (after path mutation)
from turbo_pipeline import run_pipeline  # noqa: E402
from validation import validate_document  # noqa: E402

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
def validate(body: X12Body) -> dict[str, Any]:
    """Run SNIP 1-7 validation over an X12 payload."""
    if not body.x12.strip():
        raise HTTPException(status_code=400, detail="x12 body is empty")
    return validate_document(body.x12).to_dict()


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
    )
    return result.to_dict()


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
