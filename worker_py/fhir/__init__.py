"""TurboHEDI FHIR R4 capability.

This package implements:

- :mod:`fhir.model`     — FHIR datatype helpers (CodeableConcept, Reference, ...)
- :mod:`fhir.resources` — builders for the resources we exchange
  (Patient, Practitioner, Organization, Coverage, Claim, ClaimResponse,
  ExplanationOfBenefit, CoverageEligibilityRequest/Response, Task, Bundle)
- :mod:`fhir.mappers`   — bidirectional X12 <-> FHIR mappers
  (837 <-> FHIR Claim; 835 -> ExplanationOfBenefit / ClaimResponse;
   270/271 <-> FHIR CoverageEligibility; 278 -> FHIR Claim preauthorization).

Resources are plain JSON-shaped dicts conformant to FHIR R4. The CARIN Consumer
Directed Payment Initiative (CARIN BB) and Da Vinci PAS implementation guides
are referenced via ``meta.profile`` on the appropriate resources.
"""
from __future__ import annotations

from .mappers import (
    eligibility_request_to_fhir,
    eligibility_response_to_fhir,
    fhir_claim_to_837,
    pas_claim_to_278_request,
    remittance_to_fhir,
    submission_to_fhir,
    x12_278_request_to_pas_bundle,
    x12_278_response_to_claim_response,
)
from .pas_resources import (
    build_pas_attachment_bundle,
    build_pas_claim,
    build_pas_claim_response,
    build_pas_request_bundle,
)
from .resources import (
    build_bundle,
    build_claim,
    build_coverage,
    build_coverage_eligibility_request,
    build_coverage_eligibility_response,
    build_explanation_of_benefit,
    build_organization,
    build_patient,
    build_practitioner,
)

__all__ = [
    "build_bundle",
    "build_claim",
    "build_coverage",
    "build_coverage_eligibility_request",
    "build_coverage_eligibility_response",
    "build_explanation_of_benefit",
    "build_organization",
    "build_pas_attachment_bundle",
    "build_pas_claim",
    "build_pas_claim_response",
    "build_pas_request_bundle",
    "build_patient",
    "build_practitioner",
    "eligibility_request_to_fhir",
    "eligibility_response_to_fhir",
    "fhir_claim_to_837",
    "pas_claim_to_278_request",
    "remittance_to_fhir",
    "submission_to_fhir",
    "x12_278_request_to_pas_bundle",
    "x12_278_response_to_claim_response",
]
