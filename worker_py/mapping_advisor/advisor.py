"""Advisory mapping orchestrator.

Ties together flat-file parsing, structural candidate generation, logistic
scoring, and 999-signal validation-rule inference into a single advisory
:class:`MappingSuggestionSet`.

Governance: this class produces *suggestions only*. There is intentionally no
``apply`` method — it never mutates submissions or auto-installs mappings.
Approved rules are created by a human via the repository's approval flow and
versioned like any other config.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional, Sequence

from .flatfile import extract_fields
from .model import MappingCandidate, MappingFeatures, MappingSuggestionSet
from .scorer import LogisticScorer
from .structural import extract_elements, generate_candidates, source_key
from .validation_signal import infer_validation_rules

GOVERNANCE_NOTE = (
    "ADVISORY ONLY. This module suggests candidate mappings and validation "
    "rules; it never auto-applies mappings or mutates submissions. A human must "
    "approve each suggestion, and approved rules are versioned like any other "
    "config. Train on de-identified/internal structure only."
)


class MappingAdvisor:
    """Generate ranked, advisory mapping suggestions from paired examples."""

    def __init__(
        self,
        scorer: Optional[LogisticScorer] = None,
        historical: Optional[Mapping[str, Iterable[str]]] = None,
    ) -> None:
        self.scorer = scorer or LogisticScorer()
        # Maps a structural source key -> set of confirmed flat-field names.
        self.historical: dict[str, set[str]] = {
            key: set(values) for key, values in (historical or {}).items()
        }

    def learn_confirmed(self, src_key: str, field_name: str) -> None:
        """Record a human-confirmed mapping to strengthen future scoring."""
        self.historical.setdefault(src_key, set()).add(field_name)

    def train_from_examples(
        self, samples: Sequence[MappingFeatures], labels: Sequence[int], **kwargs
    ) -> None:
        """Supervised refinement of the scorer from historical confirmations."""
        self.scorer.train(samples, labels, **kwargs)

    def suggest(
        self,
        x12_text: str,
        *,
        flat_file_text: Optional[str] = None,
        layout: Optional[Sequence[tuple[str, int, int]]] = None,
        delimiter: Optional[str] = None,
        header: bool = False,
        ack_999_text: Optional[str] = None,
        partner_id: Optional[str] = None,
        top_k: int = 25,
        min_confidence: float = 0.0,
    ) -> MappingSuggestionSet:
        elements = extract_elements(x12_text)
        transaction_set = elements[0].transaction_set if elements else ""

        candidates: list[MappingCandidate] = []
        if flat_file_text:
            fields = extract_fields(
                flat_file_text, layout=layout, delimiter=delimiter, header=header
            )
            raw = generate_candidates(elements, fields, historical=self.historical)
            ranked = self.scorer.score_candidates(raw)
            candidates = self._best_per_source(ranked)
            if min_confidence > 0.0:
                candidates = [c for c in candidates if c.confidence >= min_confidence]
            candidates = candidates[: max(0, top_k)]

        validation_rules = (
            infer_validation_rules(ack_999_text) if ack_999_text else []
        )

        return MappingSuggestionSet(
            transaction_set=transaction_set,
            partner_id=partner_id,
            candidates=candidates,
            validation_rules=validation_rules,
        )

    @staticmethod
    def _best_per_source(ranked: list[MappingCandidate]) -> list[MappingCandidate]:
        """Keep the single highest-scoring target per source element."""
        best: dict[str, MappingCandidate] = {}
        for candidate in ranked:  # ranked is already best-first
            key = source_key(candidate.source)
            if key not in best:
                best[key] = candidate
        out = list(best.values())
        out.sort(key=lambda c: c.score, reverse=True)
        return out
