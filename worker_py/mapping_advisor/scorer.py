"""Transparent logistic scoring for mapping candidates (pure stdlib).

A plain logistic-regression scorer over the :class:`MappingFeatures` vector.
Ships with sensible default weights so it produces useful rankings with **no**
training, and supports supervised refinement from historical confirmed mappings
via batch gradient descent — all in pure Python (no numpy / ML frameworks), so
the scoring is fully auditable.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

from .model import MappingCandidate, MappingFeatures

# Canonical feature order — keep in lockstep with MappingFeatures fields.
FEATURE_ORDER = (
    "value_exact_match",
    "value_normalized_match",
    "name_similarity",
    "type_match",
    "length_fit",
    "historical_support",
)

# Hand-tuned defaults: exact value and historical confirmation dominate, with
# normalized value match a strong secondary signal. Bias is negative so weak
# structural noise scores low.
_DEFAULT_WEIGHTS = {
    "value_exact_match": 3.2,
    "value_normalized_match": 2.0,
    "name_similarity": 0.8,
    "type_match": 0.5,
    "length_fit": 0.4,
    "historical_support": 2.6,
}
_DEFAULT_BIAS = -2.6


def _sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _vector(features: MappingFeatures) -> list[float]:
    return [float(getattr(features, name)) for name in FEATURE_ORDER]


class LogisticScorer:
    """Logistic-regression scorer over the mapping feature vector."""

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        bias: float = _DEFAULT_BIAS,
    ) -> None:
        base = dict(_DEFAULT_WEIGHTS)
        if weights:
            base.update(weights)
        self.weights = [base[name] for name in FEATURE_ORDER]
        self.bias = bias

    def predict_proba(self, features: MappingFeatures) -> float:
        z = self.bias + sum(w * x for w, x in zip(self.weights, _vector(features)))
        return _sigmoid(z)

    def train(
        self,
        samples: Sequence[MappingFeatures],
        labels: Sequence[int],
        *,
        epochs: int = 300,
        lr: float = 0.2,
        l2: float = 0.0,
    ) -> "LogisticScorer":
        """Refine weights via batch gradient descent on labeled examples.

        ``samples`` are feature vectors and ``labels`` are 1 (confirmed mapping)
        / 0 (rejected/negative). Returns ``self`` for chaining.
        """
        if not samples:
            return self
        if len(samples) != len(labels):
            raise ValueError("samples and labels must be the same length")
        n = len(samples)
        vectors = [_vector(f) for f in samples]
        dim = len(FEATURE_ORDER)
        for _ in range(max(1, epochs)):
            grad_w = [0.0] * dim
            grad_b = 0.0
            for vec, label in zip(vectors, labels):
                z = self.bias + sum(w * x for w, x in zip(self.weights, vec))
                pred = _sigmoid(z)
                err = pred - float(label)
                for i in range(dim):
                    grad_w[i] += err * vec[i]
                grad_b += err
            for i in range(dim):
                self.weights[i] -= lr * (grad_w[i] / n + l2 * self.weights[i])
            self.bias -= lr * (grad_b / n)
        return self

    def score_candidates(
        self, candidates: Iterable[MappingCandidate]
    ) -> list[MappingCandidate]:
        """Score candidates in place and return them ranked best-first."""
        scored = list(candidates)
        for candidate in scored:
            proba = self.predict_proba(candidate.features)
            candidate.score = proba
            candidate.confidence = proba
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored

    def to_dict(self) -> dict[str, object]:
        return {
            "weights": dict(zip(FEATURE_ORDER, self.weights)),
            "bias": self.bias,
        }
