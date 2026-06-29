"""Data model for the advisory mapping-suggestion service.

All structures are PHI-safe by construction: they capture *structure* (loop
paths, segment ids, element positions, field offsets, value shapes) and short
sample values used only for alignment scoring. Callers that ingest production
data should de-identify sample values first; the advisor itself never persists
raw clinical content.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class FlatField:
    """One field in a flat-file record (fixed-width or delimited)."""

    name: str
    start: int          # 0-based char offset (fixed) or column index (delimited)
    length: int         # field width (fixed); 0 for delimited
    value: str = ""     # sample value (used only for alignment scoring)
    kind: str = "fixed"  # "fixed" | "delimited"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class X12ElementRef:
    """A reference to one X12 data element within its loop/segment context."""

    loop_path: str          # structural breadcrumb, e.g. "HL*22/CLM/SV1"
    segment_id: str         # e.g. "CLM"
    element_position: int   # 1-based element position within the segment
    value: str = ""         # sample value (used only for alignment scoring)
    transaction_set: str = ""

    @property
    def address(self) -> str:
        """Stable, human-readable element address, e.g. ``CLM01@HL*22/CLM``."""
        return f"{self.segment_id}{self.element_position:02d}@{self.loop_path}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["address"] = self.address
        return data


@dataclass
class MappingFeatures:
    """Transparent structural-alignment features for a candidate mapping."""

    value_exact_match: float = 0.0
    value_normalized_match: float = 0.0
    name_similarity: float = 0.0
    type_match: float = 0.0
    length_fit: float = 0.0
    historical_support: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass
class MappingCandidate:
    """A proposed mapping from an X12 element to a flat-file field."""

    source: X12ElementRef
    target: FlatField
    features: MappingFeatures
    score: float = 0.0
    confidence: float = 0.0
    rule_type: str = "element_map"
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "features": self.features.to_dict(),
            "score": round(self.score, 4),
            "confidence": round(self.confidence, 4),
            "rule_type": self.rule_type,
            "rationale": self.rationale,
        }


@dataclass
class ValidationRuleSuggestion:
    """A validation rule inferred from 999 IK3/IK4 error positions."""

    loop_path: str
    segment_id: str
    element_position: Optional[int]
    error_code: str
    requirement: str
    confidence: float = 0.0
    occurrences: int = 1
    claim_refs: list[str] = field(default_factory=list)
    rule_type: str = "validation_rule"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MappingSuggestionSet:
    """The advisory output: ranked candidate mappings + inferred validations."""

    transaction_set: str
    partner_id: Optional[str]
    candidates: list[MappingCandidate] = field(default_factory=list)
    validation_rules: list[ValidationRuleSuggestion] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: _dt.datetime.now(_dt.timezone.utc).isoformat()
    )
    advisory: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_set": self.transaction_set,
            "partner_id": self.partner_id,
            "advisory": self.advisory,
            "generated_at": self.generated_at,
            "candidate_count": len(self.candidates),
            "validation_rule_count": len(self.validation_rules),
            "candidates": [c.to_dict() for c in self.candidates],
            "validation_rules": [v.to_dict() for v in self.validation_rules],
        }
