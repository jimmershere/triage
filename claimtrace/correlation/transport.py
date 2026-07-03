"""Transport-level Claimtrace correlation metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TraceContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    claim_root_id: str
    bundle_id: Optional[str] = None
    trace_id: str
    prior_state_hash: Optional[str] = None
    new_state_hash: str
    correlation_ids: dict[str, Any] = Field(default_factory=dict)

    @field_validator("claim_id", "claim_root_id", "trace_id", "new_state_hash")
    @classmethod
    def _required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value is required")
        return value


HEADER_MAP = {
    "claim_id": "X-Claim-ID",
    "bundle_id": "X-Bundle-ID",
    "trace_id": "X-Trace-ID",
    "new_state_hash": "X-State-Hash",
    "claim_root_id": "X-Claim-Root-ID",
    "prior_state_hash": "X-Prior-State-Hash",
}


def to_headers(ctx: TraceContext) -> dict[str, str]:
    headers: dict[str, str] = {}
    for field, header in HEADER_MAP.items():
        value = getattr(ctx, field)
        if value:
            headers[header] = str(value)
    if ctx.correlation_ids:
        headers["X-Correlation-IDs"] = json.dumps(ctx.correlation_ids, sort_keys=True)
    return headers


def from_headers(headers: Mapping[str, str]) -> TraceContext:
    lower = {str(key).lower(): value for key, value in headers.items()}
    values: dict[str, Any] = {}
    for field, header in HEADER_MAP.items():
        value = lower.get(header.lower())
        if value:
            values[field] = value
    raw_correlation = lower.get("x-correlation-ids")
    if raw_correlation:
        # Peer-controlled header: a malformed value must not crash inbound
        # correlation resolution. Accept only a JSON object; ignore anything else.
        try:
            parsed = json.loads(raw_correlation)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            values["correlation_ids"] = parsed
    return TraceContext(**values)


def envelope_path(path: str | Path) -> Path:
    return Path(f"{Path(path)}.env.json")


def write_envelope(path: str | Path, ctx: TraceContext) -> Path:
    target = envelope_path(path)
    payload = {"path": str(path), "trace": ctx.model_dump(mode="json")}
    target.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    return target


def read_envelope(path: str | Path) -> TraceContext:
    payload = json.loads(envelope_path(path).read_text(encoding="utf-8"))
    return TraceContext(**payload["trace"])
