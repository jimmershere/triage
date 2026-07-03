"""Member eligibility-on-date-of-service edits.

Confirms each claim's subscriber was covered on the date of service. The
bundled roster is a demo artifact — production deployments should pass a live
eligibility roster (sourced from 270/271 transactions) via the ``roster``
context argument.
"""
from __future__ import annotations

from validation.model import ClaimProjection

from ..model import EditCategory, ScrubFinding, ScrubReport, ScrubSeverity, parse_x12_date
from ..tables import load_table


def _claim_service_date(claim: ClaimProjection) -> str | None:
    for line in claim.service_lines:
        if line.service_date:
            return line.service_date
    return claim.statement_from_date


def _covered(spans: list[dict], dos: str) -> bool:
    dos_date = parse_x12_date(dos)
    if dos_date is None:
        return False
    for span in spans:
        start = parse_x12_date(span.get("start"))
        end = parse_x12_date(span.get("end"))
        # An open-ended span (missing/blank end date — common in active 271
        # coverage) must still count as covered; the old `start and end` guard
        # treated it as not-covered and produced false ELIG.NOT_ELIGIBLE denials.
        if start is None or dos_date < start:
            continue
        if end is not None and dos_date > end:
            continue
        return True
    return False


def apply(
    claims: list[ClaimProjection],
    report: ScrubReport,
    *,
    roster: dict[str, list[dict]] | None = None,
    **_ctx,
) -> None:
    if roster is None:
        roster = load_table("member_eligibility").get("members", {})
    if not roster:
        return

    for claim in claims:
        member = claim.subscriber_id
        dos = _claim_service_date(claim)
        if not member:
            continue

        spans = roster.get(member)
        if spans is None:
            report.add(
                ScrubFinding(
                    category=EditCategory.ELIGIBILITY,
                    severity=ScrubSeverity.ADVISORY,
                    code="ELIG.NOT_VERIFIED",
                    message=(
                        f"Member {member} was not found in the eligibility "
                        "roster; coverage on the date of service could not be "
                        "verified."
                    ),
                    claim_id=claim.claim_id,
                    resolution="Run a 270/271 eligibility inquiry for this member.",
                    source="Eligibility roster",
                )
            )
            continue

        if not dos:
            continue
        if not _covered(spans, dos):
            report.add(
                ScrubFinding(
                    category=EditCategory.ELIGIBILITY,
                    severity=ScrubSeverity.DENY,
                    code="ELIG.NOT_ELIGIBLE_ON_DOS",
                    message=(
                        f"Member {member} was not eligible on the date of "
                        f"service ({dos}); no active coverage span covers that "
                        "date."
                    ),
                    claim_id=claim.claim_id,
                    resolution=(
                        "Verify the date of service and the member's coverage; "
                        "bill the correct payer or the patient as appropriate."
                    ),
                    source="Eligibility roster",
                )
            )
