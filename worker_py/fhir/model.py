"""FHIR R4 datatype helpers.

Small, focused builders for the FHIR datatypes used by this package's resource
builders. They produce plain dicts (FHIR JSON shape) — no class hierarchy —
because every downstream consumer (REST, file output, the Bundle builder) wants
JSON anyway.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable

# --- canonical code systems used by HIPAA <-> FHIR ----------------------

ICD10_CM = "http://hl7.org/fhir/sid/icd-10-cm"
CPT = "http://www.ama-assn.org/go/cpt"
HCPCS = "https://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"
NPI = "http://hl7.org/fhir/sid/us-npi"
SSN = "http://hl7.org/fhir/sid/us-ssn"
TAX_ID = "urn:oid:2.16.840.1.113883.4.4"
PLACE_OF_SERVICE = "https://www.cms.gov/Medicare/Coding/place-of-service-codes"
CLAIM_TYPE = "http://terminology.hl7.org/CodeSystem/claim-type"
ADJUDICATION = "http://terminology.hl7.org/CodeSystem/adjudication"
EOB_TYPE = "http://terminology.hl7.org/CodeSystem/ex-claimtype"
ELIGIBILITY_SERVICE_TYPE = "http://hl7.org/fhir/ex-serviceuomtype"

# CARIN BB ExplanationOfBenefit profile URLs.
CARIN_EOB_PROFESSIONAL = (
    "http://hl7.org/fhir/us/carin-bb/StructureDefinition/"
    "C4BB-ExplanationOfBenefit-Professional-NonClinician"
)
CARIN_EOB_INPATIENT = (
    "http://hl7.org/fhir/us/carin-bb/StructureDefinition/"
    "C4BB-ExplanationOfBenefit-Inpatient-Institutional"
)
CARIN_EOB_OUTPATIENT = (
    "http://hl7.org/fhir/us/carin-bb/StructureDefinition/"
    "C4BB-ExplanationOfBenefit-Outpatient-Institutional"
)
DAVINCI_PAS_CLAIM = (
    "http://hl7.org/fhir/us/davinci-pas/StructureDefinition/profile-claim"
)


# ---------------------------------------------------------------------------
# Datatype builders
# ---------------------------------------------------------------------------

def coding(system: str, code: str | None, display: str | None = None) -> dict[str, Any]:
    """A FHIR Coding (system / code / display)."""
    c: dict[str, Any] = {"system": system}
    if code is not None:
        c["code"] = code
    if display:
        c["display"] = display
    return c


def codeable_concept(
    codings: Iterable[dict[str, Any]] | None = None,
    *,
    text: str | None = None,
) -> dict[str, Any]:
    """A FHIR CodeableConcept (one or more codings, optional free text)."""
    cc: dict[str, Any] = {}
    if codings:
        coding_list = [c for c in codings if c]
        if coding_list:
            cc["coding"] = coding_list
    if text:
        cc["text"] = text
    return cc


def identifier(
    value: str | None,
    *,
    system: str | None = None,
    use: str | None = None,
    type_code: str | None = None,
) -> dict[str, Any] | None:
    if not value:
        return None
    ident: dict[str, Any] = {"value": value}
    if system:
        ident["system"] = system
    if use:
        ident["use"] = use
    if type_code:
        ident["type"] = codeable_concept(
            [coding("http://terminology.hl7.org/CodeSystem/v2-0203", type_code)]
        )
    return ident


def reference(ref: str, *, display: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"reference": ref}
    if display:
        out["display"] = display
    return out


def human_name(
    family: str | None = None,
    given: str | None = None,
    *,
    use: str = "official",
) -> dict[str, Any] | None:
    name: dict[str, Any] = {"use": use}
    if family:
        name["family"] = family
    if given:
        name["given"] = [given]
    if "family" not in name and "given" not in name:
        return None
    return name


def fhir_date(value: str | None) -> str | None:
    """Convert an X12 D8 (CCYYMMDD) date string to FHIR ``YYYY-MM-DD``."""
    if not value or len(value) != 8 or not value.isdigit():
        return None
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def fhir_dateTime(value: str | None) -> str | None:
    """Convert an X12 D8 date to a FHIR ``dateTime`` string."""
    d = fhir_date(value)
    return d  # FHIR accepts date as a valid dateTime


def money(amount: str | float | None, currency: str = "USD") -> dict[str, Any] | None:
    if amount is None or amount == "":
        return None
    try:
        value = round(float(amount), 2)
    except (TypeError, ValueError):
        return None
    return {"value": value, "currency": currency}


def quantity(value: str | float | None, *, unit: str | None = None) -> dict[str, Any] | None:
    if value is None or value == "":
        return None
    try:
        q: dict[str, Any] = {"value": float(value)}
    except (TypeError, ValueError):
        return None
    if unit:
        q["unit"] = unit
    return q


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def gender_from_x12(value: str | None) -> str | None:
    """Map an X12 administrative gender code (M/F/U) to FHIR ``Patient.gender``."""
    if not value:
        return None
    mapping = {"M": "male", "F": "female", "U": "unknown"}
    return mapping.get(value.strip().upper())


def gender_to_x12(value: str | None) -> str | None:
    """Inverse of :func:`gender_from_x12`."""
    if not value:
        return None
    mapping = {"male": "M", "female": "F", "unknown": "U", "other": "U"}
    return mapping.get(value.strip().lower())
