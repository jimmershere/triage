"""FHIR R4 resource builders.

Each builder returns a JSON-shaped dict conformant to the named FHIR R4
resource. Where appropriate the ``meta.profile`` field references the CARIN
Blue Button (consumer claims) or Da Vinci PAS (prior authorization) profile.
"""
from __future__ import annotations

from typing import Any

from .model import (
    ADJUDICATION,
    CARIN_EOB_INPATIENT,
    CARIN_EOB_OUTPATIENT,
    CARIN_EOB_PROFESSIONAL,
    CLAIM_TYPE,
    CPT,
    DAVINCI_PAS_CLAIM,
    ICD10_CM,
    NPI,
    PLACE_OF_SERVICE,
    TAX_ID,
    codeable_concept,
    coding,
    fhir_date,
    fhir_dateTime,
    gender_from_x12,
    human_name,
    identifier,
    money,
    now_iso,
    quantity,
    reference,
)

_CLAIM_TYPE_BY_VARIANT = {
    "P": ("professional", "Professional"),
    "I": ("institutional", "Institutional"),
    "D": ("oral", "Oral"),
}


def _drop_empty(resource: dict[str, Any]) -> dict[str, Any]:
    """Strip top-level keys whose value is None or an empty container."""
    return {k: v for k, v in resource.items() if v not in (None, "", [], {})}


# ---------------------------------------------------------------------------
# Patient / Practitioner / Organization / Coverage
# ---------------------------------------------------------------------------

def build_patient(
    *,
    id: str | None = None,
    member_id: str | None = None,
    family: str | None = None,
    given: str | None = None,
    gender: str | None = None,
    birth_date: str | None = None,
) -> dict[str, Any]:
    res: dict[str, Any] = {"resourceType": "Patient"}
    if id:
        res["id"] = id
    ident = identifier(
        member_id,
        system="http://hl7.org/fhir/sid/us-mb",
        type_code="MB",
    )
    if ident:
        res["identifier"] = [ident]
    name = human_name(family=family, given=given)
    if name:
        res["name"] = [name]
    g = gender_from_x12(gender)
    if g:
        res["gender"] = g
    bd = fhir_date(birth_date)
    if bd:
        res["birthDate"] = bd
    return _drop_empty(res)


def build_practitioner(
    *,
    id: str | None = None,
    npi: str | None = None,
    family: str | None = None,
    given: str | None = None,
) -> dict[str, Any]:
    res: dict[str, Any] = {"resourceType": "Practitioner"}
    if id:
        res["id"] = id
    ident = identifier(npi, system=NPI, type_code="NPI")
    if ident:
        res["identifier"] = [ident]
    name = human_name(family=family, given=given)
    if name:
        res["name"] = [name]
    return _drop_empty(res)


def build_organization(
    *,
    id: str | None = None,
    npi: str | None = None,
    tax_id: str | None = None,
    name: str | None = None,
    type_code: str | None = None,
) -> dict[str, Any]:
    res: dict[str, Any] = {"resourceType": "Organization"}
    if id:
        res["id"] = id
    idents = [
        i for i in (
            identifier(npi, system=NPI, type_code="NPI"),
            identifier(tax_id, system=TAX_ID, type_code="TAX"),
        ) if i is not None
    ]
    if idents:
        res["identifier"] = idents
    if name:
        res["name"] = name
    if type_code:
        res["type"] = [codeable_concept(
            [coding("http://terminology.hl7.org/CodeSystem/organization-type", type_code)]
        )]
    return _drop_empty(res)


def build_coverage(
    *,
    id: str | None = None,
    member_id: str | None = None,
    beneficiary_ref: str,
    payor_ref: str,
    relationship: str | None = None,
) -> dict[str, Any]:
    res: dict[str, Any] = {
        "resourceType": "Coverage",
        "status": "active",
        "beneficiary": reference(beneficiary_ref),
        "payor": [reference(payor_ref)],
    }
    if id:
        res["id"] = id
    if member_id:
        res["subscriberId"] = member_id
    if relationship:
        res["relationship"] = codeable_concept(
            [coding(
                "http://terminology.hl7.org/CodeSystem/subscriber-relationship",
                relationship.lower(),
            )]
        )
    return _drop_empty(res)


# ---------------------------------------------------------------------------
# Claim (837P/I/D submission)
# ---------------------------------------------------------------------------

def build_claim(
    *,
    id: str,
    variant: str = "P",
    use: str = "claim",
    patient_ref: str,
    insurer_ref: str,
    provider_ref: str,
    created: str | None = None,
    diagnosis_codes: list[str] | None = None,
    procedure_codes: list[dict[str, Any]] | None = None,
    items: list[dict[str, Any]] | None = None,
    total_charge: str | None = None,
    billable_period_start: str | None = None,
    billable_period_end: str | None = None,
    insurance_focal: bool = True,
    coverage_display: str | None = None,
    place_of_service: str | None = None,
    type_of_bill: str | None = None,
    profile: list[str] | None = None,
) -> dict[str, Any]:
    """Build a FHIR R4 Claim resource.

    ``variant`` is one of ``"P"`` (professional), ``"I"`` (institutional),
    ``"D"`` (oral/dental). Pass ``use="preauthorization"`` for a Da Vinci PAS
    claim and an appropriate ``profile`` list.
    """
    type_code, type_display = _CLAIM_TYPE_BY_VARIANT.get(variant, ("professional", "Professional"))

    res: dict[str, Any] = {
        "resourceType": "Claim",
        "id": id,
        "status": "active",
        "type": codeable_concept(
            [coding(CLAIM_TYPE, type_code, type_display)]
        ),
        "use": use,
        "patient": reference(patient_ref),
        "created": created or now_iso(),
        "insurer": reference(insurer_ref),
        "provider": reference(provider_ref),
        "priority": codeable_concept(
            [coding("http://terminology.hl7.org/CodeSystem/processpriority", "normal")]
        ),
    }
    if profile:
        res["meta"] = {"profile": profile}

    if billable_period_start or billable_period_end:
        period: dict[str, Any] = {}
        if billable_period_start:
            period["start"] = fhir_date(billable_period_start) or billable_period_start
        if billable_period_end:
            period["end"] = fhir_date(billable_period_end) or billable_period_end
        if period:
            res["billablePeriod"] = period

    if diagnosis_codes:
        res["diagnosis"] = [
            {
                "sequence": i,
                "diagnosisCodeableConcept": codeable_concept(
                    [coding(ICD10_CM, dx)]
                ),
            }
            for i, dx in enumerate(diagnosis_codes, start=1)
        ]

    if procedure_codes:
        res["procedure"] = [
            {
                "sequence": p.get("sequence", i),
                "procedureCodeableConcept": codeable_concept(
                    [coding(p.get("system", CPT), p["code"])]
                ),
            }
            for i, p in enumerate(procedure_codes, start=1)
            if p.get("code")
        ]

    res["insurance"] = [{
        "sequence": 1,
        "focal": insurance_focal,
        "coverage": reference("#coverage", display=coverage_display),
    }]

    if items:
        res["item"] = []
        for line in items:
            item: dict[str, Any] = {
                "sequence": int(line.get("sequence", 1)),
                "productOrService": codeable_concept(
                    [coding(line.get("system", CPT), line["code"])]
                ),
            }
            if line.get("service_date"):
                d = fhir_date(line["service_date"])
                if d:
                    item["servicedDate"] = d
            if line.get("place_of_service"):
                item["locationCodeableConcept"] = codeable_concept(
                    [coding(PLACE_OF_SERVICE, line["place_of_service"])]
                )
            elif place_of_service:
                item["locationCodeableConcept"] = codeable_concept(
                    [coding(PLACE_OF_SERVICE, place_of_service)]
                )
            charge = money(line.get("charge"))
            if charge:
                item["unitPrice"] = charge
                item["net"] = charge
            q = quantity(line.get("units"))
            if q:
                item["quantity"] = q
            if line.get("modifiers"):
                item["modifier"] = [
                    codeable_concept([coding(CPT, m)]) for m in line["modifiers"] if m
                ]
            if line.get("diagnosis_pointers"):
                item["diagnosisSequence"] = [
                    int(p) for p in line["diagnosis_pointers"] if str(p).isdigit()
                ]
            res["item"].append(item)

    if type_of_bill:
        res.setdefault("supportingInfo", []).append({
            "sequence": 1,
            "category": codeable_concept(
                [coding("http://terminology.hl7.org/CodeSystem/claiminformationcategory", "info")]
            ),
            "code": codeable_concept(
                [coding(
                    "https://www.nubc.org/CodeSystem/TypeOfBill", type_of_bill
                )],
                text=f"Type of Bill {type_of_bill}",
            ),
        })

    total = money(total_charge)
    if total:
        res["total"] = total

    return _drop_empty(res)


# ---------------------------------------------------------------------------
# ExplanationOfBenefit (835 remittance)
# ---------------------------------------------------------------------------

def build_explanation_of_benefit(
    *,
    id: str,
    type_code: str = "professional",
    patient_ref: str,
    insurer_ref: str,
    provider_ref: str,
    use: str = "claim",
    outcome: str = "complete",
    created: str | None = None,
    items: list[dict[str, Any]] | None = None,
    totals: list[dict[str, Any]] | None = None,
    payment_amount: str | None = None,
    payment_date: str | None = None,
    profile: list[str] | None = None,
) -> dict[str, Any]:
    if profile is None:
        profile = {
            "institutional": [CARIN_EOB_INPATIENT],
            "outpatient":   [CARIN_EOB_OUTPATIENT],
        }.get(type_code, [CARIN_EOB_PROFESSIONAL])

    res: dict[str, Any] = {
        "resourceType": "ExplanationOfBenefit",
        "id": id,
        "meta": {"profile": profile},
        "status": "active",
        "type": codeable_concept([coding(CLAIM_TYPE, type_code)]),
        "use": use,
        "patient": reference(patient_ref),
        "created": created or now_iso(),
        "insurer": reference(insurer_ref),
        "provider": reference(provider_ref),
        "outcome": outcome,
    }

    if items:
        res["item"] = []
        for line in items:
            item: dict[str, Any] = {
                "sequence": int(line.get("sequence", 1)),
                "productOrService": codeable_concept(
                    [coding(line.get("system", CPT), line["code"])]
                ),
            }
            adjudication: list[dict[str, Any]] = []
            for cat, amount in (
                ("submitted", line.get("charge")),
                ("benefit", line.get("paid")),
                ("eligible", line.get("eligible")),
            ):
                m = money(amount)
                if m:
                    adjudication.append({
                        "category": codeable_concept([coding(ADJUDICATION, cat)]),
                        "amount": m,
                    })
            if adjudication:
                item["adjudication"] = adjudication
            res["item"].append(item)

    if totals:
        res["total"] = []
        for total in totals:
            m = money(total.get("amount"))
            if not m:
                continue
            res["total"].append({
                "category": codeable_concept(
                    [coding(ADJUDICATION, total.get("category", "benefit"))]
                ),
                "amount": m,
            })

    pay = money(payment_amount)
    if pay:
        res["payment"] = {"amount": pay}
        d = fhir_date(payment_date)
        if d:
            res["payment"]["date"] = d

    return _drop_empty(res)


# ---------------------------------------------------------------------------
# Coverage eligibility (270/271)
# ---------------------------------------------------------------------------

def build_coverage_eligibility_request(
    *,
    id: str,
    patient_ref: str,
    insurer_ref: str,
    provider_ref: str,
    service_date: str | None = None,
    service_types: list[str] | None = None,
    created: str | None = None,
) -> dict[str, Any]:
    res: dict[str, Any] = {
        "resourceType": "CoverageEligibilityRequest",
        "id": id,
        "status": "active",
        "purpose": ["benefits"],
        "patient": reference(patient_ref),
        "created": created or now_iso(),
        "insurer": reference(insurer_ref),
        "provider": reference(provider_ref),
    }
    d = fhir_date(service_date)
    if d:
        res["servicedDate"] = d
    if service_types:
        res["item"] = [
            {"category": codeable_concept([coding("https://x12.org/codes/service-type-codes", s)])}
            for s in service_types
        ]
    return _drop_empty(res)


def build_coverage_eligibility_response(
    *,
    id: str,
    request_ref: str,
    patient_ref: str,
    insurer_ref: str,
    outcome: str = "complete",
    benefits: list[dict[str, Any]] | None = None,
    created: str | None = None,
) -> dict[str, Any]:
    res: dict[str, Any] = {
        "resourceType": "CoverageEligibilityResponse",
        "id": id,
        "status": "active",
        "purpose": ["benefits"],
        "patient": reference(patient_ref),
        "created": created or now_iso(),
        "insurer": reference(insurer_ref),
        "request": reference(request_ref),
        "outcome": outcome,
    }
    if benefits:
        insurance: dict[str, Any] = {
            "coverage": reference("#coverage"),
            "inforce": True,
            "item": [],
        }
        for b in benefits:
            entry: dict[str, Any] = {
                "category": codeable_concept(
                    [coding("https://x12.org/codes/service-type-codes",
                            b.get("service_type", "30"))]
                ),
            }
            if b.get("eligibility"):
                entry["benefit"] = [{
                    "type": codeable_concept(
                        [coding(
                            "http://terminology.hl7.org/CodeSystem/benefit-type",
                            b["eligibility"],
                        )]
                    ),
                }]
            insurance["item"].append(entry)
        res["insurance"] = [insurance]
    return _drop_empty(res)


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------

def build_bundle(
    entries: list[dict[str, Any]],
    *,
    bundle_type: str = "collection",
    id: str | None = None,
) -> dict[str, Any]:
    """Wrap a list of FHIR resources in a Bundle.

    ``bundle_type`` is one of FHIR's defined codes: ``collection``,
    ``transaction``, ``batch``, ``searchset``, ``document``, ``message``, ...
    For ``transaction`` entries each gets a default POST request to its type.
    """
    bundle_entries = []
    for resource in entries:
        entry: dict[str, Any] = {"resource": resource}
        if bundle_type in ("transaction", "batch"):
            rtype = resource.get("resourceType")
            entry["request"] = {"method": "POST", "url": rtype or ""}
        bundle_entries.append(entry)

    bundle: dict[str, Any] = {
        "resourceType": "Bundle",
        "type": bundle_type,
        "entry": bundle_entries,
    }
    if id:
        bundle["id"] = id
    return bundle
