"""ICD-10-CM diagnosis sequencing edits.

The first-listed (principal) diagnosis on a professional claim must be a code
that is permitted in that position. External cause-of-morbidity codes
(ICD-10-CM chapter 20, V00-Y99) and manifestation codes may never be sequenced
first.
"""
from __future__ import annotations

from datetime import date

from validation.codesets import get_active_registry
from validation.model import ClaimProjection

from ..model import EditCategory, ScrubFinding, ScrubReport, ScrubSeverity

_ICD10_CODESET = "icd10cm"


def _claim_service_date(claim: ClaimProjection) -> date | None:
    candidates = [line.service_date for line in claim.service_lines if line.service_date]
    candidates += [claim.statement_to_date, claim.statement_from_date]
    for raw in candidates:
        text = (raw or "").strip()
        if len(text) == 8 and text.isdigit():
            try:
                return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
            except ValueError:
                continue
    return None


def _check_codeset_currency(claim: ClaimProjection, report: ScrubReport) -> None:
    """Advisory: flag diagnosis codes invalid/deactivated for the service date.

    Consults the effective-dated code-set registry (Workstream 2) when one is
    loaded. Advisory-only — Triage detects and reports, never rewrites a code.
    """
    registry = get_active_registry()
    if registry is None or not registry.has(_ICD10_CODESET):
        return
    as_of = _claim_service_date(claim)
    for dx in claim.diagnosis_codes:
        result = registry.resolve(dx, _ICD10_CODESET, as_of)
        if result.known_codeset and not result.valid:
            report.add(
                ScrubFinding(
                    category=EditCategory.DIAGNOSIS_SEQUENCING,
                    severity=ScrubSeverity.ADVISORY,
                    code="DX.CODESET_EFFECTIVE",
                    message=(
                        f"Diagnosis {dx} {result.reason} per code-set version "
                        f"{result.version_label}."
                    ),
                    claim_id=claim.claim_id,
                    diagnosis_code=dx,
                    resolution=(
                        "Verify the diagnosis against the code set effective on "
                        "the date of service before resubmission."
                    ),
                    source="Triage effective-dated code-set registry",
                )
            )

# ICD-10-CM chapter 20 — external causes of morbidity — cannot be principal.
_EXTERNAL_CAUSE_FIRST_LETTERS = ("V", "W", "X", "Y")

# A small set of ICD-10-CM manifestation codes that require an underlying
# condition to be coded first ("code first" / "in diseases classified
# elsewhere"). Production deployments should load the full manifestation set.
_MANIFESTATION_CODES = {
    "D63.1": "Anemia in chronic kidney disease",
    "D77": "Disorders of blood in diseases classified elsewhere",
    "F02.80": "Dementia in other diseases classified elsewhere",
    "G63": "Polyneuropathy in diseases classified elsewhere",
    "H36": "Retinal disorders in diseases classified elsewhere",
    "M90.80": "Osteopathy in diseases classified elsewhere",
}


def apply(claims: list[ClaimProjection], report: ScrubReport, **_ctx) -> None:
    for claim in claims:
        if not claim.diagnosis_codes:
            continue
        _check_codeset_currency(claim, report)
        principal = claim.diagnosis_codes[0].strip().upper()
        normalized = principal.replace(".", "")

        if normalized[:1] in _EXTERNAL_CAUSE_FIRST_LETTERS:
            report.add(
                ScrubFinding(
                    category=EditCategory.DIAGNOSIS_SEQUENCING,
                    severity=ScrubSeverity.DENY,
                    code="DXSEQ.EXTERNAL_CAUSE_PRINCIPAL",
                    message=(
                        f"Diagnosis {claim.diagnosis_codes[0]} is an external "
                        "cause-of-morbidity code (ICD-10-CM chapter 20) and may "
                        "not be sequenced as the principal diagnosis."
                    ),
                    claim_id=claim.claim_id,
                    diagnosis_code=claim.diagnosis_codes[0],
                    resolution=(
                        "Sequence the condition/injury code first; external "
                        "cause codes are always secondary."
                    ),
                    source="ICD-10-CM Official Guidelines for Coding and Reporting",
                )
            )

        for manifestation, label in _MANIFESTATION_CODES.items():
            if principal.startswith(manifestation):
                report.add(
                    ScrubFinding(
                        category=EditCategory.DIAGNOSIS_SEQUENCING,
                        severity=ScrubSeverity.REVIEW,
                        code="DXSEQ.MANIFESTATION_PRINCIPAL",
                        message=(
                            f"Diagnosis {claim.diagnosis_codes[0]} ({label}) is a "
                            "manifestation code and requires the underlying "
                            "condition to be coded first."
                        ),
                        claim_id=claim.claim_id,
                        diagnosis_code=claim.diagnosis_codes[0],
                        resolution="Sequence the underlying etiology code first.",
                        source="ICD-10-CM Official Guidelines for Coding and Reporting",
                    )
                )
                break
