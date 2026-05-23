"""ICD-10-CM diagnosis sequencing edits.

The first-listed (principal) diagnosis on a professional claim must be a code
that is permitted in that position. External cause-of-morbidity codes
(ICD-10-CM chapter 20, V00-Y99) and manifestation codes may never be sequenced
first.
"""
from __future__ import annotations

from validation.model import ClaimProjection

from ..model import EditCategory, ScrubFinding, ScrubReport, ScrubSeverity

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
