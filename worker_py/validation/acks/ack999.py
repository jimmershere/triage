"""999 Implementation Acknowledgment generation — ASC X12 005010X231A1.

The 999 reports, per functional group and per transaction set, whether the
transaction conformed to the HIPAA implementation guide, and itemizes
segment-level (IK3) and element-level (IK4) errors.

Reference: ASC X12 005010X231A1 Implementation Acknowledgment For Health Care
Insurance (999).
"""
from __future__ import annotations

from datetime import datetime

from ..model import Severity, SnipType, ValidationIssue, ValidationReport
from ..parser import FunctionalGroup, Transaction, X12Document
from .builder import X12Builder, now_stamp

_999_VERSION = "005010X231A1"

# Cap on IK3/IK4 detail groups emitted per transaction to bound 999 size.
_MAX_DETAIL_PER_TXN = 50


def _segment_error_code(issue: ValidationIssue) -> str:
    """Map an issue to an IK304 implementation segment syntax error code."""
    if "MISSING" in issue.code and issue.element_position is None:
        return "3"  # mandatory segment missing
    return "8"  # segment has data element errors


def _element_error_code(issue: ValidationIssue) -> str:
    """Map an issue to an IK403 implementation data element syntax error code."""
    if issue.snip_type == SnipType.CODE_SET:
        return "7"  # invalid code value
    if "MISSING" in issue.code:
        return "1"  # mandatory data element missing
    return "7"


def _txn_issues(report: ValidationReport, txn: Transaction) -> list[ValidationIssue]:
    return [
        i
        for i in report.issues
        if i.transaction_control == txn.control_number
        and i.transaction_set == txn.set_code
        and i.severity.rejects
    ]


def _emit_transaction(
    builder: X12Builder, txn: Transaction, issues: list[ValidationIssue], comp: str
) -> str:
    """Emit AK2 + IK3/IK4 + IK5 for one transaction. Returns the IK501 code."""
    builder.add("AK2", txn.set_code, txn.control_number, txn.implementation_version)

    for issue in issues[:_MAX_DETAIL_PER_TXN]:
        builder.add(
            "IK3",
            issue.segment_id or "",
            issue.segment_position if issue.segment_position is not None else "",
            issue.loop_id or "",
            _segment_error_code(issue),
        )
        if issue.element_position is not None:
            position = str(issue.element_position)
            if issue.component_position is not None:
                position = f"{issue.element_position}{comp}{issue.component_position}"
            builder.add(
                "IK4",
                position,
                "",  # IK402 data element reference number (situational)
                _element_error_code(issue),
                (issue.actual or "")[:99],
            )

    if not issues:
        ik501 = "A"
        builder.add("IK5", ik501)
    else:
        ik501 = "R"
        builder.add("IK5", ik501, "5")  # 5 = one or more segments in error
    return ik501


def _emit_group(
    builder: X12Builder, grp: FunctionalGroup, report: ValidationReport, comp: str
) -> None:
    """Emit one ST*999 acknowledging a single original functional group."""
    builder.add(
        "AK1",
        grp.functional_id or "HC",
        grp.control_number,
        grp.version or "",
    )

    accepted = 0
    rejected = 0
    for txn in grp.transactions:
        issues = _txn_issues(report, txn)
        ik501 = _emit_transaction(builder, txn, issues, comp)
        if ik501 == "A":
            accepted += 1
        else:
            rejected += 1

    received = len(grp.transactions)
    if rejected == 0:
        ak901 = "A"
    elif accepted == 0:
        ak901 = "R"
    else:
        ak901 = "P"  # partially accepted
    builder.add(
        "AK9",
        ak901,
        str(grp.declared_transaction_count or received),
        str(received),
        str(accepted),
    )


def generate_999(
    doc: X12Document,
    report: ValidationReport,
    *,
    now: datetime | None = None,
    control: str = "000000001",
) -> str:
    """Generate a 999 implementation acknowledgment for ``doc``."""
    isa_date, gs_date, time_, _ = now_stamp(now)
    builder = X12Builder(doc.delimiters)
    comp = doc.delimiters.component

    first = doc.interchanges[0] if doc.interchanges else None
    sender = (first.receiver_id if first else "") or "TURBOHEDI"
    receiver = (first.sender_id if first else "") or "UNKNOWN"
    usage = (first.usage_indicator if first else "") or "P"

    builder.interchange(
        sender=sender,
        receiver=receiver,
        control=control,
        date=isa_date,
        time=time_,
        usage=usage,
    )
    builder.group(
        functional_id="FA",
        sender=sender,
        receiver=receiver,
        date=gs_date,
        time=time_,
        control=control.lstrip("0") or "1",
        version=_999_VERSION,
    )

    st_counter = 0
    groups = [grp for ic in doc.interchanges for grp in ic.groups]
    if not groups:
        st_counter += 1
        builder.transaction(
            set_code="999", control=str(st_counter).rjust(4, "0"), version=_999_VERSION
        )
        builder.add("AK1", "HC", "", "")
        builder.add("AK9", "R", "0", "0", "0")
        builder.end_transaction()
    else:
        for grp in groups:
            st_counter += 1
            builder.transaction(
                set_code="999",
                control=str(st_counter).rjust(4, "0"),
                version=_999_VERSION,
            )
            _emit_group(builder, grp, report, comp)
            builder.end_transaction()

    builder.end_group()
    builder.end_interchange()
    return builder.render()
