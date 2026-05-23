"""TurboHEDI Tier 1 — Assisted Review with enhanced validation rules.

Applies deeper deterministic checks beyond what the standard harness does:
- Cross-claim consistency (duplicate claim IDs, amount outliers)
- Partner pattern deviation (segment counts vs historical norms)
- Structural anomaly detail extraction
- Enriched audit trail with specific flag reasons

No AI/GPU needed. Pure rules engine on top of the profile + raw text.
"""
from __future__ import annotations

import logging
import re
import statistics
from typing import Any

from file_profiler import FileProfile, _parse_segments
from complexity_scorer import ComplexityScore
from routing_engine import RoutingDecision
from tier_executor import TierResult

logger = logging.getLogger("tier1_assist")


def run_assisted_review(
    profile: FileProfile,
    score: ComplexityScore,
    decision: RoutingDecision,
    text: str,
) -> TierResult:
    """Run enhanced deterministic checks for Tier 1 files."""
    flags: list[str] = []
    recommendations: list[str] = []

    # --- Check 1: Duplicate claim IDs ---
    claim_ids = _extract_claim_ids(text)
    dupes = _find_duplicates(claim_ids)
    if dupes:
        flags.append(f"DUPLICATE_CLAIM_IDS:{len(dupes)}")
        recommendations.append(
            f"Found {len(dupes)} duplicate claim ID(s): {', '.join(list(dupes)[:5])}"
        )

    # --- Check 2: Claim amount outliers ---
    amounts = _extract_claim_amounts(text)
    if len(amounts) >= 3:
        outliers = _detect_amount_outliers(amounts)
        if outliers:
            flags.append(f"AMOUNT_OUTLIERS:{len(outliers)}")
            recommendations.append(
                f"{len(outliers)} claim amount(s) are statistical outliers "
                f"(>{2}σ from mean ${statistics.mean(amounts):.2f})"
            )

    # --- Check 3: Segment density anomaly ---
    if profile.byte_size > 0:
        density = profile.segment_count / max(profile.byte_size / 1024, 0.1)
        if density > 100:
            flags.append(f"HIGH_SEGMENT_DENSITY:{density:.0f}")
            recommendations.append(
                f"Unusually dense file: {density:.0f} segments/KB (normal ~40)"
            )
        elif density < 10 and profile.segment_count > 10:
            flags.append(f"LOW_SEGMENT_DENSITY:{density:.0f}")
            recommendations.append(
                f"Unusually sparse file: {density:.0f} segments/KB — may contain embedded data"
            )

    # --- Check 4: Mixed transaction types ---
    if profile.mixed_transaction_types:
        flags.append("MIXED_TX_TYPES")
        recommendations.append(
            f"File contains mixed transaction types: {profile.transaction_sets}. "
            "Verify each type maps to the correct processing path."
        )

    # --- Check 5: Control structure integrity ---
    ctrl_issues = _check_control_structure(text)
    if ctrl_issues:
        flags.extend(ctrl_issues)
        recommendations.append(
            f"Control structure issues found: {', '.join(ctrl_issues)}"
        )

    # --- Check 6: Large claim count warning ---
    if profile.transaction_count > 100:
        flags.append(f"HIGH_CLAIM_VOLUME:{profile.transaction_count}")
        recommendations.append(
            f"High volume file: {profile.transaction_count} transactions. "
            "Consider batch processing with checkpointing."
        )

    # --- Check 7: Schema version mismatch ---
    if profile.document_version and "005010" not in (profile.document_version or ""):
        flags.append(f"NON_5010_VERSION:{profile.document_version}")
        recommendations.append(
            f"Document version {profile.document_version} is not 005010. "
            "Verify translator supports this version."
        )

    # --- Check 8: New/unknown partner with anomalies ---
    if profile.partner_reliability_tier in ("new", "unknown") and profile.validation_anomaly_count > 0:
        flags.append("NEW_PARTNER_WITH_ANOMALIES")
        recommendations.append(
            "New/unknown trading partner with validation anomalies. "
            "Recommend manual spot-check of first few claims."
        )

    status = "completed" if not any("HUMAN" in f for f in flags) else "needs_review"

    logger.info(
        "Tier 1 review for %s: %d flags, %d recommendations",
        profile.file_id, len(flags), len(recommendations),
    )

    return TierResult(
        tier=1,
        status=status,
        flags=flags,
        recommendations=recommendations,
    )


def _extract_claim_ids(text: str) -> list[str]:
    """Extract CLM segment claim IDs from X12 text."""
    segments = _parse_segments(text)
    ids = []
    for seg in segments:
        if seg and seg[0].upper() == "CLM" and len(seg) > 1:
            ids.append(seg[1])
    return ids


def _find_duplicates(items: list[str]) -> set[str]:
    """Find items that appear more than once."""
    seen: set[str] = set()
    dupes: set[str] = set()
    for item in items:
        if item in seen:
            dupes.add(item)
        seen.add(item)
    return dupes


def _extract_claim_amounts(text: str) -> list[float]:
    """Extract CLM segment amounts (element 2) from X12 text."""
    segments = _parse_segments(text)
    amounts = []
    for seg in segments:
        if seg and seg[0].upper() == "CLM" and len(seg) > 2:
            try:
                amounts.append(float(seg[2]))
            except (ValueError, TypeError):
                pass
    return amounts


def _detect_amount_outliers(amounts: list[float], z_threshold: float = 2.0) -> list[float]:
    """Find amounts more than z_threshold standard deviations from the mean."""
    if len(amounts) < 3:
        return []
    mean = statistics.mean(amounts)
    stdev = statistics.stdev(amounts)
    if stdev == 0:
        return []
    return [a for a in amounts if abs(a - mean) / stdev > z_threshold]


def _check_control_structure(text: str) -> list[str]:
    """Verify ISA/IEA, GS/GE, ST/SE envelope counts match."""
    segments = _parse_segments(text)
    issues = []

    isa_count = sum(1 for s in segments if s and s[0].upper() == "ISA")
    iea_count = sum(1 for s in segments if s and s[0].upper() == "IEA")
    gs_count = sum(1 for s in segments if s and s[0].upper() == "GS")
    ge_count = sum(1 for s in segments if s and s[0].upper() == "GE")
    st_count = sum(1 for s in segments if s and s[0].upper() == "ST")
    se_count = sum(1 for s in segments if s and s[0].upper() == "SE")

    if isa_count != iea_count:
        issues.append(f"ISA_IEA_MISMATCH:{isa_count}v{iea_count}")
    if gs_count != ge_count:
        issues.append(f"GS_GE_MISMATCH:{gs_count}v{ge_count}")
    if st_count != se_count:
        issues.append(f"ST_SE_MISMATCH:{st_count}v{se_count}")

    # Verify SE segment counts match actual segment counts
    for seg in segments:
        if seg and seg[0].upper() == "SE" and len(seg) > 1:
            try:
                declared = int(seg[1])
                # SE count includes ST and SE themselves
                # This is a heuristic check — exact validation is in the harness
                if declared < 2:
                    issues.append(f"SE_COUNT_TOO_LOW:{declared}")
            except (ValueError, TypeError):
                issues.append("SE_COUNT_UNPARSEABLE")

    return issues
