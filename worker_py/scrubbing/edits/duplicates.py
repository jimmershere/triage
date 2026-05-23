"""Duplicate-service detection edits.

Two checks:
- **Cross-claim duplicate** — the same billing provider reporting the same
  procedure for the same patient on the same date of service across claims.
- **Intra-claim duplicate** — the same procedure/date repeated on two service
  lines of one claim.
"""
from __future__ import annotations

from validation.model import ClaimProjection

from ..model import EditCategory, ScrubFinding, ScrubReport, ScrubSeverity


def apply(claims: list[ClaimProjection], report: ScrubReport, **_ctx) -> None:
    # key -> (claim_id, line_no) of the first occurrence.
    seen: dict[tuple[str, str, str, str], tuple[str, str]] = {}

    for claim in claims:
        patient = claim.subscriber_id or claim.patient_control_number or ""
        provider = claim.billing_provider_npi or ""
        intra: set[tuple[str, str]] = set()

        for line in claim.service_lines:
            proc = line.procedure_code
            if not proc:
                continue
            dos = line.service_date or claim.statement_from_date or ""

            intra_key = (proc, dos)
            if intra_key in intra:
                report.add(
                    ScrubFinding(
                        category=EditCategory.DUPLICATE,
                        severity=ScrubSeverity.REVIEW,
                        code="DUP.INTRA_CLAIM",
                        message=(
                            f"Procedure {proc} on {dos or 'the service date'} "
                            "appears on more than one line of this claim."
                        ),
                        claim_id=claim.claim_id,
                        line_no=line.line_no,
                        procedure_code=proc,
                        resolution=(
                            "Consolidate the lines and bill the correct unit "
                            "count, or append a modifier if the services are "
                            "genuinely distinct."
                        ),
                        source="Duplicate detection",
                    )
                )
            intra.add(intra_key)

            key = (provider, patient, proc, dos)
            if key in seen and seen[key][0] != (claim.claim_id or ""):
                prior_claim, _ = seen[key]
                report.add(
                    ScrubFinding(
                        category=EditCategory.DUPLICATE,
                        severity=ScrubSeverity.DENY,
                        code="DUP.CROSS_CLAIM",
                        message=(
                            f"Procedure {proc} for patient {patient or 'unknown'} "
                            f"on {dos or 'the service date'} duplicates a service "
                            f"already billed on claim {prior_claim}."
                        ),
                        claim_id=claim.claim_id,
                        line_no=line.line_no,
                        procedure_code=proc,
                        related_code=None,
                        resolution=(
                            "Confirm this is not a resubmission; if it is a "
                            "correction, use frequency code 7 and reference the "
                            "original claim."
                        ),
                        source="Duplicate detection",
                    )
                )
            else:
                seen[key] = (claim.claim_id or "", line.line_no or "")
