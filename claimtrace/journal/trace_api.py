"""Claimtrace journal query and replay helpers."""

from __future__ import annotations

from typing import Any

from .store import ClaimEvent, JournalStore


def _related_claims(events: list[ClaimEvent], claim_id: str) -> set[str]:
    related = {claim_id}
    changed = True
    while changed:
        changed = False
        for event in events:
            parent = event.correlation_ids.get("parent")
            children = set(event.correlation_ids.get("children", []) or [])
            if event.claim_id in related or parent in related or related.intersection(children):
                before = len(related)
                related.add(event.claim_id)
                if parent:
                    related.add(str(parent))
                related.update(str(child) for child in children)
                changed = changed or len(related) > before
    return related


def get_trace(store: JournalStore, *, claim_id: str | None = None, bundle_id: str | None = None) -> list[ClaimEvent]:
    if not claim_id and not bundle_id:
        raise ValueError("claim_id or bundle_id is required")
    events = store.events()
    if claim_id:
        related = _related_claims(events, claim_id)
        return [
            event
            for event in events
            if event.claim_id in related
            or event.correlation_ids.get("parent") in related
            or related.intersection(set(event.correlation_ids.get("children", []) or []))
        ]
    return [event for event in events if event.bundle_id == bundle_id]


def reconstruct(store: JournalStore, claim_id: str) -> list[dict[str, Any]]:
    timeline = []
    for event in get_trace(store, claim_id=claim_id):
        timeline.append(
            {
                "event_id": str(event.event_id),
                "operation_type": event.operation_type,
                "claim_id": event.claim_id,
                "bundle_id": event.bundle_id,
                "state_hash": event.new_state_hash,
                "payload_location": event.payload_location,
                "ts": event.ts.isoformat(),
                "correlation_ids": event.correlation_ids,
            }
        )
    return timeline
