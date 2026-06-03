"""Triage Tier 2 — Supervised Swarm via Ollama.

Spawns parallel AI analysis tasks for complex EDI files:
1. Structural Analysis  — layout, nesting, segment flow diagnosis
2. Anomaly Diagnosis    — explain validation errors, suggest root causes
3. Compliance Check     — flag regulatory/HIPAA concerns in claim data
4. Correction Proposal  — suggest specific edits to fix identified issues

Each task runs as a separate Ollama generation call. Results are merged
and a supervisor prompt synthesizes a final verdict with confidence score.

The supervisor can:
- APPROVE: allow automated processing to continue
- FLAG: process but mark for post-processing human review
- REJECT: park the file, do not auto-process

All prompts, responses, and the supervisor verdict are logged for audit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from complexity_scorer import ComplexityScore
from file_profiler import FileProfile, _parse_segments
from routing_engine import RoutingDecision
from swarms import (
    LlmClient,
    OllamaClient,
    SwarmAgent,
    SwarmResult,
    SwarmRunner,
)
from tier_executor import TierResult

logger = logging.getLogger("tier2_swarm")

# Max tokens for each swarm task response
_MAX_PREDICT = 400
# Max tokens for supervisor synthesis
_SUPERVISOR_MAX_PREDICT = 400
# Timeout per Ollama call in seconds
_CALL_TIMEOUT = 90
# Max segment text to include in prompts (chars)
_MAX_CONTEXT_CHARS = 2000


@dataclass
class SwarmTask:
    """One unit of work in the analysis swarm.

    Retained as a public symbol for callers that import it; the
    coordinator now executes these as :class:`swarms.SwarmAgent`
    instances under :class:`swarms.SwarmRunner` so the four specialist
    Ollama calls actually run in parallel instead of being serialized.
    """
    name: str
    prompt: str
    response: str | None = None
    duration_ms: float = 0.0
    error: str | None = None


def execute_swarm(
    profile: FileProfile,
    score: ComplexityScore,
    decision: RoutingDecision,
    text: str,
    *,
    ollama_url: str = "http://localhost:11434",
    ollama_model: str = "qwen2.5-coder:7b",
    llm_client: LlmClient | None = None,
    swarm_runner: SwarmRunner | None = None,
) -> TierResult:
    """Run the parallel tier-2 specialist swarm and a supervised merge.

    This was previously implemented as a ``ThreadPoolExecutor(max_workers=1)``
    around four direct Ollama HTTP calls — i.e. fully serial despite the
    pool. It is now routed through :class:`swarms.SwarmRunner`, which:

    - actually fans the four specialist calls out across worker threads,
    - captures an audit trail (prompt + response + timing per call),
    - retries transient transport errors,
    - and produces a single supervisor :class:`SupervisorVerdict` over
      the agent responses.

    The function signature is preserved so :mod:`tier_executor` and the
    worker continue to work without changes; ``llm_client`` and
    ``swarm_runner`` are new injection seams for tests and for
    coordinator-driven supervisor calls.
    """
    excerpt = _build_excerpt(text, profile)
    context_block = _build_context_block(profile, score, decision)

    agent_options = {"temperature": 0.1, "num_predict": _MAX_PREDICT}
    supervisor_options = {"temperature": 0.05, "num_predict": _SUPERVISOR_MAX_PREDICT}

    agent_specs = [
        ("structural_analysis", _structural_prompt(excerpt, context_block)),
        ("anomaly_diagnosis", _anomaly_prompt(excerpt, context_block, profile)),
        ("compliance_check", _compliance_prompt(excerpt, context_block, profile)),
        ("correction_proposal", _correction_prompt(excerpt, context_block, profile)),
    ]
    agents = [
        SwarmAgent(name=name, prompt=prompt, options=dict(agent_options))
        for name, prompt in agent_specs
    ]

    client = llm_client or OllamaClient(
        url=ollama_url, model=ollama_model, timeout=_CALL_TIMEOUT
    )
    runner = swarm_runner or SwarmRunner(
        client,
        max_workers=len(agents),
        per_call_timeout=_CALL_TIMEOUT,
        max_retries=1,
    )

    logger.info(
        "Launching tier-2 swarm of %d agents for file %s on %s/%s",
        len(agents), profile.file_id, ollama_url, ollama_model,
    )

    supervisor_context = (
        f"{context_block}\n\n"
        "You are the tier-2 healthcare EDI quality supervisor. Synthesize "
        "the four specialist analyses into a single verdict:\n"
        "- APPROVE: safe for automated processing\n"
        "- FLAG: process it but mark for human post-review\n"
        "- REJECT: do NOT auto-process, park for manual handling"
    )

    swarm_result: SwarmResult = runner.run(
        workload="tier2_swarm",
        agents=agents,
        supervisor_context=supervisor_context,
        supervisor_options=supervisor_options,
    )

    return _tier_result_from(swarm_result, agent_count=len(agents))


def _tier_result_from(swarm_result: SwarmResult, *, agent_count: int) -> TierResult:
    """Translate a :class:`swarms.SwarmResult` into the worker's TierResult."""
    ai_analyses: list[dict[str, Any]] = []
    failed_names: list[str] = []
    for agent in swarm_result.agents:
        ai_analyses.append(
            {
                "task": agent.name,
                "response": agent.response,
                "duration_ms": round(agent.duration_ms, 2),
                "error": agent.error,
                "attempts": agent.attempts,
            }
        )
        if not agent.succeeded:
            failed_names.append(agent.name)

    verdict_obj = swarm_result.supervisor
    verdict = getattr(verdict_obj, "verdict", "FLAG") if verdict_obj else "FLAG"
    confidence = float(getattr(verdict_obj, "confidence", 0.0)) if verdict_obj else 0.0
    summary = getattr(verdict_obj, "summary", "") if verdict_obj else ""
    issues = list(getattr(verdict_obj, "issues", [])) if verdict_obj else []

    flags: list[str] = []
    if verdict == "REJECT":
        flags.extend(["SWARM_REJECTED", "AUTO_PROCESSING_BLOCKED"])
    elif verdict == "FLAG":
        flags.extend(["SWARM_FLAGGED", "POST_PROCESSING_REVIEW"])
    else:
        flags.append("SWARM_APPROVED")

    if confidence < 0.6:
        flags.append(f"LOW_SWARM_CONFIDENCE:{confidence:.2f}")

    for issue in issues:
        flags.append(f"SWARM_ISSUE:{issue}")

    recommendations: list[str] = []
    if summary:
        recommendations.append(summary)
    if failed_names:
        recommendations.append(
            f"{len(failed_names)} swarm agent(s) failed: {failed_names}"
        )

    status_map = {"APPROVE": "completed", "FLAG": "needs_review", "REJECT": "parked"}
    status = status_map.get(verdict, "needs_review")

    return TierResult(
        tier=2,
        status=status,
        ai_analyses=ai_analyses,
        flags=flags,
        recommendations=recommendations,
        swarm_task_count=agent_count,
        supervisor_verdict=verdict,
    )


def _build_excerpt(text: str, profile: FileProfile) -> str:
    """Build a representative excerpt of the EDI file for prompts."""
    if len(text) <= _MAX_CONTEXT_CHARS:
        return text

    # Include header (ISA/GS/ST), a middle sample, and trailer (SE/GE/IEA)
    segments = _parse_segments(text)
    if not segments:
        return text[:_MAX_CONTEXT_CHARS]

    # Take first 20 segments, middle 20, last 20
    n = len(segments)
    head = segments[:20]
    mid_start = max(20, n // 2 - 10)
    mid = segments[mid_start:mid_start + 20]
    tail = segments[max(0, n - 20):]

    sep = "~"
    if text.startswith("ISA") and len(text) >= 106:
        sep = text[105]
    elem_sep = "*"
    if text.startswith("ISA") and len(text) >= 4:
        elem_sep = text[3]

    parts = []
    parts.append(f"[First 20 of {n} segments]")
    parts.append(sep.join(elem_sep.join(s) for s in head))
    parts.append(f"\n[Middle sample at segment {mid_start}]")
    parts.append(sep.join(elem_sep.join(s) for s in mid))
    parts.append(f"\n[Last 20 segments]")
    parts.append(sep.join(elem_sep.join(s) for s in tail))

    return "\n".join(parts)


def _build_context_block(
    profile: FileProfile,
    score: ComplexityScore,
    decision: RoutingDecision,
) -> str:
    """Build a summary context block for swarm prompts."""
    return f"""File metadata:
- Standard: {profile.document_standard}, Version: {profile.document_version}
- Transaction sets: {profile.transaction_sets}
- Segments: {profile.segment_count}, Transactions: {profile.transaction_count}
- Size: {profile.byte_size} bytes
- Partner tier: {profile.partner_reliability_tier}
- Schema confidence: {profile.schema_match_confidence:.2f}
- Map confidence: {profile.map_match_confidence:.2f}
- Validation anomalies: {profile.validation_anomaly_count} ({profile.validation_anomaly_types})
- Loop irregularities: {profile.loop_irregularity_markers}
- Complexity score: {score.total_score:.1f}/100
- Routing tier: {decision.tier} ({decision.tier_label})
- Gate triggers: {decision.gate_triggers or 'none'}"""


def _structural_prompt(excerpt: str, context: str) -> str:
    return f"""You are an EDI structural analyst. Examine this healthcare EDI file and report on:
1. Envelope structure (ISA/GS/ST nesting) — any mismatches or unusual patterns
2. HL hierarchy — is the subscriber/patient/claim nesting correct
3. Segment ordering — any segments out of expected order for this transaction type
4. Loop boundaries — are loops properly opened and closed
5. Any structural anomalies that could cause processing failures

{context}

EDI content:
{excerpt}

Provide a concise analysis with specific segment references. Focus on issues that would cause real processing problems."""


def _anomaly_prompt(excerpt: str, context: str, profile: FileProfile) -> str:
    anomaly_detail = ""
    if profile.validation_anomaly_types:
        anomaly_detail = f"\nKnown validation anomaly codes: {profile.validation_anomaly_types}"
    if profile.loop_irregularity_markers:
        anomaly_detail += f"\nLoop irregularity markers: {profile.loop_irregularity_markers}"

    return f"""You are an EDI anomaly diagnostician. This file has been flagged for complexity. Diagnose:
1. Root cause of each validation anomaly — what specifically is wrong
2. Whether anomalies are data errors, mapping errors, or format errors
3. Pattern recognition — are these anomalies correlated or independent
4. Impact assessment — which anomalies block processing vs. are cosmetic
{anomaly_detail}

{context}

EDI content:
{excerpt}

For each anomaly, state: what it is, why it happened, whether it blocks processing, and how to fix it."""


def _compliance_prompt(excerpt: str, context: str, profile: FileProfile) -> str:
    return f"""You are a healthcare EDI compliance reviewer. Check this file for:
1. HIPAA transaction set compliance (5010 requirements)
2. Required segment/element presence per implementation guide
3. Code set validity (place of service, diagnosis, procedure codes if visible)
4. NPI/identifier format correctness
5. Any data that suggests incorrect billing patterns

{context}

EDI content:
{excerpt}

Flag only real compliance concerns, not cosmetic issues. Rate each finding as CRITICAL, WARNING, or INFO."""


def _correction_prompt(excerpt: str, context: str, profile: FileProfile) -> str:
    return f"""You are an EDI correction specialist. Based on the file analysis, propose specific corrections:
1. For each structural issue — exact segment edit needed
2. For each validation error — the correct value or format
3. For missing required elements — what should be added and where
4. Priority ordering — which fixes are needed for successful processing

{context}

EDI content:
{excerpt}

Provide corrections in order of priority. For each: state the segment, the current value, the proposed fix, and why."""
