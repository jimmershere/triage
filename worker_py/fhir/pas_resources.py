"""Da Vinci PAS-focused FHIR R4 resource builders.

These builders intentionally return plain JSON-shaped dictionaries, matching the
rest of :mod:`worker_py.fhir`.  They cover the PAS structures needed to bridge
X12 278 prior-authorization traffic with FHIR:

* PAS request ``Bundle``
* PAS request ``Claim``
* PAS response ``ClaimResponse``
* PAS attachment ``Bundle`` with ``DocumentReference`` + ``Binary``
"""
from __future__ import annotations

import base64
from typing import Any

from .model import (
    CPT,
    DAVINCI_PAS_CLAIM,
    codeable_concept,
    coding,
    fhir_date,
    money,
    now_iso,
    quantity,
    reference,
)
from .resources import build_bundle

DAVINCI_PAS_CLAIM_RESPONSE = (
    "http://hl7.org/fhir/us/davinci-pas/StructureDefinition/profile-claimresponse"
)
DAVINCI_PAS_ATTACHMENT_BUNDLE = (
    "http://hl7.org/fhir/us/davinci-pas/StructureDefinition/profile-pas-attachment-bundle"
)
DAVINCI_PAS_DOCUMENT_REFERENCE = (
    "http://hl7.org/fhir/us/davinci-pas/StructureDefinition/profile-documentreference"
)
DAVINCI_PAS_BINARY = (
    "http://hl7.org/fhir/us/davinci-pas/StructureDefinition/profile-binary"
)

PAS_UM_CATEGORY = "https://x12.org/codes/health-care-services-review-request-category"
PAS_CERTIFICATION_TYPE = "https://x12.org/codes/certification-type"
PAS_SERVICE_LEVEL = "https://x12.org/codes/service-level"
PAS_DECISION = "https://x12.org/codes/health-care-services-review-decision"


def _drop_empty(resource: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in resource.items() if v not in (None, "", [], {})}


def build_pas_claim(
    *,
    id: str,
    patient_ref: str,
    insurer_ref: str,
    provider_ref: str,
    created: str | None = None,
    um_category: str | None = None,
    certification_type: str | None = None,
    service_level: str | None = None,
    service_date: str | None = None,
    service_codes: list[dict[str, Any]] | None = None,
    diagnosis_codes: list[str] | None = None,
    total_charge: str | None = None,
    trace_number: str | None = None,
    authorization_number: str | None = None,
    additional_identifiers: list[dict[str, Any]] | None = None,
    additional_supporting_info: list[dict[str, Any]] | None = None,
    profile: list[str] | None = None,
) -> dict[str, Any]:
    """Build a PAS profiled FHIR ``Claim`` for preauthorization."""
    res: dict[str, Any] = {
        "resourceType": "Claim",
        "id": id,
        "meta": {"profile": profile or [DAVINCI_PAS_CLAIM]},
        "status": "active",
        "type": codeable_concept([
            coding("http://terminology.hl7.org/CodeSystem/claim-type", "professional")
        ]),
        "use": "preauthorization",
        "patient": reference(patient_ref),
        "created": created or now_iso(),
        "insurer": reference(insurer_ref),
        "provider": reference(provider_ref),
        "priority": codeable_concept([
            coding("http://terminology.hl7.org/CodeSystem/processpriority", "normal")
        ]),
    }

    identifiers = []
    if trace_number:
        identifiers.append({
            "system": "urn:x12:trn",
            "value": trace_number,
            "type": codeable_concept([
                coding("http://terminology.hl7.org/CodeSystem/v2-0203", "PLAC")
            ]),
        })
    if authorization_number:
        identifiers.append({
            "system": "urn:x12:ref:g1",
            "value": authorization_number,
            "type": codeable_concept([
                coding("http://terminology.hl7.org/CodeSystem/v2-0203", "FILL")
            ]),
        })
    if additional_identifiers:
        identifiers.extend(additional_identifiers)
    if identifiers:
        res["identifier"] = identifiers

    supporting_info: list[dict[str, Any]] = []
    for code, system, category_code in (
        (um_category, PAS_UM_CATEGORY, "um-category"),
        (certification_type, PAS_CERTIFICATION_TYPE, "certification-type"),
        (service_level, PAS_SERVICE_LEVEL, "service-level"),
    ):
        if not code:
            continue
        supporting_info.append({
            "sequence": len(supporting_info) + 1,
            "category": codeable_concept([
                coding("http://terminology.hl7.org/CodeSystem/claiminformationcategory", category_code)
            ]),
            "code": codeable_concept([coding(system, code)]),
        })
    if supporting_info:
        res["supportingInfo"] = supporting_info
    if additional_supporting_info:
        res.setdefault("supportingInfo", []).extend(additional_supporting_info)

    if diagnosis_codes:
        res["diagnosis"] = [
            {
                "sequence": i,
                "diagnosisCodeableConcept": codeable_concept([
                    coding("http://hl7.org/fhir/sid/icd-10-cm", dx)
                ]),
            }
            for i, dx in enumerate(diagnosis_codes, start=1)
        ]

    if service_codes:
        res["item"] = []
        for i, svc in enumerate(service_codes, start=1):
            item: dict[str, Any] = {
                "sequence": int(svc.get("sequence", i)),
                "productOrService": codeable_concept([
                    coding(svc.get("system", CPT), svc.get("code"))
                ]),
            }
            d = fhir_date(svc.get("service_date") or service_date)
            if d:
                item["servicedDate"] = d
            q = quantity(svc.get("units"))
            if q:
                item["quantity"] = q
            price = money(svc.get("charge"))
            if price:
                item["unitPrice"] = price
                item["net"] = price
            res["item"].append(item)
    elif service_date:
        d = fhir_date(service_date)
        if d:
            res["billablePeriod"] = {"start": d, "end": d}

    total = money(total_charge)
    if total:
        res["total"] = total

    return _drop_empty(res)


def build_pas_claim_response(
    *,
    id: str,
    claim_ref: str,
    patient_ref: str,
    insurer_ref: str,
    request_ref: str | None = None,
    created: str | None = None,
    outcome: str = "complete",
    disposition: str | None = None,
    preauth_ref: str | None = None,
    decision_code: str | None = None,
    item_decisions: list[dict[str, Any]] | None = None,
    profile: list[str] | None = None,
) -> dict[str, Any]:
    """Build a PAS profiled FHIR ``ClaimResponse``."""
    res: dict[str, Any] = {
        "resourceType": "ClaimResponse",
        "id": id,
        "meta": {"profile": profile or [DAVINCI_PAS_CLAIM_RESPONSE]},
        "status": "active",
        "type": codeable_concept([
            coding("http://terminology.hl7.org/CodeSystem/claim-type", "professional")
        ]),
        "use": "preauthorization",
        "patient": reference(patient_ref),
        "created": created or now_iso(),
        "insurer": reference(insurer_ref),
        "request": reference(request_ref or claim_ref),
        "outcome": outcome,
    }
    if disposition:
        res["disposition"] = disposition
    if preauth_ref:
        res["preAuthRef"] = preauth_ref
    if decision_code:
        res["processNote"] = [{
            "number": 1,
            "type": "display",
            "text": decision_code,
        }]
    if item_decisions:
        res["item"] = []
        for i, decision in enumerate(item_decisions, start=1):
            adjudication = {
                "category": codeable_concept([coding(PAS_DECISION, "decision")]),
                "reason": codeable_concept([
                    coding(PAS_DECISION, decision.get("decision_code", decision_code))
                ]),
            }
            amount = money(decision.get("amount"))
            if amount:
                adjudication["amount"] = amount
            res["item"].append({
                "itemSequence": int(decision.get("sequence", i)),
                "adjudication": [adjudication],
            })
    return _drop_empty(res)


def build_pas_attachment_bundle(
    *,
    id: str,
    claim_ref: str,
    patient_ref: str,
    content: bytes | str,
    content_type: str = "application/octet-stream",
    title: str | None = None,
) -> dict[str, Any]:
    """Build a PAS supporting-documentation bundle.

    The bundle shape mirrors DTR/275-style attachment exchange while staying
    dependency-free until the official validator is wired in.
    """
    raw = content.encode("utf-8") if isinstance(content, str) else content
    binary = {
        "resourceType": "Binary",
        "id": f"{id}-binary",
        "meta": {"profile": [DAVINCI_PAS_BINARY]},
        "contentType": content_type,
        "data": base64.b64encode(raw).decode("ascii"),
    }
    document = {
        "resourceType": "DocumentReference",
        "id": f"{id}-document",
        "meta": {"profile": [DAVINCI_PAS_DOCUMENT_REFERENCE]},
        "status": "current",
        "subject": reference(patient_ref),
        "description": title or "PAS supporting documentation",
        "context": {"related": [reference(claim_ref)]},
        "content": [{
            "attachment": {
                "contentType": content_type,
                "title": title or "PAS attachment",
                "url": f"Binary/{binary['id']}",
            }
        }],
    }
    bundle = build_bundle([document, binary], bundle_type="collection", id=id)
    bundle["meta"] = {"profile": [DAVINCI_PAS_ATTACHMENT_BUNDLE]}
    return bundle


def build_pas_request_bundle(
    entries: list[dict[str, Any]],
    *,
    id: str | None = None,
    bundle_type: str = "collection",
) -> dict[str, Any]:
    """Wrap PAS request resources in a FHIR Bundle."""
    return build_bundle(entries, bundle_type=bundle_type, id=id)
