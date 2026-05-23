"""Supervisor verdict parsing and policy.

A supervisor takes the responses from a swarm of agents and renders a single
verdict — ``APPROVE``, ``FLAG`` (process but mark for human review) or
``REJECT`` — with a confidence score and a list of issues.

The supervisor's reply is expected to be a JSON object; this module tolerates
a wide range of malformed output (markdown fences, leading text, truncated
JSON, mis-cased verdicts) and falls back to a safe ``FLAG`` verdict.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

_VALID_VERDICTS = {"APPROVE", "FLAG", "REJECT"}
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class SupervisorVerdict:
    """A parsed supervisor decision."""

    verdict: str = "FLAG"
    confidence: float = 0.0
    summary: str = ""
    issues: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [value]
    return []


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    return text


def parse_supervisor_response(raw: str) -> SupervisorVerdict:
    """Tolerantly parse a supervisor LLM response into a verdict.

    A bare empty / unparseable string yields a default ``FLAG`` verdict with
    a *confidence of 0* and an issue recording the parse failure.
    """
    text = _strip_fences(raw or "")
    if not text:
        return SupervisorVerdict(
            verdict="FLAG",
            summary="Supervisor returned no content.",
            issues=["supervisor_empty"],
            raw=raw,
        )

    payload: dict[str, Any] | None = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(text)
        if match:
            try:
                payload = json.loads(match.group())
            except json.JSONDecodeError:
                payload = None

    if not isinstance(payload, dict):
        return SupervisorVerdict(
            verdict="FLAG",
            summary="Could not parse supervisor JSON.",
            issues=["supervisor_parse_failure"],
            raw=raw,
        )

    verdict = str(payload.get("verdict", "FLAG")).upper().strip()
    if verdict not in _VALID_VERDICTS:
        verdict = "FLAG"

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return SupervisorVerdict(
        verdict=verdict,
        confidence=confidence,
        summary=str(payload.get("summary", "")).strip(),
        issues=_coerce(payload.get("issues")),
        actions=_coerce(payload.get("actions")),
        raw=raw,
    )
