"""Da Vinci PAS <-> X12 278 mapper functions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from validation.acks.builder import X12Builder, now_stamp
from validation.engine import validate_document
from validation.parser import parse
from ..model import CPT, HCPCS, codeable_concept, coding, fhir_date

from ..pas_resources import (
    build_pas_claim,
    build_pas_claim_response,
    build_pas_request_bundle,
)
from ..resources import build_organization, build_patient, build_practitioner


def _slug(value: str | None, fallback: str) -> str:
    safe = "".join(ch for ch in (value or "") if ch.isalnum())
    return safe or fallback


def _first_coding_code(resource: dict[str, Any], path: list[str], system_contains: str | None = None) -> str | None:
    node: Any = resource
    for key in path:
        if isinstance(node, dict):
            node = node.get(key)
        else:
            return None
    if not isinstance(node, dict):
        return None
    for coding in node.get("coding", []):
        if system_contains and system_contains not in coding.get("system", ""):
            continue
        if coding.get("code"):
            return coding["code"]
    return None


def _supporting_code(claim: dict[str, Any], category_code: str) -> str | None:
    for info in claim.get("supportingInfo", []):
        category = info.get("category", {})
        if _first_coding_code({"x": category}, ["x"]) != category_code:
            continue
        code = info.get("code", {})
        for coding in code.get("coding", []):
            if coding.get("code"):
                return coding["code"]
    return None


def _identifier(claim: dict[str, Any], system: str) -> str | None:
    for ident in claim.get("identifier", []):
        if ident.get("system") == system and ident.get("value"):
            return ident["value"]
    return None

def _x12_identifier(system: str, value: str, type_code: str | None = None) -> dict[str, Any]:
    ident: dict[str, Any] = {"system": system, "value": value}
    if type_code:
        ident["type"] = codeable_concept([
            coding("http://terminology.hl7.org/CodeSystem/v2-0203", type_code)
        ])
    return ident


def _supporting_info(sequence: int, category_code: str, code: str, *, system: str) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "category": codeable_concept([
            coding("http://terminology.hl7.org/CodeSystem/claiminformationcategory", category_code)
        ]),
        "code": codeable_concept([coding(system, code)]),
    }


def _date_supporting_info(sequence: int, qualifier: str, value: str) -> dict[str, Any]:
    info = _supporting_info(
        sequence,
        f"dtp-{qualifier.lower()}",
        qualifier,
        system="https://x12.org/codes/date-time-qualifier",
    )
    d = fhir_date(value)
    if d:
        info["timingDate"] = d
    else:
        info["valueString"] = value
    return info


def _ref_identifier(qualifier: str, value: str) -> dict[str, Any]:
    return _x12_identifier(f"urn:x12:ref:{qualifier.lower()}", value, qualifier.upper())


def _procedure_from_composite(value: str) -> tuple[str | None, str | None, list[str]]:
    parts = value.split(":") if value else []
    if not parts:
        return None, None, []
    qualifier = parts[0] or None
    code = parts[1] if len(parts) > 1 else parts[0]
    modifiers = [p for p in parts[2:] if p]
    return qualifier, code, modifiers


def _service_from_sv(seg: Any, sequence: int, service_date: str | None) -> dict[str, Any] | None:
    if seg.seg_id == "SV1":
        qualifier, code, modifiers = _procedure_from_composite(seg.elem(1))
        if not code:
            return None
        return {
            "sequence": sequence,
            "qualifier": qualifier,
            "code": code,
            "system": HCPCS if qualifier == "HC" else CPT,
            "modifiers": modifiers,
            "charge": seg.elem(2) or None,
            "unit_basis": seg.elem(3) or None,
            "units": seg.elem(4) or None,
            "service_date": service_date,
        }
    if seg.seg_id == "SV2":
        qualifier, code, modifiers = _procedure_from_composite(seg.elem(2))
        if not code:
            return None
        return {
            "sequence": sequence,
            "revenue_code": seg.elem(1) or None,
            "qualifier": qualifier,
            "code": code,
            "system": HCPCS if qualifier == "HC" else CPT,
            "modifiers": modifiers,
            "charge": seg.elem(3) or None,
            "unit_basis": seg.elem(4) or None,
            "units": seg.elem(5) or None,
            "service_date": service_date,
        }
    if seg.seg_id.startswith("SV"):
        qualifier, code, modifiers = _procedure_from_composite(seg.elem(1))
        if not code:
            return None
        return {
            "sequence": sequence,
            "qualifier": qualifier,
            "code": code,
            "system": HCPCS if qualifier == "HC" else CPT,
            "modifiers": modifiers,
            "charge": seg.elem(2) or None,
            "units": seg.elem(4) or None,
            "service_date": service_date,
        }
    return None


def _diagnoses_from_hi(seg: Any) -> list[str]:
    diagnoses: list[str] = []
    for i in range(1, seg.max_element + 1):
        parts = seg.components(i)
        if not parts:
            continue
        qualifier = parts[0]
        code = parts[1] if len(parts) > 1 else ""
        if qualifier in {"ABK", "ABF", "BK", "BF", "PR", "BN"} and code:
            diagnoses.append(code)
    return diagnoses


def _x12_278_context(text: str) -> dict[str, Any]:
    doc = parse(text)
    ctx: dict[str, Any] = {
        "payer_name": None,
        "payer_id": None,
        "provider_name": None,
        "provider_npi": None,
        "patient_last": None,
        "patient_first": None,
        "patient_id": None,
        "trace_number": None,
        "service_date": None,
        "um_category": None,
        "certification_type": None,
        "service_level": None,
        "authorization_number": None,
        "decision_code": None,
        "total_charge": None,
        "refs": [],
        "dtps": [],
        "diagnoses": [],
        "services": [],
        "um_extras": [],
    }
    for txn in doc.transactions:
        if txn.set_code != "278":
            continue
        for seg in txn.segments:
            if seg.seg_id == "BHT":
                ctx["trace_number"] = ctx["trace_number"] or seg.elem(3)
            elif seg.seg_id == "NM1":
                entity = seg.elem(1)
                if entity in ("X3", "PR"):
                    ctx["payer_name"] = seg.elem(3)
                    if seg.elem(8) in ("PI", "XV"):
                        ctx["payer_id"] = seg.elem(9)
                elif entity in ("1P", "85", "FA"):
                    ctx["provider_name"] = seg.elem(3)
                    if seg.elem(8) == "XX":
                        ctx["provider_npi"] = seg.elem(9)
                elif entity in ("IL", "QC"):
                    ctx["patient_last"] = seg.elem(3)
                    ctx["patient_first"] = seg.elem(4)
                    ctx["patient_id"] = seg.elem(9)
            elif seg.seg_id == "TRN":
                ctx["trace_number"] = seg.elem(2) or ctx["trace_number"]
            elif seg.seg_id == "DTP" and seg.elem(1) in ("472", "291", "AAH"):
                ctx["dtps"].append({
                    "qualifier": seg.elem(1),
                    "format": seg.elem(2),
                    "value": seg.elem(3),
                })
                if seg.elem(1) in ("472", "AAH"):
                    ctx["service_date"] = seg.elem(3) or ctx["service_date"]
            elif seg.seg_id == "UM":
                ctx["um_category"] = seg.elem(1) or ctx["um_category"]
                ctx["certification_type"] = seg.elem(2) or ctx["certification_type"]
                ctx["service_level"] = seg.elem(3) or ctx["service_level"]
                for pos in range(4, seg.max_element + 1):
                    if seg.elem(pos):
                        ctx["um_extras"].append({
                            "position": pos,
                            "value": seg.elem(pos),
                        })
            elif seg.seg_id == "REF":
                if seg.elem(1) and seg.elem(2):
                    ctx["refs"].append({"qualifier": seg.elem(1), "value": seg.elem(2)})
                if seg.elem(1) in ("G1", "9F"):
                    ctx["authorization_number"] = seg.elem(2) or ctx["authorization_number"]
            elif seg.seg_id == "HI":
                ctx["diagnoses"].extend(_diagnoses_from_hi(seg))
            elif seg.seg_id.startswith("SV"):
                service = _service_from_sv(
                    seg, len(ctx["services"]) + 1, ctx.get("service_date")
                )
                if service:
                    ctx["services"].append(service)
            elif seg.seg_id == "HCR":
                ctx["decision_code"] = seg.elem(1) or ctx["decision_code"]
                ctx["authorization_number"] = seg.elem(2) or ctx["authorization_number"]
            elif seg.seg_id == "AMT" and seg.elem(1) in ("T3", "F5"):
                ctx["total_charge"] = seg.elem(2) or ctx["total_charge"]
    return ctx


def x12_278_request_to_pas_bundle(text: str) -> dict[str, Any]:
    """Map an X12 278 request into a PAS request Bundle."""
    report = validate_document(text, expected_transaction="278")
    if report.transaction_set != "278":
        raise ValueError("Expected an X12 278 prior-authorization payload.")

    ctx = _x12_278_context(text)
    patient_id = f"patient-{_slug(ctx.get('patient_id'), '1')}"
    payer_id = f"payer-{_slug(ctx.get('payer_id') or ctx.get('payer_name'), '1')}"
    provider_id = f"provider-{_slug(ctx.get('provider_npi') or ctx.get('provider_name'), '1')}"
    claim_id = f"pas-{_slug(ctx.get('trace_number'), 'request')}"
    additional_identifiers = [
        _ref_identifier(ref["qualifier"], ref["value"])
        for ref in ctx["refs"]
        if ref["qualifier"].upper() != "G1" or ref["value"] != ctx.get("authorization_number")
    ]
    additional_supporting = []
    for dtp in ctx["dtps"]:
        if dtp.get("qualifier") and dtp.get("value"):
            additional_supporting.append(
                _date_supporting_info(
                    len(additional_supporting) + 4,
                    dtp["qualifier"],
                    dtp["value"],
                )
            )
    for extra in ctx["um_extras"]:
        additional_supporting.append(
            _supporting_info(
                len(additional_supporting) + 4,
                f"um{extra['position']:02d}",
                extra["value"],
                system="https://x12.org/codes/health-care-services-review-information",
            )
        )

    patient = build_patient(
        id=patient_id,
        member_id=ctx.get("patient_id"),
        family=ctx.get("patient_last"),
        given=ctx.get("patient_first"),
    )
    payer = build_organization(
        id=payer_id,
        name=ctx.get("payer_name"),
        type_code="pay",
    )
    provider = build_practitioner(
        id=provider_id,
        npi=ctx.get("provider_npi"),
        family=ctx.get("provider_name"),
    )
    claim = build_pas_claim(
        id=claim_id,
        patient_ref=f"Patient/{patient_id}",
        insurer_ref=f"Organization/{payer_id}",
        provider_ref=f"Practitioner/{provider_id}",
        um_category=ctx.get("um_category"),
        certification_type=ctx.get("certification_type"),
        service_level=ctx.get("service_level"),
        service_date=ctx.get("service_date"),
        service_codes=ctx.get("services"),
        diagnosis_codes=ctx.get("diagnoses"),
        total_charge=ctx.get("total_charge"),
        trace_number=ctx.get("trace_number"),
        authorization_number=ctx.get("authorization_number"),
        additional_identifiers=additional_identifiers,
        additional_supporting_info=additional_supporting,
    )
    return build_pas_request_bundle([patient, payer, provider, claim], id=claim_id)


def x12_278_response_to_claim_response(text: str) -> dict[str, Any]:
    """Map an X12 278 response into a PAS ``ClaimResponse``.

    This first pass supports common HCR/REF identifiers and returns a standalone
    ClaimResponse.  Future crosswalk work should add item-level service
    decisions and payer-specific denial reason codes.
    """
    report = validate_document(text, expected_transaction="278")
    if report.transaction_set != "278":
        raise ValueError("Expected an X12 278 prior-authorization payload.")
    ctx = _x12_278_context(text)
    patient_id = f"patient-{_slug(ctx.get('patient_id'), '1')}"
    payer_id = f"payer-{_slug(ctx.get('payer_id') or ctx.get('payer_name'), '1')}"
    trace = _slug(ctx.get("trace_number"), "response")
    return build_pas_claim_response(
        id=f"pas-response-{trace}",
        claim_ref=f"Claim/pas-{trace}",
        patient_ref=f"Patient/{patient_id}",
        insurer_ref=f"Organization/{payer_id}",
        preauth_ref=ctx.get("authorization_number"),
        decision_code=ctx.get("decision_code"),
        disposition=ctx.get("decision_code"),
    )
def _diagnosis_codes_from_claim(claim: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for diagnosis in claim.get("diagnosis", []):
        concept = diagnosis.get("diagnosisCodeableConcept", {})
        for item in concept.get("coding", []):
            if item.get("code"):
                codes.append(item["code"])
                break
    return codes


def _ref_identifiers_from_claim(claim: dict[str, Any]) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for ident in claim.get("identifier", []):
        system = ident.get("system", "")
        value = ident.get("value")
        if not value or not system.startswith("urn:x12:ref:"):
            continue
        refs.append((system.rsplit(":", 1)[-1].upper(), value))
    return refs


def _dtp_values_from_claim(claim: dict[str, Any]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for info in claim.get("supportingInfo", []):
        category = _first_coding_code({"x": info.get("category", {})}, ["x"])
        if not category or not category.startswith("dtp-"):
            continue
        qualifier = category.replace("dtp-", "", 1).upper()
        value = info.get("timingDate") or info.get("valueString")
        if value:
            values.append((qualifier, "".join(ch for ch in value if ch.isdigit())[:8]))
    return values


def _service_date_from_claim(claim: dict[str, Any]) -> str | None:
    if claim.get("billablePeriod", {}).get("start"):
        return "".join(ch for ch in claim["billablePeriod"]["start"] if ch.isdigit())[:8]
    for item in claim.get("item", []):
        if item.get("servicedDate"):
            return "".join(ch for ch in item["servicedDate"] if ch.isdigit())[:8]
    return None


def _item_to_sv1(item: dict[str, Any]) -> tuple[str, str | None, str, str | None] | None:
    product = item.get("productOrService", {})
    code = None
    system = ""
    for coding_item in product.get("coding", []):
        if coding_item.get("code"):
            code = coding_item["code"]
            system = coding_item.get("system", "")
            break
    if not code:
        return None
    qualifier = "HC" if "HCPCS" in system or "cpt" in system.lower() else "HC"
    modifiers = []
    for modifier in item.get("modifier", []):
        mod_code = _first_coding_code({"x": modifier}, ["x"])
        if mod_code:
            modifiers.append(mod_code)
    composite = ":".join([qualifier, code, *modifiers])
    charge = item.get("unitPrice", {}).get("value") or item.get("net", {}).get("value")
    units = item.get("quantity", {}).get("value")
    return composite, str(charge) if charge is not None else None, "UN", str(units) if units is not None else None


def pas_claim_to_278_request(
    claim: dict[str, Any],
    *,
    sender_id: str = "TURBOHEDI",
    receiver_id: str = "RECEIVER",
    control: str = "000000001",
    now: datetime | None = None,
) -> str:
    """Serialize a PAS Claim into a minimal X12 278 request."""
    if claim.get("resourceType") != "Claim" or claim.get("use") != "preauthorization":
        raise ValueError("Expected a FHIR Claim with use='preauthorization'.")

    isa_date, gs_date, time_, time6 = now_stamp(now or datetime.now(timezone.utc))
    trace = _identifier(claim, "urn:x12:trn") or claim.get("id") or "TRACE001"
    um_category = _supporting_code(claim, "um-category") or "HS"
    certification_type = _supporting_code(claim, "certification-type") or "I"
    service_level = _supporting_code(claim, "service-level") or "1"
    service_date = _service_date_from_claim(claim)

    builder = X12Builder()
    builder.interchange(
        sender=sender_id,
        receiver=receiver_id,
        control=control,
        date=isa_date,
        time=time_,
    )
    builder.group(
        functional_id="HI",
        sender=sender_id,
        receiver=receiver_id,
        date=gs_date,
        time=time_,
        control=control.lstrip("0") or "1",
        version="005010X217",
    )
    builder.transaction(set_code="278", control="0001", version="005010X217")
    builder.add("BHT", "0007", "13", trace, gs_date, time6)
    builder.add("HL", "1", "", "20", "1")
    builder.add("NM1", "X3", "2", receiver_id, "", "", "", "", "PI", receiver_id)
    builder.add("HL", "2", "1", "21", "1")
    builder.add("NM1", "1P", "2", sender_id, "", "", "", "", "XX", "1234567893")
    builder.add("HL", "3", "2", "22", "0")
    builder.add("NM1", "IL", "1", "PATIENT", "", "", "", "", "MI", "UNKNOWN")
    for qualifier, value in _ref_identifiers_from_claim(claim):
        builder.add("REF", qualifier, value)
    written_dtps: set[tuple[str, str]] = set()
    for qualifier, value in _dtp_values_from_claim(claim):
        if value:
            builder.add("DTP", qualifier, "D8", value)
            written_dtps.add((qualifier, value))
    if service_date:
        if ("472", service_date) not in written_dtps:
            builder.add("DTP", "472", "D8", service_date)
    diagnoses = _diagnosis_codes_from_claim(claim)
    if diagnoses:
        builder.add("HI", f"ABK:{diagnoses[0]}", *(f"ABF:{dx}" for dx in diagnoses[1:]))
    builder.add("HL", "4", "3", "EV", "1")
    builder.add("UM", um_category, certification_type, service_level)
    for item in claim.get("item", []):
        sv1 = _item_to_sv1(item)
        if sv1:
            builder.add("SV1", *sv1)
    builder.end_transaction()
    builder.end_group()
    builder.end_interchange()
    return builder.render()
