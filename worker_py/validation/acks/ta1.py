"""TA1 Interchange Acknowledgment generation.

A TA1 reports on the syntactic integrity of the ISA/IEA interchange envelope
itself — it is produced before (and independently of) any functional or
implementation acknowledgment.

Reference: X12 005010 Appendix B — Interchange Acknowledgment, TA1 segment.
"""
from __future__ import annotations

from datetime import datetime

from ..model import ValidationReport
from ..parser import Interchange, X12Document
from .builder import X12Builder, now_stamp

# TA105 interchange note codes.
_NOTE_NO_ERROR = "000"
_NOTE_CONTROL_MISMATCH = "001"
_NOTE_GROUP_COUNT = "021"
_NOTE_MISSING_TRAILER = "024"


def _evaluate(ic: Interchange) -> tuple[str, str]:
    """Return (TA104 ack code, TA105 note code) for one interchange."""
    if ic.iea is None:
        return "R", _NOTE_MISSING_TRAILER

    isa13 = ic.isa.elem(13).strip() if ic.isa else ""
    iea02 = ic.iea.elem(2).strip()
    if isa13 and iea02 and isa13 != iea02:
        return "R", _NOTE_CONTROL_MISMATCH

    declared = ic.declared_group_count
    if declared is not None and declared != len(ic.groups):
        return "E", _NOTE_GROUP_COUNT

    return "A", _NOTE_NO_ERROR


def generate_ta1(
    doc: X12Document,
    report: ValidationReport,
    *,
    now: datetime | None = None,
    control: str = "000000001",
) -> str:
    """Generate a TA1 interchange acknowledgment for every interchange in ``doc``.

    Returns one reply interchange containing a TA1 segment per acknowledged
    interchange. Sender/receiver identifiers are role-swapped from the original.
    """
    isa_date, _, isa_time, _ = now_stamp(now)
    builder = X12Builder(doc.delimiters)

    if not doc.interchanges or doc.interchanges[0].isa is None:
        # No usable ISA header — emit a rejecting TA1 with empty references.
        builder.interchange(
            sender="TURBOHEDI",
            receiver="UNKNOWN",
            control=control,
            date=isa_date,
            time=isa_time,
        )
        builder.add("TA1", "", "", "", "R", _NOTE_MISSING_TRAILER)
        builder.end_interchange()
        return builder.render()

    first = doc.interchanges[0]
    builder.interchange(
        sender=(first.receiver_id or "TURBOHEDI"),
        receiver=(first.sender_id or "UNKNOWN"),
        control=control,
        date=isa_date,
        time=isa_time,
        usage=(first.usage_indicator or "P"),
    )

    for ic in doc.interchanges:
        ack_code, note_code = _evaluate(ic)
        orig_control = ic.isa.elem(13).strip() if ic.isa else ""
        orig_date = ic.isa.elem(9).strip() if ic.isa else ""
        orig_time = ic.isa.elem(10).strip() if ic.isa else ""
        builder.add("TA1", orig_control, orig_date, orig_time, ack_code, note_code)

    builder.end_interchange()
    return builder.render()
