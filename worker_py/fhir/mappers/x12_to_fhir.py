"""X12 -> FHIR R4 mappers.

These translate parsed HIPAA X12 transactions into FHIR R4 resources, returning
a transaction-style :class:`Bundle` that can be POSTed to a FHIR server.

Supported transactions:

- **837** (Professional / Institutional / Dental) -> ``Bundle`` of
  ``Patient``, ``Organization`` (payer + billing provider), ``Practitioner``
  (rendering provider), ``Claim``.
- **835** -> ``Bundle`` of ``Patient`` + ``ExplanationOfBenefit`` per claim
  (CARIN Blue Button profiled).
- **270** -> ``CoverageEligibilityRequest``.
- **271** -> ``CoverageEligibilityResponse``.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from validation.engine import validate_document
from validation.model import ClaimProjection
from validation.parser import Segment, parse

from ..model import CPT, DAVINCI_PAS_CLAIM, codeable_concept, coding, gender_from_x12
from ..resources import (
    build_bundle,
    build_claim,
    build_coverage_eligibility_request,
    build_coverage_eligibility_response,
    build_explanation_of_benefit,
    build_organization,
    build_patient,
    build_practitioner,
)


# ---------------------------------------------------------------------------
# 837 submission -> Bundle of Patient + Org + Practitioner + Claim
# ---------------------------------------------------------------------------

def _variant_from_version(version: str | None) -> str:
    v = version or ""
    if "X223" in v:
        return "I"
    if "X224" in v:
        return "D"
    return "P"


def _slug(value: str | None, fallback: str) -> str:
    safe = "".join(ch for ch in (value or "") if ch.isalnum())
    return safe or fallback


def submission_to_fhir(text: str, *, bundle_type: str = "transaction") -> dict[str, Any]:
    """Map an X12 837 submission into a FHIR Bundle.

    Each claim becomes a ``Claim`` resource accompanied by its ``Patient``,
    billing-provider ``Organization``, rendering-provider ``Practitioner``
    (when present) and payer ``Organization``. Within the Bundle, resources
    are deduplicated by stable id.
    """
    report = validate_document(text)
    variant = _variant_from_version(report.implementation_version)

    resources: dict[str, dict[str, Any]] = {}

    def add(resource: dict[str, Any]) -> str:
        key = f"{resource['resourceType']}/{resource['id']}"
        resources.setdefault(key, resource)
        return key

    for idx, claim in enumerate(report.claims, start=1):
        claim_slug = _slug(claim.claim_id, f"claim{idx}")
        patient_id = f"patient-{claim_slug}"
        patient = build_patient(
            id=patient_id,
            member_id=claim.subscriber_id,
            family=claim.patient_last_name,
            given=claim.patient_first_name,
            gender=claim.patient_gender,
            birth_date=claim.patient_dob,
        )
        patient_ref = add(patient)

        payer_id = f"payer-{_slug(claim.payer_id or claim.payer_name, 'unknown')}"
        payer = build_organization(
            id=payer_id, name=claim.payer_name, type_code="pay",
        )
        payer_ref = add(payer)

        billing_id = f"org-{_slug(claim.billing_provider_npi or claim.billing_provider_name, 'billing')}"
        billing = build_organization(
            id=billing_id,
            npi=claim.billing_provider_npi,
            tax_id=claim.billing_provider_tax_id,
            name=claim.billing_provider_name,
            type_code="prov",
        )
        billing_ref = add(billing)

        if claim.rendering_provider_npi:
            rendering_id = f"pract-{_slug(claim.rendering_provider_npi, 'rendering')}"
            add(build_practitioner(id=rendering_id, npi=claim.rendering_provider_npi))

        items = [
            {
                "sequence": int(line.line_no) if line.line_no and str(line.line_no).isdigit() else i,
                "code": line.procedure_code,
                "system": CPT,
                "charge": line.charge_amount,
                "units": line.units,
                "service_date": line.service_date,
                "place_of_service": line.place_of_service or claim.place_of_service,
                "modifiers": line.modifiers,
                "diagnosis_pointers": line.diagnosis_pointers,
            }
            for i, line in enumerate(claim.service_lines, start=1)
            if line.procedure_code
        ]

        claim_resource = build_claim(
            id=f"claim-{claim_slug}",
            variant=variant,
            patient_ref=patient_ref,
            insurer_ref=payer_ref,
            provider_ref=billing_ref,
            diagnosis_codes=claim.diagnosis_codes,
            items=items,
            total_charge=claim.total_charge,
            billable_period_start=claim.statement_from_date,
            billable_period_end=claim.statement_to_date,
            coverage_display=claim.payer_name,
            place_of_service=claim.place_of_service,
            type_of_bill=claim.type_of_bill,
        )
        add(claim_resource)

    return build_bundle(list(resources.values()), bundle_type=bundle_type)


# ---------------------------------------------------------------------------
# 835 remittance -> Bundle of ExplanationOfBenefit
# ---------------------------------------------------------------------------

def _dec(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def _extract_remittance(text: str) -> list[dict[str, Any]]:
    """Walk a parsed 835 and project claim/service payment records.

    Returns a list of dicts shaped for :func:`build_explanation_of_benefit`.
    """
    doc = parse(text)
    payer_name: str | None = None
    payer_id: str | None = None
    payee_name: str | None = None
    payee_npi: str | None = None
    payment_date: str | None = None
    payment_amount: str | None = None

    claims: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_service: dict[str, Any] | None = None
    patient_name: tuple[str | None, str | None] = (None, None)
    patient_id: str | None = None

    for txn in doc.transactions:
        if txn.set_code != "835":
            continue
        for seg in txn.segments:
            tag = seg.seg_id
            if tag == "BPR":
                payment_amount = seg.elem(2) or payment_amount
            elif tag == "DTM" and seg.elem(1) == "405":
                payment_date = seg.elem(2) or payment_date
            elif tag == "N1":
                qualifier = seg.elem(1)
                name = seg.elem(2)
                identifier_qualifier = seg.elem(3)
                identifier_value = seg.elem(4)
                if qualifier == "PR":
                    payer_name = name
                    if identifier_qualifier and identifier_value:
                        payer_id = identifier_value
                elif qualifier == "PE":
                    payee_name = name
                    if identifier_qualifier == "XX":
                        payee_npi = identifier_value
            elif tag == "CLP":
                if current is not None:
                    if current_service:
                        current.setdefault("lines", []).append(current_service)
                        current_service = None
                    claims.append(current)
                current = {
                    "claim_id": seg.elem(1) or None,
                    "status": seg.elem(2) or None,
                    "total_charge": seg.elem(3) or None,
                    "paid": seg.elem(4) or None,
                    "patient_resp": seg.elem(5) or None,
                    "filing_indicator": seg.elem(6) or None,
                    "payer_claim_control": seg.elem(7) or None,
                    "facility_code": seg.elem(8) or None,
                    "claim_frequency": seg.elem(9) or None,
                    "patient_last": None,
                    "patient_first": None,
                    "patient_id": None,
                    "lines": [],
                }
                patient_name = (None, None)
                patient_id = None
            elif tag == "NM1" and seg.elem(1) == "QC" and current is not None:
                current["patient_last"] = seg.elem(3) or None
                current["patient_first"] = seg.elem(4) or None
                current["patient_id"] = seg.elem(9) or None
            elif tag == "SVC" and current is not None:
                if current_service:
                    current["lines"].append(current_service)
                comp_proc = seg.comp(1, 2) or seg.elem(1)
                current_service = {
                    "code": comp_proc,
                    "charge": seg.elem(2) or None,
                    "paid": seg.elem(3) or None,
                    "revenue_code": seg.elem(4) or None,
                    "units": seg.elem(5) or None,
                }

        if current is not None:
            if current_service:
                current.setdefault("lines", []).append(current_service)
                current_service = None
            claims.append(current)
            current = None

    return [
        {
            "claim": c,
            "payer_name": payer_name,
            "payer_id": payer_id,
            "payee_name": payee_name,
            "payee_npi": payee_npi,
            "payment_date": payment_date,
            "payment_amount": payment_amount,
        }
        for c in claims
    ]


def remittance_to_fhir(text: str, *, bundle_type: str = "collection") -> dict[str, Any]:
    """Map an X12 835 remittance into a Bundle of ExplanationOfBenefit resources."""
    records = _extract_remittance(text)
    resources: list[dict[str, Any]] = []
    seen_org: set[str] = set()

    for record in records:
        claim = record["claim"]
        claim_slug = _slug(claim.get("claim_id"), f"eob{len(resources)+1}")

        patient_id = f"patient-{claim_slug}"
        patient = build_patient(
            id=patient_id,
            member_id=claim.get("patient_id"),
            family=claim.get("patient_last"),
            given=claim.get("patient_first"),
        )
        resources.append(patient)

        payer_id = f"payer-{_slug(record.get('payer_id') or record.get('payer_name'), 'unknown')}"
        if payer_id not in seen_org:
            resources.append(build_organization(
                id=payer_id, name=record.get("payer_name"), type_code="pay",
            ))
            seen_org.add(payer_id)

        provider_id = f"org-{_slug(record.get('payee_npi') or record.get('payee_name'), 'payee')}"
        if provider_id not in seen_org:
            resources.append(build_organization(
                id=provider_id,
                npi=record.get("payee_npi"),
                name=record.get("payee_name"),
                type_code="prov",
            ))
            seen_org.add(provider_id)

        items = [
            {
                "sequence": i,
                "code": line.get("code") or "UNKNOWN",
                "charge": line.get("charge"),
                "paid": line.get("paid"),
            }
            for i, line in enumerate(claim.get("lines", []), start=1)
        ]

        totals = []
        if claim.get("total_charge"):
            totals.append({"category": "submitted", "amount": claim.get("total_charge")})
        if claim.get("paid"):
            totals.append({"category": "benefit", "amount": claim.get("paid")})
        if claim.get("patient_resp"):
            totals.append({"category": "patientpayoutstanding", "amount": claim.get("patient_resp")})

        eob = build_explanation_of_benefit(
            id=f"eob-{claim_slug}",
            patient_ref=f"Patient/{patient_id}",
            insurer_ref=f"Organization/{payer_id}",
            provider_ref=f"Organization/{provider_id}",
            items=items,
            totals=totals,
            payment_amount=claim.get("paid"),
            payment_date=record.get("payment_date"),
        )
        resources.append(eob)

    return build_bundle(resources, bundle_type=bundle_type)


# ---------------------------------------------------------------------------
# 270/271 eligibility
# ---------------------------------------------------------------------------

def _eligibility_context(text: str) -> dict[str, Any]:
    doc = parse(text)
    ctx: dict[str, Any] = {
        "payer_name": None,
        "payer_id": None,
        "provider_name": None,
        "provider_npi": None,
        "subscriber_last": None,
        "subscriber_first": None,
        "subscriber_id": None,
        "service_types": [],
        "benefits": [],
        "service_date": None,
    }
    for txn in doc.transactions:
        if txn.set_code not in ("270", "271"):
            continue
        for seg in txn.segments:
            tag = seg.seg_id
            if tag == "NM1":
                entity = seg.elem(1)
                if entity == "PR":
                    ctx["payer_name"] = seg.elem(3)
                    if seg.elem(8) == "PI":
                        ctx["payer_id"] = seg.elem(9)
                elif entity in ("1P", "41", "85"):
                    ctx["provider_name"] = seg.elem(3)
                    if seg.elem(8) == "XX":
                        ctx["provider_npi"] = seg.elem(9)
                elif entity == "IL":
                    ctx["subscriber_last"] = seg.elem(3)
                    ctx["subscriber_first"] = seg.elem(4)
                    ctx["subscriber_id"] = seg.elem(9)
            elif tag == "DMG":
                ctx["subscriber_dob"] = seg.elem(2)
                ctx["subscriber_gender"] = seg.elem(3)
            elif tag == "DTP" and seg.elem(1) == "291":
                ctx["service_date"] = seg.elem(3)
            elif tag == "EQ" and seg.elem(1):
                ctx["service_types"].append(seg.elem(1))
            elif tag == "EB":
                ctx["benefits"].append({
                    "eligibility": seg.elem(1),
                    "coverage_level": seg.elem(2),
                    "service_type": seg.elem(3),
                    "insurance_type": seg.elem(4),
                })
    return ctx


def eligibility_request_to_fhir(text: str) -> dict[str, Any]:
    """Map an X12 270 eligibility inquiry to a CoverageEligibilityRequest."""
    ctx = _eligibility_context(text)
    patient = build_patient(
        id="patient-1",
        member_id=ctx.get("subscriber_id"),
        family=ctx.get("subscriber_last"),
        given=ctx.get("subscriber_first"),
        gender=ctx.get("subscriber_gender"),
        birth_date=ctx.get("subscriber_dob"),
    )
    payer = build_organization(
        id="payer-1", name=ctx.get("payer_name"), type_code="pay",
    )
    provider = build_organization(
        id="provider-1",
        npi=ctx.get("provider_npi"),
        name=ctx.get("provider_name"),
        type_code="prov",
    )
    request = build_coverage_eligibility_request(
        id="eligibility-1",
        patient_ref="Patient/patient-1",
        insurer_ref="Organization/payer-1",
        provider_ref="Organization/provider-1",
        service_date=ctx.get("service_date"),
        service_types=ctx.get("service_types"),
    )
    return build_bundle([patient, payer, provider, request])


def eligibility_response_to_fhir(text: str) -> dict[str, Any]:
    """Map an X12 271 eligibility response to a CoverageEligibilityResponse."""
    ctx = _eligibility_context(text)
    patient = build_patient(
        id="patient-1",
        member_id=ctx.get("subscriber_id"),
        family=ctx.get("subscriber_last"),
        given=ctx.get("subscriber_first"),
        gender=ctx.get("subscriber_gender"),
        birth_date=ctx.get("subscriber_dob"),
    )
    payer = build_organization(
        id="payer-1", name=ctx.get("payer_name"), type_code="pay",
    )
    response = build_coverage_eligibility_response(
        id="eligibility-response-1",
        request_ref="CoverageEligibilityRequest/eligibility-1",
        patient_ref="Patient/patient-1",
        insurer_ref="Organization/payer-1",
        benefits=ctx.get("benefits"),
    )
    return build_bundle([patient, payer, response])
