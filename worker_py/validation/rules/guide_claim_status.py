"""276/277 implementation-guide validation — ASC X12N 005010X212
(Health Care Claim Status Request and Response).

276 is the claim status request; 277 is the response. The response carries
STC claim-status segments whose category code is governed by an external code
set (SNIP type 5).

Reference: ASC X12N 005010X212 (276/277) TR3.
"""
from __future__ import annotations

from ..codesets import get_codeset
from ..model import ClaimProjection, Severity, SnipType, ValidationIssue, ValidationReport
from ..parser import Transaction

_TR3 = "ASC X12N 005010X212 (276/277) TR3"


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


def _validate(txn: Transaction, report: ValidationReport, *, is_response: bool) -> None:
    label = "277" if is_response else "276"
    segs = txn.segments

    bht = txn.first("BHT")
    if bht is None:
        _issue(
            report, txn, SnipType.REQUIREMENT, Severity.ERROR, "REQ.BHT.MISSING",
            f"BHT segment is required in a {label} transaction.",
            segment_id="BHT",
        )
    elif bht.elem(1) != "0010":
        _issue(
            report, txn, SnipType.REQUIREMENT, Severity.ERROR, "REQ.BHT01.STRUCTURE",
            f"BHT01 must be 0010 for {label}; found '{bht.elem(1)}'.",
            segment_id="BHT", element=1,
        )

    if not any(s.seg_id == "NM1" and s.elem(1) == "PR" for s in segs):
        _issue(
            report, txn, SnipType.REQUIREMENT, Severity.ERROR, "REQ.NM1PR.MISSING",
            "Payer identification (NM1*PR) is required.",
            segment_id="NM1",
        )
    if not any(s.seg_id == "TRN" for s in segs):
        _issue(
            report, txn, SnipType.REQUIREMENT, Severity.ERROR, "REQ.TRN.MISSING",
            f"At least one TRN trace segment is required in a {label}.",
            segment_id="TRN",
        )

    if is_response:
        stc_segs = [s for s in segs if s.seg_id == "STC"]
        if not stc_segs:
            _issue(
                report, txn, SnipType.REQUIREMENT, Severity.ERROR, "REQ.STC.MISSING",
                "A 277 response must carry at least one STC claim-status segment.",
                segment_id="STC",
            )
        category_set = get_codeset("claim_status_category")
        for stc in stc_segs:
            category = stc.comp(1, 1)
            if category and not category_set.is_valid(category):
                _issue(
                    report, txn, SnipType.CODE_SET, Severity.WARNING,
                    "CODE.STC01.CATEGORY",
                    f"STC claim-status category code '{category}' was not found "
                    "in the bundled claim-status-category subset.",
                    segment_id="STC", element=1, actual=category,
                )
    else:
        # 276 requests should reference the claim being queried.
        if not any(s.seg_id == "REF" for s in segs):
            _issue(
                report, txn, SnipType.SITUATIONAL, Severity.WARNING, "SIT.REF.MISSING",
                "A 276 request normally carries a REF segment identifying the "
                "claim being queried.",
                segment_id="REF",
            )


def validate_276(txn: Transaction, report: ValidationReport) -> list[ClaimProjection]:
    """Validate one 276 claim status request transaction."""
    _validate(txn, report, is_response=False)
    return []


def validate_277(txn: Transaction, report: ValidationReport) -> list[ClaimProjection]:
    """Validate one 277 claim status response transaction."""
    _validate(txn, report, is_response=True)
    return []
