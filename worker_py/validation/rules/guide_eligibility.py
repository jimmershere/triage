"""270/271 implementation-guide validation — ASC X12N 005010X279A1
(Health Care Eligibility Benefit Inquiry and Response).

270 is the eligibility inquiry; 271 is the response. Both use the same
Information Source / Information Receiver / Subscriber / Dependent hierarchical
loop structure — a 270 carries EQ inquiry segments, a 271 carries EB benefit
segments (and AAA request-validation segments).

Reference: ASC X12N 005010X279A1 (270/271) TR3.
"""
from __future__ import annotations

from ..model import ClaimProjection, Severity, SnipType, ValidationIssue, ValidationReport
from ..parser import Transaction

_TR3 = "ASC X12N 005010X279A1 (270/271) TR3"


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
            spec_ref=_TR3,
        )
    )


def _validate(txn: Transaction, report: ValidationReport, *, is_response: bool) -> None:
    label = "271" if is_response else "270"
    segs = txn.segments

    bht = txn.first("BHT")
    if bht is None:
        _issue(
            report, txn, SnipType.REQUIREMENT, Severity.ERROR, "REQ.BHT.MISSING",
            f"BHT segment is required in a {label} transaction.",
            segment_id="BHT",
        )
    else:
        if bht.elem(1) != "0022":
            _issue(
                report, txn, SnipType.REQUIREMENT, Severity.ERROR,
                "REQ.BHT01.STRUCTURE",
                f"BHT01 must be 0022 for {label}; found '{bht.elem(1)}'.",
                segment_id="BHT", element=1,
            )
        expected_purpose = "11" if is_response else "13"
        if bht.elem(2) and bht.elem(2) != expected_purpose:
            _issue(
                report, txn, SnipType.SITUATIONAL, Severity.WARNING,
                "SIT.BHT02.PURPOSE",
                f"BHT02 should be {expected_purpose} for {label}; found "
                f"'{bht.elem(2)}'.",
                segment_id="BHT", element=2,
            )

    # Hierarchical-loop presence.
    if not any(s.seg_id == "HL" and s.elem(3) == "20" for s in segs):
        _issue(
            report, txn, SnipType.REQUIREMENT, Severity.ERROR, "REQ.HL20.MISSING",
            "Information Source hierarchical level (HL*..*20) is required.",
            segment_id="HL",
        )
    if not any(s.seg_id == "NM1" and s.elem(1) == "PR" for s in segs):
        _issue(
            report, txn, SnipType.REQUIREMENT, Severity.ERROR, "REQ.NM1PR.MISSING",
            "Loop 2100A payer / information source (NM1*PR) is required.",
            segment_id="NM1",
        )
    if not any(s.seg_id == "NM1" and s.elem(1) == "IL" for s in segs):
        _issue(
            report, txn, SnipType.REQUIREMENT, Severity.ERROR, "REQ.NM1IL.MISSING",
            "Loop 2100C subscriber name (NM1*IL) is required.",
            segment_id="NM1",
        )

    if is_response:
        if not any(s.seg_id == "EB" for s in segs):
            _issue(
                report, txn, SnipType.REQUIREMENT, Severity.ERROR, "REQ.EB.MISSING",
                "A 271 response must carry at least one EB eligibility/benefit "
                "segment.",
                segment_id="EB",
            )
    else:
        if not any(s.seg_id == "EQ" for s in segs):
            _issue(
                report, txn, SnipType.REQUIREMENT, Severity.ERROR, "REQ.EQ.MISSING",
                "A 270 inquiry must carry at least one EQ eligibility/benefit "
                "inquiry segment.",
                segment_id="EQ",
            )


def validate_270(txn: Transaction, report: ValidationReport) -> list[ClaimProjection]:
    """Validate one 270 eligibility inquiry transaction."""
    _validate(txn, report, is_response=False)
    return []


def validate_271(txn: Transaction, report: ValidationReport) -> list[ClaimProjection]:
    """Validate one 271 eligibility response transaction."""
    _validate(txn, report, is_response=True)
    return []
