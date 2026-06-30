"""Append-only claim journal models and stores."""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from typing import Any, Iterable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


OperationType = Literal[
    "INGEST",
    "VALIDATE",
    "SPLIT",
    "BUNDLE",
    "ROUTE",
    "ACK",
    "ADJUDICATE",
    "TRANSFORM",
    "MERKLE_ROOT",
]


class ClaimEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    claim_id: str
    bundle_id: Optional[str] = None
    prior_state_hash: Optional[str] = None
    new_state_hash: str
    payload_location: str
    operation_type: OperationType
    service_name: str
    ts: _dt.datetime = Field(default_factory=lambda: _dt.datetime.now(_dt.timezone.utc))
    correlation_ids: dict[str, Any] = Field(default_factory=dict)

    @field_validator("claim_id", "new_state_hash", "payload_location", "service_name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value is required")
        return value


class JournalStore:
    def append_event(self, event: ClaimEvent) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def events(self) -> list[ClaimEvent]:  # pragma: no cover - interface
        raise NotImplementedError


class InMemoryJournalStore(JournalStore):
    def __init__(self, events: Iterable[ClaimEvent] | None = None) -> None:
        # Tie-break equal timestamps by event_id to match the Postgres store's
        # ORDER BY ts ASC, event_id ASC — otherwise journal replay / Merkle root
        # over the timeline can differ between the in-memory and DB backends.
        self._events = sorted(list(events or []), key=lambda event: (event.ts, str(event.event_id)))

    def append_event(self, event: ClaimEvent) -> None:
        if any(existing.event_id == event.event_id for existing in self._events):
            raise ValueError(f"duplicate event_id {event.event_id}")
        self._events.append(event)
        self._events.sort(key=lambda item: (item.ts, str(item.event_id)))

    def events(self) -> list[ClaimEvent]:
        return list(self._events)


class PostgresJournalStore(JournalStore):
    def __init__(self, conn) -> None:
        self.conn = conn

    def append_event(self, event: ClaimEvent) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO claim_event (
                    event_id, claim_id, bundle_id, prior_state_hash, new_state_hash,
                    payload_location, operation_type, service_name, ts, correlation_ids
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                """,
                (
                    str(event.event_id),
                    event.claim_id,
                    event.bundle_id,
                    event.prior_state_hash,
                    event.new_state_hash,
                    event.payload_location,
                    event.operation_type,
                    event.service_name,
                    event.ts,
                    json.dumps(event.correlation_ids),
                ),
            )
        self.conn.commit()

    def events(self) -> list[ClaimEvent]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT event_id, claim_id, bundle_id, prior_state_hash, new_state_hash,
                       payload_location, operation_type, service_name, ts, correlation_ids
                  FROM claim_event
                 ORDER BY ts ASC, event_id ASC
                """
            )
            rows = cur.fetchall()
        events: list[ClaimEvent] = []
        for row in rows:
            events.append(
                ClaimEvent(
                    event_id=row[0],
                    claim_id=row[1],
                    bundle_id=row[2],
                    prior_state_hash=row[3],
                    new_state_hash=row[4],
                    payload_location=row[5],
                    operation_type=row[6],
                    service_name=row[7],
                    ts=row[8],
                    correlation_ids=row[9] or {},
                )
            )
        return events
