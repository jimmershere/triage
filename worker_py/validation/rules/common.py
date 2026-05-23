"""Envelope-level validation — SNIP types 1 (integrity), 2 (requirement),
and 3 (balancing) for the ISA/IEA, GS/GE and ST/SE control structures.

These rules apply to every HIPAA X12 transaction regardless of transaction set.
"""
from __future__ import annotations

from ..model import Severity, SnipType, ValidationIssue, ValidationReport
from ..parser import FunctionalGroup, Interchange, Transaction, X12Document

_ISA_SPEC = "X12 005010 Appendix B — Interchange Control Structure"
_GS_SPEC = "X12 005010 Appendix B — Functional Group Structure"
_ST_SPEC = "X12 005010 — Transaction Set Control Structure"

# Valid GS01 functional identifier codes for HIPAA healthcare transactions.
_HEALTHCARE_FUNCTIONAL_IDS = {
    "HC": "Health Care Claim (837)",
    "HP": "Health Care Claim Payment/Advice (835)",
    "HB": "Eligibility/Benefit Inquiry (270)",
    "HS": "Eligibility/Benefit Response (271)",
    "HR": "Health Care Claim Status Request (276)",
    "HN": "Health Care Claim Status Notification (277)",
    "HI": "Health Care Services Review (278)",
    "BE": "Benefit Enrollment and Maintenance (834)",
    "FA": "Functional Acknowledgment (999/997)",
}


def validate_envelopes(doc: X12Document, report: ValidationReport) -> None:
    """Run all envelope-level checks for a parsed document."""
    # Surface any structural parse issues collected by the parser.
    for issue in doc.parse_issues:
        report.add(issue)

    if not doc.interchanges:
        report.add(
            ValidationIssue(
                snip_type=SnipType.INTEGRITY,
                severity=Severity.FATAL,
                code="ENV.ISA.MISSING",
                message="No ISA interchange envelope found in the document.",
                segment_id="ISA",
                spec_ref=_ISA_SPEC,
            )
        )
        return

    for ic in doc.interchanges:
        _validate_interchange(ic, report)


def _validate_interchange(ic: Interchange, report: ValidationReport) -> None:
    if ic.isa is None:
        report.add(
            ValidationIssue(
                snip_type=SnipType.REQUIREMENT,
                severity=Severity.ERROR,
                code="ENV.ISA.MISSING",
                message="Functional group present without an ISA interchange header.",
                segment_id="ISA",
                spec_ref=_ISA_SPEC,
            )
        )
    else:
        isa = ic.isa
        # SNIP 1 — ISA is a fixed-length structure with 16 elements.
        if isa.max_element < 16:
            report.add(
                ValidationIssue(
                    snip_type=SnipType.INTEGRITY,
                    severity=Severity.ERROR,
                    code="ENV.ISA.ELEMENTS",
                    message=(
                        f"ISA must carry 16 elements; found {isa.max_element}."
                    ),
                    segment_id="ISA",
                    spec_ref=_ISA_SPEC,
                )
            )
        # SNIP 5/2 — usage indicator must be P or T.
        usage = isa.elem(15).strip()
        if usage and usage not in ("P", "T"):
            report.add(
                ValidationIssue(
                    snip_type=SnipType.CODE_SET,
                    severity=Severity.ERROR,
                    code="ENV.ISA15.USAGE",
                    message=f"ISA15 usage indicator must be P or T; found '{usage}'.",
                    segment_id="ISA",
                    element_position=15,
                    expected="P or T",
                    actual=usage,
                    spec_ref=_ISA_SPEC,
                )
            )

    if ic.iea is None:
        report.add(
            ValidationIssue(
                snip_type=SnipType.REQUIREMENT,
                severity=Severity.ERROR,
                code="ENV.IEA.MISSING",
                message="Interchange is not closed by an IEA trailer.",
                segment_id="IEA",
                spec_ref=_ISA_SPEC,
            )
        )

    # SNIP 1 — interchange control numbers must match (ISA13 == IEA02).
    if ic.isa is not None and ic.iea is not None:
        isa13 = ic.isa.elem(13).strip()
        iea02 = ic.iea.elem(2).strip()
        if isa13 != iea02:
            report.add(
                ValidationIssue(
                    snip_type=SnipType.INTEGRITY,
                    severity=Severity.ERROR,
                    code="ENV.ISA.CONTROL",
                    message=(
                        f"Interchange control number mismatch: ISA13='{isa13}' "
                        f"!= IEA02='{iea02}'."
                    ),
                    segment_id="ISA/IEA",
                    expected=isa13,
                    actual=iea02,
                    spec_ref=_ISA_SPEC,
                )
            )

    # SNIP 3 — IEA01 must equal the actual functional group count.
    declared_groups = ic.declared_group_count
    actual_groups = len(ic.groups)
    if declared_groups is not None and declared_groups != actual_groups:
        report.add(
            ValidationIssue(
                snip_type=SnipType.BALANCING,
                severity=Severity.ERROR,
                code="BAL.IEA01.GROUP_COUNT",
                message=(
                    f"IEA01 declares {declared_groups} functional group(s) but "
                    f"{actual_groups} were found."
                ),
                segment_id="IEA",
                element_position=1,
                expected=str(declared_groups),
                actual=str(actual_groups),
                spec_ref=_ISA_SPEC,
            )
        )

    if not ic.groups:
        report.add(
            ValidationIssue(
                snip_type=SnipType.REQUIREMENT,
                severity=Severity.ERROR,
                code="ENV.GS.MISSING",
                message="Interchange contains no functional group (GS..GE).",
                segment_id="GS",
                spec_ref=_GS_SPEC,
            )
        )

    for grp in ic.groups:
        _validate_group(grp, report)


def _validate_group(grp: FunctionalGroup, report: ValidationReport) -> None:
    if grp.gs is None:
        report.add(
            ValidationIssue(
                snip_type=SnipType.REQUIREMENT,
                severity=Severity.ERROR,
                code="ENV.GS.MISSING",
                message="Transactions present without a GS functional group header.",
                segment_id="GS",
                spec_ref=_GS_SPEC,
            )
        )
    else:
        # SNIP 5 — GS01 functional identifier must be a known code.
        fid = grp.functional_id
        if fid and fid not in _HEALTHCARE_FUNCTIONAL_IDS:
            report.add(
                ValidationIssue(
                    snip_type=SnipType.CODE_SET,
                    severity=Severity.WARNING,
                    code="ENV.GS01.FUNCTIONAL_ID",
                    message=(
                        f"GS01 functional identifier '{fid}' is not a recognized "
                        "HIPAA healthcare functional group code."
                    ),
                    segment_id="GS",
                    element_position=1,
                    actual=fid,
                    spec_ref=_GS_SPEC,
                )
            )

    if grp.ge is None:
        report.add(
            ValidationIssue(
                snip_type=SnipType.REQUIREMENT,
                severity=Severity.ERROR,
                code="ENV.GE.MISSING",
                message="Functional group is not closed by a GE trailer.",
                segment_id="GE",
                spec_ref=_GS_SPEC,
            )
        )

    # SNIP 1 — group control numbers must match (GS06 == GE02).
    if grp.gs is not None and grp.ge is not None:
        gs06 = grp.gs.elem(6).strip()
        ge02 = grp.ge.elem(2).strip()
        if gs06 != ge02:
            report.add(
                ValidationIssue(
                    snip_type=SnipType.INTEGRITY,
                    severity=Severity.ERROR,
                    code="ENV.GS.CONTROL",
                    message=(
                        f"Functional group control number mismatch: GS06='{gs06}' "
                        f"!= GE02='{ge02}'."
                    ),
                    segment_id="GS/GE",
                    expected=gs06,
                    actual=ge02,
                    spec_ref=_GS_SPEC,
                )
            )

    # SNIP 3 — GE01 must equal the actual transaction count.
    declared = grp.declared_transaction_count
    actual = len(grp.transactions)
    if declared is not None and declared != actual:
        report.add(
            ValidationIssue(
                snip_type=SnipType.BALANCING,
                severity=Severity.ERROR,
                code="BAL.GE01.TXN_COUNT",
                message=(
                    f"GE01 declares {declared} transaction set(s) but {actual} "
                    "were found."
                ),
                segment_id="GE",
                element_position=1,
                expected=str(declared),
                actual=str(actual),
                spec_ref=_GS_SPEC,
            )
        )

    if not grp.transactions:
        report.add(
            ValidationIssue(
                snip_type=SnipType.REQUIREMENT,
                severity=Severity.ERROR,
                code="ENV.ST.MISSING",
                message="Functional group contains no transaction set (ST..SE).",
                segment_id="ST",
                spec_ref=_ST_SPEC,
            )
        )

    for txn in grp.transactions:
        _validate_transaction_envelope(txn, report)


def _validate_transaction_envelope(txn: Transaction, report: ValidationReport) -> None:
    if txn.se is None:
        report.add(
            ValidationIssue(
                snip_type=SnipType.REQUIREMENT,
                severity=Severity.ERROR,
                code="ENV.SE.MISSING",
                message="Transaction set is not closed by an SE trailer.",
                segment_id="SE",
                transaction_set=txn.set_code,
                transaction_control=txn.control_number,
                spec_ref=_ST_SPEC,
            )
        )

    # SNIP 1 — transaction control numbers must match (ST02 == SE02).
    if txn.st is not None and txn.se is not None:
        st02 = txn.st.elem(2).strip()
        se02 = txn.se.elem(2).strip()
        if st02 != se02:
            report.add(
                ValidationIssue(
                    snip_type=SnipType.INTEGRITY,
                    severity=Severity.ERROR,
                    code="ENV.ST.CONTROL",
                    message=(
                        f"Transaction control number mismatch: ST02='{st02}' "
                        f"!= SE02='{se02}'."
                    ),
                    segment_id="ST/SE",
                    transaction_set=txn.set_code,
                    transaction_control=txn.control_number,
                    expected=st02,
                    actual=se02,
                    spec_ref=_ST_SPEC,
                )
            )

    # SNIP 3 — SE01 must equal the actual ST..SE inclusive segment count.
    declared = txn.declared_segment_count
    actual = txn.actual_segment_count
    if declared is not None and declared != actual:
        report.add(
            ValidationIssue(
                snip_type=SnipType.BALANCING,
                severity=Severity.ERROR,
                code="BAL.SE01.SEG_COUNT",
                message=(
                    f"SE01 declares {declared} segment(s) but the transaction "
                    f"contains {actual}."
                ),
                segment_id="SE",
                element_position=1,
                transaction_set=txn.set_code,
                transaction_control=txn.control_number,
                expected=str(declared),
                actual=str(actual),
                spec_ref=_ST_SPEC,
            )
        )
