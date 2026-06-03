"""Triage Tier Executor — dispatches files to tier-specific processing paths.

Tier 0: deterministic fast path (existing translator pipeline)
Tier 1: assisted review — extra validation rules, anomaly flagging, enriched audit
Tier 2: supervised swarm — AI analysis via Ollama
Tier 3: human exception — park file, log, notify, do NOT auto-process
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from file_profiler import FileProfile
from complexity_scorer import ComplexityScore
from routing_engine import RoutingDecision

logger = logging.getLogger("tier_executor")


@dataclass
class TierResult:
    """Outcome of tier-specific execution."""
    tier: int
    status: str  # completed, parked, failed, needs_review
    processing_ms: float = 0.0
    ai_analyses: list[dict[str, Any]] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    swarm_task_count: int = 0
    supervisor_verdict: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "status": self.status,
            "processing_ms": round(self.processing_ms, 2),
            "ai_analyses": self.ai_analyses,
            "flags": self.flags,
            "recommendations": self.recommendations,
            "swarm_task_count": self.swarm_task_count,
            "supervisor_verdict": self.supervisor_verdict,
            "error": self.error,
        }


def execute_tier(
    tier: int,
    profile: FileProfile,
    score: ComplexityScore,
    decision: RoutingDecision,
    text: str,
    *,
    ollama_url: str = "http://localhost:11434",
    ollama_model: str = "qwen2.5-coder:7b",
) -> TierResult:
    """Dispatch to the correct tier handler."""
    start = time.perf_counter()

    try:
        if tier == 0:
            result = _execute_tier0(profile, score, decision)
        elif tier == 1:
            result = _execute_tier1(profile, score, decision, text)
        elif tier == 2:
            from tier2_swarm import execute_swarm
            result = execute_swarm(
                profile, score, decision, text,
                ollama_url=ollama_url, ollama_model=ollama_model,
            )
        elif tier == 3:
            result = _execute_tier3(profile, score, decision)
        else:
            result = TierResult(tier=tier, status="failed", error=f"Unknown tier: {tier}")
    except Exception as exc:
        logger.exception("Tier %d execution failed for file %s", tier, profile.file_id)
        result = TierResult(tier=tier, status="failed", error=str(exc))

    result.processing_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "Tier %d execution for %s: status=%s, flags=%d, analyses=%d (%.1fms)",
        tier, profile.file_id, result.status,
        len(result.flags), len(result.ai_analyses), result.processing_ms,
    )
    return result


def _execute_tier0(
    profile: FileProfile,
    score: ComplexityScore,
    decision: RoutingDecision,
) -> TierResult:
    """Tier 0: deterministic fast path. No extra processing needed."""
    return TierResult(
        tier=0,
        status="completed",
        flags=[],
        recommendations=["deterministic_processing_sufficient"],
    )


def _execute_tier1(
    profile: FileProfile,
    score: ComplexityScore,
    decision: RoutingDecision,
    text: str,
) -> TierResult:
    """Tier 1: assisted review — apply enhanced validation rules."""
    from tier1_assist import run_assisted_review
    return run_assisted_review(profile, score, decision, text)


def _execute_tier3(
    profile: FileProfile,
    score: ComplexityScore,
    decision: RoutingDecision,
) -> TierResult:
    """Tier 3: human exception — park the file, do not auto-process."""
    reasons = []
    if decision.gate_triggers:
        reasons.extend(decision.gate_triggers)
    if score.reason_codes:
        reasons.extend(score.reason_codes)

    return TierResult(
        tier=3,
        status="parked",
        flags=["HUMAN_REVIEW_REQUIRED", "AUTO_PROCESSING_BLOCKED"],
        recommendations=[
            f"File scored {score.total_score:.1f}/100 with gates: {decision.gate_triggers}",
            "Manual review required before processing",
            "Check compliance materiality and validation anomalies",
        ],
    )
