"""TurboHEDI Complexity Scorer — weighted 100-point scoring model.

Applies weighted scoring across structural load, novelty, execution risk,
and business impact.  Emits total score, factor contributions, and confidence.

Reference: 2026-05-13 Swarm Escalation MVP Design Pack § Scoring Model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from file_profiler import FileProfile


@dataclass
class FactorScore:
    """Individual factor contribution to the total complexity score."""
    bucket: str
    factor: str
    weight: int
    raw_severity: float  # 0.0–1.0 normalized
    weighted_score: float  # raw_severity * weight
    evidence: str = ""


@dataclass
class ComplexityScore:
    """Full scoring result for one file."""
    total_score: float
    tier_suggestion: int  # 0–3 based on thresholds
    confidence: float     # 0.0–1.0 — how much data backed the score
    factors: list[FactorScore] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_score": round(self.total_score, 2),
            "tier_suggestion": self.tier_suggestion,
            "confidence": round(self.confidence, 2),
            "factors": [
                {
                    "bucket": f.bucket,
                    "factor": f.factor,
                    "weight": f.weight,
                    "raw_severity": round(f.raw_severity, 3),
                    "weighted_score": round(f.weighted_score, 2),
                    "evidence": f.evidence,
                }
                for f in self.factors
            ],
            "reason_codes": self.reason_codes,
        }


# ---------------------------------------------------------------------------
# Default weight configuration — matches design pack table
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS: dict[str, dict[str, int]] = {
    "structural_load": {
        "segment_count_density": 8,
        "transaction_count": 7,
        "transaction_type_mix": 5,
        "loop_irregularity": 10,
    },
    "novelty_variance": {
        "partner_reliability_tier": 7,
        "schema_map_confidence": 8,
        "custom_segment_presence": 5,
        "format_drift": 5,
    },
    "execution_risk": {
        "historical_failure_rate": 8,
        "validation_anomaly_count": 8,
        "retry_history": 4,
        "output_confidence_deficit": 5,
    },
    "business_impact": {
        "downstream_criticality": 8,
        "sla_sensitivity": 5,
        "compliance_materiality": 7,
    },
}

# Threshold bands: design pack § Thresholds
TIER_THRESHOLDS = [
    (0, 24, 0),    # Tier 0 — deterministic fast path
    (25, 39, 1),   # Tier 1 — assisted / rules-enhanced
    (40, 74, 2),   # Tier 2 — supervised swarm
    (75, 100, 3),  # Tier 3 — human exception
]

# Segment density norms (segments per KB) for severity calculation
_DENSITY_NORMAL = 40   # typical ~40 segments/KB for 837
_DENSITY_HIGH = 120    # unusually dense


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def score_file(
    profile: FileProfile,
    weights: dict[str, dict[str, int]] | None = None,
) -> ComplexityScore:
    """Score a FileProfile and return a ComplexityScore with factor breakdown."""
    w = weights or DEFAULT_WEIGHTS
    factors: list[FactorScore] = []
    reason_codes: list[str] = []
    data_signals = 0  # count of factors with real signal (not default)
    total_possible_signals = 0

    # -----------------------------------------------------------------------
    # Structural Load (30 pts)
    # -----------------------------------------------------------------------
    sl = w["structural_load"]

    # Segment count density
    kb = max(profile.byte_size / 1024, 0.1)
    density = profile.segment_count / kb
    density_sev = _clamp((density - _DENSITY_NORMAL) / (_DENSITY_HIGH - _DENSITY_NORMAL))
    factors.append(FactorScore(
        "structural_load", "segment_count_density", sl["segment_count_density"],
        density_sev, density_sev * sl["segment_count_density"],
        f"density={density:.1f} seg/KB",
    ))
    total_possible_signals += 1
    if density > _DENSITY_NORMAL:
        data_signals += 1

    # Transaction count
    tx_sev = _clamp(profile.transaction_count / 50)  # 50+ transactions = max severity
    factors.append(FactorScore(
        "structural_load", "transaction_count", sl["transaction_count"],
        tx_sev, tx_sev * sl["transaction_count"],
        f"tx_count={profile.transaction_count}",
    ))
    total_possible_signals += 1
    if profile.transaction_count > 1:
        data_signals += 1

    # Transaction type mix
    mix_sev = 1.0 if profile.mixed_transaction_types else 0.0
    factors.append(FactorScore(
        "structural_load", "transaction_type_mix", sl["transaction_type_mix"],
        mix_sev, mix_sev * sl["transaction_type_mix"],
        f"mixed={profile.mixed_transaction_types}, sets={profile.transaction_sets}",
    ))
    total_possible_signals += 1
    data_signals += 1  # always have this signal

    # Loop/structure irregularity
    irreg_count = len(profile.loop_irregularity_markers)
    irreg_sev = _clamp(irreg_count / 3)  # 3+ irregularities = max
    if irreg_count > 0:
        reason_codes.append("loop_irregularity")
    factors.append(FactorScore(
        "structural_load", "loop_irregularity", sl["loop_irregularity"],
        irreg_sev, irreg_sev * sl["loop_irregularity"],
        f"markers={profile.loop_irregularity_markers}",
    ))
    total_possible_signals += 1
    data_signals += 1

    # -----------------------------------------------------------------------
    # Novelty / Variance (25 pts)
    # -----------------------------------------------------------------------
    nv = w["novelty_variance"]

    # Partner reliability tier
    tier_map = {"stable": 0.0, "variable": 0.4, "new": 0.7, "unknown": 0.5}
    partner_sev = tier_map.get(profile.partner_reliability_tier, 0.5)
    factors.append(FactorScore(
        "novelty_variance", "partner_reliability_tier", nv["partner_reliability_tier"],
        partner_sev, partner_sev * nv["partner_reliability_tier"],
        f"tier={profile.partner_reliability_tier}",
    ))
    total_possible_signals += 1
    if profile.partner_reliability_tier != "unknown":
        data_signals += 1

    # Schema/map match confidence (inverted — low confidence = high severity)
    avg_confidence = (profile.schema_match_confidence + profile.map_match_confidence) / 2
    conf_sev = _clamp(1.0 - avg_confidence)
    if conf_sev > 0.5:
        reason_codes.append("low_map_confidence")
    factors.append(FactorScore(
        "novelty_variance", "schema_map_confidence", nv["schema_map_confidence"],
        conf_sev, conf_sev * nv["schema_map_confidence"],
        f"schema={profile.schema_match_confidence:.2f}, map={profile.map_match_confidence:.2f}",
    ))
    total_possible_signals += 1
    data_signals += 1

    # Custom segment presence
    custom_sev = 1.0 if profile.custom_segment_present else 0.0
    factors.append(FactorScore(
        "novelty_variance", "custom_segment_presence", nv["custom_segment_presence"],
        custom_sev, custom_sev * nv["custom_segment_presence"],
        f"custom={profile.custom_segment_present}",
    ))
    total_possible_signals += 1
    data_signals += 1

    # Format drift (placeholder — needs historical baseline comparison)
    # For MVP, uses a simple heuristic: unknown partner + low confidence = drift
    drift_sev = 0.0
    if profile.partner_reliability_tier in ("new", "unknown") and avg_confidence < 0.8:
        drift_sev = 0.4
        reason_codes.append("potential_format_drift")
    factors.append(FactorScore(
        "novelty_variance", "format_drift", nv["format_drift"],
        drift_sev, drift_sev * nv["format_drift"],
        "heuristic_estimate",
    ))
    total_possible_signals += 1
    # no real signal here yet

    # -----------------------------------------------------------------------
    # Execution Risk (25 pts)
    # -----------------------------------------------------------------------
    er = w["execution_risk"]

    # Historical failure rate
    fail_sev = _clamp(profile.historical_failure_rate)
    factors.append(FactorScore(
        "execution_risk", "historical_failure_rate", er["historical_failure_rate"],
        fail_sev, fail_sev * er["historical_failure_rate"],
        f"rate={profile.historical_failure_rate:.2f}",
    ))
    total_possible_signals += 1
    if profile.historical_failure_rate > 0:
        data_signals += 1

    # Validation anomaly count
    anomaly_sev = _clamp(profile.validation_anomaly_count / 10)  # 10+ = max
    if profile.validation_anomaly_count > 0:
        reason_codes.append("validation_anomalies")
    factors.append(FactorScore(
        "execution_risk", "validation_anomaly_count", er["validation_anomaly_count"],
        anomaly_sev, anomaly_sev * er["validation_anomaly_count"],
        f"count={profile.validation_anomaly_count}, types={profile.validation_anomaly_types}",
    ))
    total_possible_signals += 1
    data_signals += 1

    # Retry history
    retry_sev = _clamp(profile.retry_count / 3)  # 3+ retries = max
    factors.append(FactorScore(
        "execution_risk", "retry_history", er["retry_history"],
        retry_sev, retry_sev * er["retry_history"],
        f"retries={profile.retry_count}",
    ))
    total_possible_signals += 1
    if profile.retry_count > 0:
        data_signals += 1

    # Output confidence deficit (uses map confidence as proxy)
    deficit_sev = _clamp(1.0 - profile.map_match_confidence) * 0.5
    factors.append(FactorScore(
        "execution_risk", "output_confidence_deficit", er["output_confidence_deficit"],
        deficit_sev, deficit_sev * er["output_confidence_deficit"],
        f"map_confidence={profile.map_match_confidence:.2f}",
    ))
    total_possible_signals += 1
    data_signals += 1

    # -----------------------------------------------------------------------
    # Business Impact (20 pts)
    # -----------------------------------------------------------------------
    bi = w["business_impact"]

    # Downstream criticality
    crit_map = {"low": 0.1, "normal": 0.3, "high": 0.7, "critical": 1.0}
    crit_sev = crit_map.get(profile.business_criticality_class, 0.3)
    factors.append(FactorScore(
        "business_impact", "downstream_criticality", bi["downstream_criticality"],
        crit_sev, crit_sev * bi["downstream_criticality"],
        f"class={profile.business_criticality_class}",
    ))
    total_possible_signals += 1
    if profile.business_criticality_class != "normal":
        data_signals += 1

    # SLA sensitivity
    sla_map = {"relaxed": 0.1, "standard": 0.3, "urgent": 0.8}
    sla_sev = sla_map.get(profile.sla_class, 0.3)
    factors.append(FactorScore(
        "business_impact", "sla_sensitivity", bi["sla_sensitivity"],
        sla_sev, sla_sev * bi["sla_sensitivity"],
        f"sla={profile.sla_class}",
    ))
    total_possible_signals += 1
    if profile.sla_class != "standard":
        data_signals += 1

    # Compliance materiality
    comp_sev = 1.0 if profile.compliance_materiality_flag else 0.0
    if profile.compliance_materiality_flag:
        reason_codes.append("compliance_materiality")
    factors.append(FactorScore(
        "business_impact", "compliance_materiality", bi["compliance_materiality"],
        comp_sev, comp_sev * bi["compliance_materiality"],
        f"flagged={profile.compliance_materiality_flag}",
    ))
    total_possible_signals += 1
    data_signals += 1

    # -----------------------------------------------------------------------
    # Total score and tier suggestion
    # -----------------------------------------------------------------------
    total = sum(f.weighted_score for f in factors)
    total = _clamp(total, 0.0, 100.0)

    # Confidence = proportion of factors that had real data behind them
    confidence = data_signals / max(total_possible_signals, 1)

    # Tier suggestion from threshold bands
    tier = 0
    for lo, hi, t in TIER_THRESHOLDS:
        if lo <= total <= hi:
            tier = t
            break
    else:
        tier = 3  # above 100 (shouldn't happen, but safe)

    return ComplexityScore(
        total_score=total,
        tier_suggestion=tier,
        confidence=confidence,
        factors=factors,
        reason_codes=list(dict.fromkeys(reason_codes)),
    )
