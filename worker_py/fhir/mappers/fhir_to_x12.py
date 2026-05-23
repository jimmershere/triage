"""FHIR -> X12 mapper.

Converts a FHIR R4 ``Claim`` resource (standalone or inside a ``Bundle``) into
a conformant X12 005010X222A1 837P transaction. Patient / Organization /
Practitioner references inside the same bundle are resolved automatically.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from validation.acks.builder import X12Builder, now_stamp

_DEFAULT_VERSION = "005010X222A1"


def _lookup(resource: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]]]:
    """Locate the first Claim and build a reference index."""
    index: dict[str, dict[str, Any]] = {}

    def register(res: dict[str, Any]) -> None:
        rtype = res.get("resourceType")
        rid = res.get("id")
        if rtype and rid:
            index[f"{rtype}/{rid}"] = res

    if resource.get("resourceType") == "Bundle":
        for entry in resource.get("entry", []):
            res = entry.get("resource")
            if isinstance(res, dict):
                register(res)
                if entry.get("fullUrl"):
                    index[entry["fullUrl"]] = res
        claim = next(
            (r for r in index.values() if r.get("resourceType") == "Claim"),
            None,
        )
        return claim, index

    if resource.get("resourceType") == "Claim":
        register(resource)
        return resource, index

    return None, index


def _resolve(ref: dict[str, Any] | None, index: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not ref:
        return None
    target = ref.get("reference")
    if target and target in index:
        return index[target]
    return None


def _name_pieces(resource: dict[str, Any] | None) -> tuple[str | None, str | None]:
    if not resource or not resource.get("name"):
        return None, None
    name = resource["name"][0] if isinstance(resource["name"], list) else resource["name"]
    family = name.get("family") if isinstance(name, dict) else None
    given = None
    if isinstance(name, dict) and name.get("given"):
        first = name["given"][0] if isinstance(name["given"], list) else name["given"]
        given = first
    return family, given


def _identifier_value(
    resource: dict[str, Any] | None, system: str | None = None
) -> str | None:
    if not resource:
        return None
    for ident in resource.get("identifier", []):
        if not system or ident.get("system") == system:
            value = ident.get("value")
            if value:
                return value
    return None


def _to_x12_date(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def _gender_to_x12(value: str | None) -> str:
    return {"male": "M", "female": "F"}.get((value or "").lower(), "U")


def _variant_from_claim(claim: dict[str, Any]) -> tuple[str, str, str]:
    """Return (variant, version, facility_qualifier) inferred from ``Claim.type``."""
    code = None
    for c in claim.get("type", {}).get("coding", []):
        if c.get("system", "").endswith("claim-type"):
            code = c.get("code")
            break
    if code == "institutional":
        return "I", "005010X223A2", "A"
    if code == "oral":
        return "D", "005010X224A2", "B"
    return "P", _DEFAULT_VERSION, "B"


def _pos_from_item(item: dict[str, Any]) -> str | None:
    loc = item.get("locationCodeableConcept", {})
    for c in loc.get("coding", []):
        if "place-of-service" in c.get("system", ""):
            return c.get("code")
    return None


def _proc_from_item(item: dict[str, Any]) -> str | None:
    prod = item.get("productOrService", {})
    for c in prod.get("coding", []):
        if c.get("system") in (
            "http://www.ama-assn.org/go/cpt",
            "https://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets",
        ):
            return c.get("code")
        if c.get("code"):
            return c.get("code")
    return None


def fhir_claim_to_837(
    resource: dict[str, Any],
    *,
    sender_id: str = "TURBOHEDI",
    receiver_id: str = "RECEIVER",
    control: str = "000000001",
    now: datetime | None = None,
) -> str:
    """Build an X12 837 transaction from a FHIR Claim resource or Bundle."""
    claim, index = _lookup(resource)
    if claim is None:
        raise ValueError("No Claim resource found in the input FHIR payload.")

    variant, version, facility_qualifier = _variant_from_claim(claim)
    patient = _resolve(claim.get("patient"), index)
    insurer = _resolve(claim.get("insurer"), index)
    provider = _resolve(claim.get("provider"), index)

    isa_date, gs_date, time_, time6 = now_stamp(now or datetime.now(timezone.utc))
    builder = X12Builder()
    builder.interchange(
        sender=sender_id,
        receiver=receiver_id,
        control=control,
        date=isa_date,
        time=time_,
    )
    builder.group(
        functional_id="HC",
        sender=sender_id,
        receiver=receiver_id,
        date=gs_date,
        time=time_,
        control=control.lstrip("0") or "1",
        version=version,
    )
    builder.transaction(set_code="837", control="0001", version=version)
    builder.add(
        "BHT", "0019", "00",
        claim.get("id") or "REF", gs_date, time6, "CH",
    )

    # Submitter / Receiver (1000A / 1000B).
    builder.add("NM1", "41", "2", sender_id, "", "", "", "", "46", sender_id)
    builder.add("PER", "IC", "TURBOHEDI", "TE", "5555555555")
    builder.add("NM1", "40", "2", receiver_id, "", "", "", "", "46", receiver_id)

    # Billing Provider hierarchy (Loop 2000A / 2010AA).
    billing_npi = _identifier_value(provider, "http://hl7.org/fhir/sid/us-npi")
    billing_tax_id = _identifier_value(provider, "urn:oid:2.16.840.1.113883.4.4")
    billing_name = (provider or {}).get("name") or "BILLING PROVIDER"
    builder.add("HL", "1", "", "20", "1")
    builder.add(
        "NM1", "85", "2", billing_name,
        "", "", "", "",
        "XX" if billing_npi else "",
        billing_npi or "",
    )
    if billing_tax_id:
        builder.add("REF", "EI", billing_tax_id)

    # Subscriber hierarchy (Loop 2000B).
    family, given = _name_pieces(patient)
    member_id = _identifier_value(patient, "http://hl7.org/fhir/sid/us-mb") or "UNKNOWN"
    dob = _to_x12_date((patient or {}).get("birthDate"))
    gender = _gender_to_x12((patient or {}).get("gender"))

    builder.add("HL", "2", "1", "22", "0")
    builder.add("SBR", "P", "18", "", "", "", "", "", "", "CI")
    builder.add(
        "NM1", "IL", "1", family or "PATIENT",
        given or "", "", "", "", "MI", member_id,
    )
    if dob:
        builder.add("DMG", "D8", dob, gender)

    payer_id = _identifier_value(insurer) or "PAYER"
    payer_name = (insurer or {}).get("name") or "PAYER"
    builder.add("NM1", "PR", "2", payer_name, "", "", "", "", "PI", payer_id)

    # Claim (Loop 2300).
    total = claim.get("total", {}).get("value") if isinstance(claim.get("total"), dict) else None
    pos = claim.get("supportingInfo", [{}])[0].get("code", {}).get("coding", [{}])[0].get("code") if variant == "I" else None
    pos = pos or _pos_from_item((claim.get("item") or [{}])[0]) or "11"
    frequency = "1"
    builder.add(
        "CLM",
        claim.get("id") or "CLAIM",
        f"{total:.2f}" if isinstance(total, (int, float)) else "",
        "", "",
        f"{pos}:{facility_qualifier}:{frequency}",
        "Y", "A", "Y", "Y",
    )

    # Diagnoses (Loop 2300 HI).
    diagnoses: list[str] = []
    for dx in claim.get("diagnosis", []):
        concept = dx.get("diagnosisCodeableConcept", {})
        for c in concept.get("coding", []):
            if c.get("system") == "http://hl7.org/fhir/sid/icd-10-cm" and c.get("code"):
                diagnoses.append(c["code"])
                break
    if diagnoses:
        principal = diagnoses[0]
        others = diagnoses[1:]
        hi_components: list[str] = [f"ABK:{principal}"]
        for dx in others[:11]:
            hi_components.append(f"ABF:{dx}")
        builder.add("HI", *hi_components)

    # Service lines (Loop 2400).
    for i, item in enumerate(claim.get("item", []), start=1):
        builder.add("LX", str(int(item.get("sequence", i))))
        proc = _proc_from_item(item) or ""
        modifiers = [m.get("coding", [{}])[0].get("code", "") for m in item.get("modifier", [])]
        composite = "HC:" + proc + ("".join(":" + m for m in modifiers if m))
        unit_price = item.get("unitPrice", {}).get("value")
        units = item.get("quantity", {}).get("value", 1)
        ptr = ":".join(str(p) for p in item.get("diagnosisSequence", [])) or "1"
        builder.add(
            "SV1",
            composite,
            f"{unit_price:.2f}" if isinstance(unit_price, (int, float)) else "",
            "UN",
            str(units),
            "",
            "",
            ptr,
        )
        if item.get("servicedDate"):
            d = _to_x12_date(item["servicedDate"])
            if d:
                builder.add("DTP", "472", "D8", d)

    builder.end_transaction()
    builder.end_group()
    builder.end_interchange()
    return builder.render()
