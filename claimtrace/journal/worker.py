"""Worker helper that appends journal events around processing."""

from __future__ import annotations

from typing import Any, Callable

from claimtrace.common.hashing import hash_payload
from claimtrace.correlation.transport import TraceContext

from .store import ClaimEvent, JournalStore, OperationType


def append_processing_event(
    store: JournalStore,
    *,
    ctx: TraceContext,
    payload_bytes: bytes,
    payload_location: str,
    operation_type: OperationType,
    service_name: str,
    correlation_ids: dict[str, Any] | None = None,
) -> ClaimEvent:
    event = ClaimEvent(
        claim_id=ctx.claim_id,
        bundle_id=ctx.bundle_id,
        prior_state_hash=ctx.prior_state_hash,
        new_state_hash=hash_payload(payload_bytes),
        payload_location=payload_location,
        operation_type=operation_type,
        service_name=service_name,
        correlation_ids={**ctx.correlation_ids, **(correlation_ids or {})},
    )
    store.append_event(event)
    return event


def process_with_journal(
    store: JournalStore,
    *,
    ctx: TraceContext,
    inbound_payload: bytes,
    payload_location: str,
    operation_type: OperationType,
    service_name: str,
    processor: Callable[[bytes], bytes],
) -> tuple[bytes, ClaimEvent]:
    outbound = processor(inbound_payload)
    event = append_processing_event(
        store,
        ctx=ctx,
        payload_bytes=outbound,
        payload_location=payload_location,
        operation_type=operation_type,
        service_name=service_name,
    )
    return outbound, event
