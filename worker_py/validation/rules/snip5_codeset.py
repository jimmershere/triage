"""SNIP type 5 — external code-set validation.

Runs over the :class:`ClaimProjection` records produced by a guide module and
checks every value that is governed by an external code set: place of service,
claim frequency, claim filing indicator, administrative gender, ICD-10-CM
diagnosis codes, HCPCS/CPT procedure codes and NPI check digits.

For code sets that are bundled only as a curated subset (``complete=false``) an
unknown value is reported as a *warning* rather than an *error* so a partial
table never produces a false rejection.
"""
from __future__ import annotations

from datetime import date

from ..codesets import (
    get_active_registry,
    get_codeset,
    is_valid_hcpcs_cpt,
    is_valid_icd10_cm,
    is_valid_npi,
)
from ..model import (
    ClaimProjection,
    Severity,
    SnipType,
    ValidationIssue,
    ValidationReport,
)

_SPEC = "HIPAA 837 — external code set (SNIP type 5)"

# Code-set name (in the effective-dated registry) for ICD-10-CM diagnoses.
_ICD10_CODESET = "icd10cm"


def _claim_service_date(claim: ClaimProjection) -> date | None:
    """Resolve the claim's service / discharge date for effective-dated lookups.

    Uses the earliest line service date, falling back to the statement-covers
    period. ICD-10 validation is service-date driven (date of discharge for
    inpatient), so this drives *which* code-set version applies.
    """
    candidates: list[str] = []
    for line in claim.service_lines:
        if line.service_date:
            candidates.append(line.service_date)
    if claim.statement_to_date:
        candidates.append(claim.statement_to_date)
    if claim.statement_from_date:
        candidates.append(claim.statement_from_date)
    for raw in candidates:
        text = (raw or "").strip()
        if len(text) == 8 and text.isdigit():
            try:
                return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
            except ValueError:
                continue
    return None


def _membership_issue(
    report: ValidationReport,
    *,
    codeset_name: str,
    value: str,
    claim: ClaimProjection,
    code: str,
    segment_id: str,
    label: str,
) -> None:
    """Emit a code-set membership issue, severity scaled to table completeness."""
    cs = get_codeset(codeset_name)
    if cs.is_valid(value):
        return
    if cs.complete:
        severity = Severity.ERROR
        message = f"{label} '{value}' is not a valid {codeset_name} code."
    else:
        severity = Severity.WARNING
        message = (
            f"{label} '{value}' was not found in the bundled {codeset_name} "
            "subset; verify against the full published code set."
        )
    report.add(
        ValidationIssue(
            snip_type=SnipType.CODE_SET,
            severity=severity,
            code=code,
            message=message,
            segment_id=segment_id,
            claim_id=claim.claim_id,
            actual=value,
            spec_ref=_SPEC,
        )
    )


def validate_codesets(report: ValidationReport) -> None:
    """Validate external code sets across every claim in the report."""
    for claim in report.claims:
        _validate_claim_codesets(claim, report)


def _validate_claim_codesets(claim: ClaimProjection, report: ValidationReport) -> None:
    if claim.place_of_service:
        _membership_issue(
            report,
            codeset_name="place_of_service",
            value=claim.place_of_service,
            claim=claim,
            code="CODE.POS.INVALID",
            segment_id="CLM",
            label="Place of service code",
        )

    if claim.claim_frequency_code:
        _membership_issue(
            report,
            codeset_name="claim_frequency",
            value=claim.claim_frequency_code,
            claim=claim,
            code="CODE.CLM05.FREQUENCY",
            segment_id="CLM",
            label="Claim frequency code",
        )

    if claim.filing_indicator_code:
        _membership_issue(
            report,
            codeset_name="filing_indicator",
            value=claim.filing_indicator_code,
            claim=claim,
            code="CODE.SBR09.FILING_INDICATOR",
            segment_id="SBR",
            label="Claim filing indicator code",
        )

    if claim.patient_gender:
        _membership_issue(
            report,
            codeset_name="gender",
            value=claim.patient_gender,
            claim=claim,
            code="CODE.DMG03.GENDER",
            segment_id="DMG",
            label="Patient gender code",
        )

    # ICD-10-CM diagnosis codes — structural format validation always runs;
    # effective-dated membership is layered on top when a code-set registry is
    # loaded (Workstream 2), keyed to the claim's service/discharge date.
    registry = get_active_registry()
    as_of = _claim_service_date(claim)
    for dx in claim.diagnosis_codes:
        if not is_valid_icd10_cm(dx):
            report.add(
                ValidationIssue(
                    snip_type=SnipType.CODE_SET,
                    severity=Severity.ERROR,
                    code="CODE.HI.ICD10_FORMAT",
                    message=(
                        f"Diagnosis code '{dx}' is not a structurally valid "
                        "ICD-10-CM code."
                    ),
                    segment_id="HI",
                    claim_id=claim.claim_id,
                    actual=dx,
                    spec_ref=_SPEC,
                )
            )
            continue
        if registry is not None and registry.has(_ICD10_CODESET):
            result = registry.resolve(dx, _ICD10_CODESET, as_of)
            if result.known_codeset and not result.valid:
                # Advisory: membership/effective-date issues are warnings so a
                # curated registry subset never produces a false rejection. We
                # detect and report — never rewrite the code on the claim.
                report.add(
                    ValidationIssue(
                        snip_type=SnipType.CODE_SET,
                        severity=Severity.WARNING,
                        code="CODE.HI.ICD10_EFFECTIVE",
                        message=(
                            f"Diagnosis code '{dx}' {result.reason} "
                            f"(code-set version {result.version_label}, "
                            f"service date {as_of.isoformat() if as_of else 'unknown'})."
                        ),
                        segment_id="HI",
                        claim_id=claim.claim_id,
                        actual=dx,
                        spec_ref=_SPEC,
                    )
                )

    # NPI check-digit validation.
    for npi, label, seg in (
        (claim.billing_provider_npi, "Billing provider", "NM1*85"),
        (claim.rendering_provider_npi, "Rendering provider", "NM1*82"),
    ):
        if npi and not is_valid_npi(npi):
            report.add(
                ValidationIssue(
                    snip_type=SnipType.CODE_SET,
                    severity=Severity.ERROR,
                    code="CODE.NPI.CHECKSUM",
                    message=(
                        f"{label} NPI '{npi}' fails the NPI Luhn check-digit "
                        "validation."
                    ),
                    segment_id=seg,
                    claim_id=claim.claim_id,
                    actual=npi,
                    spec_ref="CMS National Provider Identifier Standard",
                )
            )

    # HCPCS/CPT procedure codes — format validation.
    for line in claim.service_lines:
        if line.procedure_code and not is_valid_hcpcs_cpt(line.procedure_code):
            report.add(
                ValidationIssue(
                    snip_type=SnipType.CODE_SET,
                    severity=Severity.ERROR,
                    code="CODE.SV101.PROCEDURE_FORMAT",
                    message=(
                        f"Procedure code '{line.procedure_code}' is not a "
                        "structurally valid HCPCS/CPT code."
                    ),
                    segment_id="SV1",
                    claim_id=claim.claim_id,
                    line_no=line.line_no,
                    actual=line.procedure_code,
                    spec_ref=_SPEC,
                )
            )
