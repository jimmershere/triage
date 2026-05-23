"""278 implementation-guide validation — ASC X12N 005010X217
(Health Care Services Review — Request for Review and Response / prior auth).

The 278 carries one or more UM (Health Care Services Review Information)
segments inside the UMO / requester / subscriber / dependent / event / service
hierarchical loops. This guide performs envelope-aware requirement checks.

Reference: ASC X12N 005010X217 (278) TR3.
"""
from __future__ import annotations

from ..model import ClaimProjection, Severity, SnipType, ValidationIssue, ValidationReport
from ..parser import Transaction

_TR3 = "ASC X12N 005010X217 (278) TR3"

# UM01 request category codes (health care services review).
_UM_REQUEST_CATEGORIES = {"AR", "HS", "SC", "IN", "CT"}


def _issue(
    report: ValidationReport,
    txn: Transaction,
    snip: SnipType,
    severity: Severity,
    code: str,
    message: str,
    *,
    segment_id: str | None = None,
    element: int | None = None,
    actual: str | None = None,
) -> None:
    report.add(
        ValidationIssue(
            snip_type=snip,
            severity=severity,
            code=code,
            message=message,
            segment_id=segment_id,
            element_position=element,
            transaction_set=txn.set_code,
            transaction_control=txn.control_number,
            actual=actual,
            spec_ref=_TR3,
        )
    )


def validate_278(txn: Transaction, report: ValidationReport) -> list[ClaimProjection]:
    """Validate one 278 health care services review transaction."""
    segs = txn.segments

    bht = txn.first("BHT")
    if bht is None:
        _issue(
            report, txn, SnipType.REQUIREMENT, Severity.ERROR, "REQ.BHT.MISSING",
            "BHT segment is required in a 278 transaction.",
            segment_id="BHT",
        )
    elif bht.elem(1) != "0007":
        _issue(
            report, txn, SnipType.REQUIREMENT, Severity.ERROR, "REQ.BHT01.STRUCTURE",
            f"BHT01 must be 0007 for a 278; found '{bht.elem(1)}'.",
            segment_id="BHT", element=1,
        )

    if not any(s.seg_id == "HL" and s.elem(3) == "20" for s in segs):
        _issue(
            report, txn, SnipType.REQUIREMENT, Severity.ERROR, "REQ.HL20.MISSING",
            "Utilization Management Organization level (HL*..*20) is required.",
            segment_id="HL",
        )

    um_segs = [s for s in segs if s.seg_id == "UM"]
    if not um_segs:
        _issue(
            report, txn, SnipType.REQUIREMENT, Severity.ERROR, "REQ.UM.MISSING",
            "A 278 must carry at least one UM health-care-services-review "
            "segment.",
            segment_id="UM",
        )
    for um in um_segs:
        category = um.elem(1)
        if category and category not in _UM_REQUEST_CATEGORIES:
            _issue(
                report, txn, SnipType.CODE_SET, Severity.WARNING, "CODE.UM01.CATEGORY",
                f"UM01 request category code '{category}' is unusual "
                f"(expected one of {', '.join(sorted(_UM_REQUEST_CATEGORIES))}).",
                segment_id="UM", element=1, actual=category,
            )

    if not any(s.seg_id == "NM1" for s in segs):
        _issue(
            report, txn, SnipType.REQUIREMENT, Severity.ERROR, "REQ.NM1.MISSING",
            "A 278 must identify its entities with NM1 segments.",
            segment_id="NM1",
        )

    return []
