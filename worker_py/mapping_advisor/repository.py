"""Persistence contract + in-memory implementation for the approval queue.

The advisor only *suggests*. Suggestions land in a queue with status
``pending``; a human then approves or rejects each one. Approval is the only
path that produces an active mapping/validation rule, and every approval is
*versioned* (a new version supersedes the previous active version for the same
rule key). Nothing here applies a rule to a submission — persistence is the
governance boundary, not an execution path.

This module is pure stdlib so it can live inside the isolated worker package.
The API persists the same records in Postgres (see ``007_mapping.sql`` and
``api/mapping_routes.py``); :class:`InMemoryMappingRepository` mirrors that
contract for tests and offline use.
"""

from __future__ import annotations

import abc
import datetime as _dt
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .model import MappingSuggestionSet

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


@dataclass
class SuggestionRecord:
    """One queued advisory suggestion awaiting human review."""

    id: int
    transaction_set: str
    partner_id: Optional[str]
    rule_type: str
    rule_key: str
    summary: str
    score: float
    confidence: float
    payload: dict[str, Any]
    status: str = PENDING
    created_at: str = field(default_factory=_now)
    decided_at: Optional[str] = None
    decided_by: Optional[str] = None
    decision_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RuleRecord:
    """An approved, versioned mapping/validation rule."""

    id: int
    suggestion_id: int
    transaction_set: str
    partner_id: Optional[str]
    rule_type: str
    rule_key: str
    version: int
    definition: dict[str, Any]
    approved_by: str
    approved_at: str = field(default_factory=_now)
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def rule_key_for(payload: dict[str, Any], transaction_set: str, partner_id: Optional[str]) -> str:
    """Compute a stable rule identity used for versioning.

    The key is value-independent so re-running the advisor on a new sample maps
    to the *same* rule lineage (and therefore a new version, not a duplicate).
    """
    partner = partner_id or "*"
    rule_type = payload.get("rule_type", "element_map")
    if rule_type == "validation_rule":
        return "|".join(
            [
                "VR",
                transaction_set,
                partner,
                str(payload.get("loop_path", "")),
                str(payload.get("segment_id", "")),
                str(payload.get("element_position", "")),
                str(payload.get("error_code", "")),
            ]
        )
    source = payload.get("source", {})
    target = payload.get("target", {})
    src = source.get("address") if isinstance(source, dict) else None
    tgt = target.get("name") if isinstance(target, dict) else None
    return "|".join(["MM", transaction_set, partner, str(src), str(tgt)])


def summarize(payload: dict[str, Any]) -> str:
    """Short, PHI-free human summary for a queue row."""
    rule_type = payload.get("rule_type", "element_map")
    if rule_type == "validation_rule":
        pos = payload.get("element_position")
        loc = f"{payload.get('segment_id', '?')}"
        if pos:
            loc += f"{int(pos):02d}"
        loc += f"@{payload.get('loop_path', '?')}"
        return f"{loc}: {payload.get('requirement', 'validation rule')}"
    source = payload.get("source", {})
    target = payload.get("target", {})
    src = source.get("address", "?") if isinstance(source, dict) else "?"
    tgt = target.get("name", "?") if isinstance(target, dict) else "?"
    return f"{src} -> {tgt}"


class MappingRuleRepository(abc.ABC):
    """Storage contract for the advisory queue + versioned approved rules."""

    @abc.abstractmethod
    def enqueue_suggestions(self, suggestion_set: MappingSuggestionSet) -> list[int]:
        """Persist a suggestion set as pending queue rows; return their ids."""

    @abc.abstractmethod
    def list_suggestions(
        self, *, status: Optional[str] = None, limit: int = 100
    ) -> list[SuggestionRecord]:
        ...

    @abc.abstractmethod
    def get_suggestion(self, suggestion_id: int) -> Optional[SuggestionRecord]:
        ...

    @abc.abstractmethod
    def approve(self, suggestion_id: int, approver: str) -> RuleRecord:
        """Approve a pending suggestion, creating a new active rule version."""

    @abc.abstractmethod
    def reject(self, suggestion_id: int, approver: str, reason: str = "") -> SuggestionRecord:
        ...

    @abc.abstractmethod
    def list_rules(
        self, *, active_only: bool = True, limit: int = 100
    ) -> list[RuleRecord]:
        ...


class InMemoryMappingRepository(MappingRuleRepository):
    """Reference implementation backed by in-process dicts (tests/offline)."""

    def __init__(self) -> None:
        self._suggestions: dict[int, SuggestionRecord] = {}
        self._rules: dict[int, RuleRecord] = {}
        self._sid = 0
        self._rid = 0

    def enqueue_suggestions(self, suggestion_set: MappingSuggestionSet) -> list[int]:
        ts = suggestion_set.transaction_set
        partner = suggestion_set.partner_id
        ids: list[int] = []
        rows: list[dict[str, Any]] = []
        for cand in suggestion_set.candidates:
            payload = cand.to_dict()
            rows.append(
                {
                    "rule_type": payload.get("rule_type", "element_map"),
                    "score": payload.get("score", 0.0),
                    "confidence": payload.get("confidence", 0.0),
                    "payload": payload,
                }
            )
        for vr in suggestion_set.validation_rules:
            payload = vr.to_dict()
            rows.append(
                {
                    "rule_type": "validation_rule",
                    "score": payload.get("confidence", 0.0),
                    "confidence": payload.get("confidence", 0.0),
                    "payload": payload,
                }
            )
        for row in rows:
            self._sid += 1
            rec = SuggestionRecord(
                id=self._sid,
                transaction_set=ts,
                partner_id=partner,
                rule_type=row["rule_type"],
                rule_key=rule_key_for(row["payload"], ts, partner),
                summary=summarize(row["payload"]),
                score=float(row["score"]),
                confidence=float(row["confidence"]),
                payload=row["payload"],
            )
            self._suggestions[rec.id] = rec
            ids.append(rec.id)
        return ids

    def list_suggestions(
        self, *, status: Optional[str] = None, limit: int = 100
    ) -> list[SuggestionRecord]:
        items = [
            s
            for s in self._suggestions.values()
            if status is None or s.status == status
        ]
        items.sort(key=lambda s: (s.confidence, s.id), reverse=True)
        return items[:limit]

    def get_suggestion(self, suggestion_id: int) -> Optional[SuggestionRecord]:
        return self._suggestions.get(suggestion_id)

    def approve(self, suggestion_id: int, approver: str) -> RuleRecord:
        rec = self._suggestions.get(suggestion_id)
        if rec is None:
            raise KeyError(f"suggestion {suggestion_id} not found")
        if rec.status == APPROVED:
            raise ValueError(f"suggestion {suggestion_id} already approved")
        existing = [r for r in self._rules.values() if r.rule_key == rec.rule_key]
        next_version = max((r.version for r in existing), default=0) + 1
        for r in existing:
            r.active = False
        self._rid += 1
        rule = RuleRecord(
            id=self._rid,
            suggestion_id=rec.id,
            transaction_set=rec.transaction_set,
            partner_id=rec.partner_id,
            rule_type=rec.rule_type,
            rule_key=rec.rule_key,
            version=next_version,
            definition=rec.payload,
            approved_by=approver,
        )
        self._rules[rule.id] = rule
        rec.status = APPROVED
        rec.decided_at = _now()
        rec.decided_by = approver
        return rule

    def reject(
        self, suggestion_id: int, approver: str, reason: str = ""
    ) -> SuggestionRecord:
        rec = self._suggestions.get(suggestion_id)
        if rec is None:
            raise KeyError(f"suggestion {suggestion_id} not found")
        rec.status = REJECTED
        rec.decided_at = _now()
        rec.decided_by = approver
        rec.decision_reason = reason
        return rec

    def list_rules(
        self, *, active_only: bool = True, limit: int = 100
    ) -> list[RuleRecord]:
        items = [
            r for r in self._rules.values() if (r.active or not active_only)
        ]
        items.sort(key=lambda r: r.id, reverse=True)
        return items[:limit]
