"""Package a human-readable rejection report for a submitter ("notify submitter").

This is the contract Workstream 4 expects from Workstream 7's rejection-report /
999 / 277CA events (built on another branch). Triage consumes those as Claimtrace
events and packages them into a flat, human-readable report the submitter's
billing or software team can act on.

PHI safety: the report carries reference identifiers (claim id, claim hash,
control numbers) and the *location* of the error (segment / element / loop) plus
the standard error code and message — never clinical content or member detail.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RejectionItem:
    """One reported error: where it is and what is wrong (no PHI)."""

    code: str = ""
    message: str = ""
    segment_id: str = ""
    element_position: str = ""
    loop_id: str = ""
    bad_data: str = ""  # echo of offending element value; caller must keep PHI-free

    def location(self) -> str:
        bits = []
        if self.loop_id:
            bits.append(f"loop {self.loop_id}")
        if self.segment_id:
            seg = self.segment_id
            if self.element_position:
                seg = f"{seg}{self.element_position}"
            bits.append(f"segment {seg}")
        return ", ".join(bits) or "unspecified location"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "segment_id": self.segment_id,
            "element_position": self.element_position,
            "loop_id": self.loop_id,
            "bad_data": self.bad_data,
            "location": self.location(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RejectionItem":
        return cls(
            code=str(data.get("code", "")),
            message=str(data.get("message", "")),
            segment_id=str(data.get("segment_id", "") or data.get("segment", "")),
            element_position=str(data.get("element_position", "") or data.get("element", "")),
            loop_id=str(data.get("loop_id", "") or data.get("loop", "")),
            bad_data=str(data.get("bad_data", "")),
        )


@dataclass
class RejectionContext:
    """Reference-only context tying a report to a claim and its rejection event."""

    claim_id: str = ""
    claim_hash_id: str = ""
    trading_partner_id: str = ""
    submitter_id: str = ""
    transaction_set: str = "837"
    ack_type: str = "999"  # 999 (syntax/IG) or 277CA (claim data)
    interchange_control: str = ""
    group_control: str = ""
    transaction_control: str = ""
    file_reference: str = ""
    correlation_id: str = ""
    items: list[RejectionItem] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RejectionContext":
        ctx = cls(
            claim_id=str(data.get("claim_id", "")),
            claim_hash_id=str(data.get("claim_hash_id", "")),
            trading_partner_id=str(data.get("trading_partner_id", "")),
            submitter_id=str(data.get("submitter_id", "")),
            transaction_set=str(data.get("transaction_set", "837")),
            ack_type=str(data.get("ack_type", "999")),
            interchange_control=str(data.get("interchange_control", "")),
            group_control=str(data.get("group_control", "")),
            transaction_control=str(data.get("transaction_control", "")),
            file_reference=str(data.get("file_reference", "")),
            correlation_id=str(data.get("correlation_id", "")),
        )
        ctx.items = [RejectionItem.from_dict(i) for i in data.get("items", [])]
        return ctx


_AUDIENCE = {
    "999": "your EDI software team (syntax / implementation-guide problem)",
    "277CA": "your billing team (claim-level data problem)",
}


def build_rejection_report(
    ctx: RejectionContext,
    *,
    generated_at: Optional[str] = None,
) -> dict[str, Any]:
    """Build a flat, human-readable rejection report plus a structured payload.

    Returns ``{"text": ..., "summary": ..., "items": [...], ...}``. The ``text``
    field is ready to package and send to the submitter; the structured fields
    feed the helpdesk UI and PHI-safe audit log.
    """
    audience = _AUDIENCE.get(ctx.ack_type.upper(), "the submitting party")
    lines: list[str] = []
    lines.append("TRIAGE CLAIM REJECTION REPORT")
    lines.append("=" * 60)
    lines.append(f"Acknowledgement type : {ctx.ack_type.upper()}")
    lines.append(f"Transaction set      : {ctx.transaction_set}")
    if ctx.trading_partner_id:
        lines.append(f"Trading partner      : {ctx.trading_partner_id}")
    if ctx.submitter_id:
        lines.append(f"Submitter            : {ctx.submitter_id}")
    if ctx.claim_id:
        lines.append(f"Claim reference      : {ctx.claim_id}")
    if ctx.claim_hash_id:
        lines.append(f"Claim hash           : {ctx.claim_hash_id}")
    control = " / ".join(
        bit
        for bit in (ctx.interchange_control, ctx.group_control, ctx.transaction_control)
        if bit
    )
    if control:
        lines.append(f"Control (ISA/GS/ST)  : {control}")
    if ctx.file_reference:
        lines.append(f"Source file          : {ctx.file_reference}")
    if generated_at:
        lines.append(f"Generated            : {generated_at}")
    lines.append("")
    lines.append(f"This claim was rejected and must be corrected by {audience}.")
    lines.append("Triage does not modify your data — please correct and resubmit.")
    lines.append("")
    lines.append(f"Errors reported ({len(ctx.items)}):")
    if not ctx.items:
        lines.append("  (no itemised errors were supplied with this rejection)")
    for idx, item in enumerate(ctx.items, start=1):
        lines.append(f"  {idx}. [{item.code or 'N/A'}] {item.location()}")
        if item.message:
            lines.append(f"     {item.message}")
        if item.bad_data:
            lines.append(f"     reported value: {item.bad_data}")
    lines.append("")
    lines.append("Reply to this report after resubmitting; Triage will close the")
    lines.append("case automatically once the corrected claim passes validation.")

    text = "\n".join(lines)
    summary = (
        f"{ctx.ack_type.upper()} rejection for claim {ctx.claim_id or '(unknown)'}: "
        f"{len(ctx.items)} error(s)."
    )
    return {
        "text": text,
        "summary": summary,
        "audience": audience,
        "ack_type": ctx.ack_type.upper(),
        "claim_id": ctx.claim_id,
        "claim_hash_id": ctx.claim_hash_id,
        "trading_partner_id": ctx.trading_partner_id,
        "submitter_id": ctx.submitter_id,
        "correlation_id": ctx.correlation_id,
        "item_count": len(ctx.items),
        "items": [item.to_dict() for item in ctx.items],
    }
