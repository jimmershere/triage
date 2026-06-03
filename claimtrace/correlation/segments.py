"""X12 segment extraction and stamping for Claimtrace metadata."""

from __future__ import annotations

import uuid
from typing import Iterable

from .transport import TraceContext


def _segments(x12_text: str) -> list[str]:
    return [segment.strip() for segment in x12_text.replace("\n", "").split("~") if segment.strip()]


def _element(segment: str, index: int) -> str | None:
    parts = segment.split("*")
    return parts[index] if len(parts) > index and parts[index] else None


def extract_trace(x12_text: str) -> TraceContext:
    correlation_ids: dict[str, object] = {}
    claim_id = ""
    claim_root_id = ""
    bundle_id = None
    trace_id = ""
    new_state_hash = ""
    prior_state_hash = None
    bundle_claim_ids: list[str] = []

    for segment in _segments(x12_text):
        parts = segment.split("*")
        tag = parts[0]
        if tag == "ISA":
            value = _element(segment, 13)
            if value:
                correlation_ids["isa_control"] = value
        elif tag == "GS":
            value = _element(segment, 6)
            if value:
                correlation_ids["gs_control"] = value
        elif tag == "ST":
            value = _element(segment, 2)
            if value:
                correlation_ids["st_control"] = value
        elif tag == "BHT":
            value = _element(segment, 3)
            if value:
                correlation_ids["bht_ref"] = value
        elif tag == "TRN":
            value = _element(segment, 2)
            if value:
                trace_id = trace_id or value
                correlation_ids["trn"] = value
        elif tag == "REF" and len(parts) >= 3:
            qualifier = parts[1]
            value = parts[2]
            if qualifier == "ZZ":
                claim_id = value
                correlation_ids["system_trace_ref"] = value
            elif qualifier == "CT":
                claim_root_id = value
            elif qualifier == "BT":
                bundle_id = value
            elif qualifier == "CH":
                bundle_claim_ids.append(value)
            elif qualifier == "SH":
                new_state_hash = value
            elif qualifier == "PH":
                prior_state_hash = value

    if not claim_root_id:
        claim_root_id = claim_id
    if bundle_claim_ids:
        correlation_ids["bundle_claim_ids"] = bundle_claim_ids
    return TraceContext(
        claim_id=claim_id,
        claim_root_id=claim_root_id,
        bundle_id=bundle_id,
        trace_id=trace_id or str(uuid.uuid4()),
        prior_state_hash=prior_state_hash,
        new_state_hash=new_state_hash or "unknown",
        correlation_ids=correlation_ids,
    )


def _stamp_segments(ctx: TraceContext) -> list[str]:
    stamped = [
        f"REF*ZZ*{ctx.claim_id}",
        f"REF*CT*{ctx.claim_root_id}",
        f"REF*SH*{ctx.new_state_hash}",
    ]
    if ctx.prior_state_hash:
        stamped.append(f"REF*PH*{ctx.prior_state_hash}")
    if ctx.bundle_id:
        stamped.append(f"REF*BT*{ctx.bundle_id}")
    for claim_id in ctx.correlation_ids.get("bundle_claim_ids", []) or []:
        stamped.append(f"REF*CH*{claim_id}")
    if not any(segment.startswith("TRN*") for segment in stamped):
        stamped.append(f"TRN*1*{ctx.trace_id}")
    return stamped


def stamp_trace(x12_text: str, ctx: TraceContext) -> str:
    segments = [
        segment
        for segment in _segments(x12_text)
        if not (
            segment.startswith("REF*ZZ*")
            or segment.startswith("REF*CT*")
            or segment.startswith("REF*BT*")
            or segment.startswith("REF*CH*")
            or segment.startswith("REF*SH*")
            or segment.startswith("REF*PH*")
        )
    ]
    insert_at = 1 if segments and segments[0].startswith("ST*") else len(segments)
    for idx, segment in enumerate(segments):
        if segment.startswith("CLM*"):
            insert_at = idx + 1
            break
    stamped = segments[:insert_at] + _stamp_segments(ctx) + segments[insert_at:]
    return "~".join(stamped) + "~"
