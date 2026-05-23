"""TurboHEDI Routing Engine — threshold-based tier selection with hard gates.

Applies threshold bands and policy gates to select processing tier.
Produces a RoutingDecision with full traceability for audit logging.

Reference: 2026-05-13 Swarm Escalation MVP Design Pack §§ Architecture,
           Thresholds, Risk Controls.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from complexity_scorer import ComplexityScore
from file_profiler import FileProfile

logger = logging.getLogger("routing_engine")


@dataclass
class RoutingDecision:
    """Immutable routing decision for one inbound file."""
    routing_decision_id: str
    file_id: str
    tier: int                           # 0–3
    tier_label: str                     # human-readable
    score_total: float
    gate_triggers: list[str] = field(default_factory=list)
    explanation: str = ""
    policy_version: str = "1.0.0"
    scorer_version: str = "1.0.0"
    decided_at: str = ""
    decision_duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "routing_decision_id": self.routing_decision_id,
            "file_id": self.file_id,
            "tier": self.tier,
            "tier_label": self.tier_label,
            "score_total": round(self.score_total, 2),
            "gate_triggers": self.gate_triggers,
            "explanation": self.explanation,
            "policy_version": self.policy_version,
            "scorer_version": self.scorer_version,
            "decided_at": self.decided_at,
            "decision_duration_ms": round(self.decision_duration_ms, 2),
        }


TIER_LABELS = {
    0: "deterministic_fast_path",
    1: "assisted_review",
    2: "supervised_swarm",
    3: "human_exception",
}


# ---------------------------------------------------------------------------
# Hard escalation gates — override numeric score when triggered
# Design pack § Hard escalation gates
# ---------------------------------------------------------------------------

class GatePolicy:
    """Configurable hard escalation gate policy."""

    def __init__(
        self,
        *,
        min_map_confidence_for_tier0: float = 0.7,
        min_schema_confidence_for_tier0: float = 0.7,
        max_anomaly_classes_for_tier1: int = 2,
        supervisor_confidence_floor: float = 0.4,
    ):
        self.min_map_confidence_for_tier0 = min_map_confidence_for_tier0
        self.min_schema_confidence_for_tier0 = min_schema_confidence_for_tier0
        self.max_anomaly_classes_for_tier1 = max_anomaly_classes_for_tier1
        self.supervisor_confidence_floor = supervisor_confidence_floor

    def evaluate(
        self,
        profile: FileProfile,
        score: ComplexityScore,
    ) -> tuple[int, list[str]]:
        """Return (minimum_tier, list_of_gate_triggers)."""
        min_tier = 0
        triggers: list[str] = []

        # Gate: low map/schema confidence → at least Tier 1
        if profile.map_match_confidence < self.min_map_confidence_for_tier0:
            min_tier = max(min_tier, 1)
            triggers.append(f"low_map_confidence:{profile.map_match_confidence:.2f}")
        if profile.schema_match_confidence < self.min_schema_confidence_for_tier0:
            min_tier = max(min_tier, 1)
            triggers.append(f"low_schema_confidence:{profile.schema_match_confidence:.2f}")

        # Gate: previously unseen partner + document combo → at least Tier 1
        if profile.partner_reliability_tier == "new":
            min_tier = max(min_tier, 1)
            triggers.append("new_partner")

        # Gate: multiple anomaly classes → at least Tier 2
        unique_anomaly_types = len(set(profile.validation_anomaly_types))
        if unique_anomaly_types > self.max_anomaly_classes_for_tier1:
            min_tier = max(min_tier, 2)
            triggers.append(f"multi_anomaly_classes:{unique_anomaly_types}")

        # Gate: compliance materiality + unresolved anomaly → at least Tier 3
        if profile.compliance_materiality_flag and profile.validation_anomaly_count > 0:
            min_tier = max(min_tier, 3)
            triggers.append("compliance_with_anomalies")

        # Gate: overall score confidence very low → bump up one tier
        if score.confidence < self.supervisor_confidence_floor:
            min_tier = max(min_tier, score.tier_suggestion + 1)
            triggers.append(f"low_score_confidence:{score.confidence:.2f}")

        # Gate: control number mismatch → at least Tier 1
        if any("control_number_mismatch" in m for m in profile.loop_irregularity_markers):
            min_tier = max(min_tier, 1)
            triggers.append("control_number_mismatch")

        return min(min_tier, 3), triggers


# Default gate policy instance
DEFAULT_GATE_POLICY = GatePolicy()


def route_file(
    profile: FileProfile,
    score: ComplexityScore,
    *,
    gate_policy: GatePolicy | None = None,
    policy_version: str = "1.0.0",
) -> RoutingDecision:
    """Determine processing tier for a profiled and scored file."""
    start = time.perf_counter()
    policy = gate_policy or DEFAULT_GATE_POLICY

    # Start with the scorer's suggestion
    tier = score.tier_suggestion

    # Apply hard escalation gates
    gate_min_tier, gate_triggers = policy.evaluate(profile, score)
    if gate_min_tier > tier:
        logger.info(
            "Hard gate escalated file %s from Tier %d to Tier %d: %s",
            profile.file_id, tier, gate_min_tier, gate_triggers,
        )
        tier = gate_min_tier

    tier = min(tier, 3)

    # Build explanation
    parts = [f"score={score.total_score:.1f} → Tier {score.tier_suggestion}"]
    if gate_triggers:
        parts.append(f"gates escalated to Tier {tier}: {', '.join(gate_triggers)}")
    if score.reason_codes:
        parts.append(f"reasons: {', '.join(score.reason_codes)}")
    explanation = "; ".join(parts)

    elapsed_ms = (time.perf_counter() - start) * 1000

    decision = RoutingDecision(
        routing_decision_id=uuid4().hex,
        file_id=profile.file_id,
        tier=tier,
        tier_label=TIER_LABELS.get(tier, f"tier_{tier}"),
        score_total=score.total_score,
        gate_triggers=gate_triggers,
        explanation=explanation,
        policy_version=policy_version,
        decided_at=datetime.now(timezone.utc).isoformat(),
        decision_duration_ms=elapsed_ms,
    )

    logger.info(
        "Routed file %s → %s (score=%.1f, gates=%s, %.2fms)",
        profile.file_id, decision.tier_label, score.total_score,
        gate_triggers or "none", elapsed_ms,
    )

    return decision
