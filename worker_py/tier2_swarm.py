"""TurboHEDI Tier 2 — Supervised Swarm via Ollama on Xander GPUs.

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

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

import urllib.request
import urllib.error

from file_profiler import FileProfile, _parse_segments
from complexity_scorer import ComplexityScore
from routing_engine import RoutingDecision
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
    """One unit of work in the analysis swarm."""
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
    ollama_url: str = "http://192.168.1.206:11434",
    ollama_model: str = "qwen2.5-coder:7b",
) -> TierResult:
    """Run parallel AI analysis swarm and supervised merge."""
    # Build a truncated excerpt for prompts
    excerpt = _build_excerpt(text, profile)
    context_block = _build_context_block(profile, score, decision)

    # Define swarm tasks
    tasks = [
        SwarmTask(
            name="structural_analysis",
            prompt=_structural_prompt(excerpt, context_block),
        ),
        SwarmTask(
            name="anomaly_diagnosis",
            prompt=_anomaly_prompt(excerpt, context_block, profile),
        ),
        SwarmTask(
            name="compliance_check",
            prompt=_compliance_prompt(excerpt, context_block, profile),
        ),
        SwarmTask(
            name="correction_proposal",
            prompt=_correction_prompt(excerpt, context_block, profile),
        ),
    ]

    # Run tasks in parallel on Xander GPUs
    logger.info(
        "Launching swarm of %d tasks for file %s on %s/%s",
        len(tasks), profile.file_id, ollama_url, ollama_model,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        futures = {
            pool.submit(_run_ollama, task, ollama_url, ollama_model): task
            for task in tasks
        }
        for future in as_completed(futures, timeout=_CALL_TIMEOUT * len(tasks)):
            task = futures[future]
            try:
                future.result()
            except Exception as exc:
                task.error = str(exc)
                logger.warning("Swarm task %s failed: %s", task.name, exc)

    completed = [t for t in tasks if t.response and not t.error]
    failed = [t for t in tasks if t.error]

    logger.info(
        "Swarm results for %s: %d completed, %d failed (%.1fms total)",
        profile.file_id,
        len(completed),
        len(failed),
        sum(t.duration_ms for t in tasks),
    )

    # Run supervisor synthesis
    supervisor_result = _run_supervisor(
        tasks, profile, score, decision,
        ollama_url=ollama_url, ollama_model=ollama_model,
    )

    # Build tier result
    ai_analyses = []
    for task in tasks:
        ai_analyses.append({
            "task": task.name,
            "response": task.response,
            "duration_ms": round(task.duration_ms, 2),
            "error": task.error,
        })

    flags = []
    recommendations = []

    verdict = supervisor_result.get("verdict", "FLAG")
    confidence = supervisor_result.get("confidence", 0.0)
    summary = supervisor_result.get("summary", "")
    issues = supervisor_result.get("issues", [])

    if verdict == "REJECT":
        flags.append("SWARM_REJECTED")
        flags.append("AUTO_PROCESSING_BLOCKED")
    elif verdict == "FLAG":
        flags.append("SWARM_FLAGGED")
        flags.append("POST_PROCESSING_REVIEW")
    else:
        flags.append("SWARM_APPROVED")

    if confidence < 0.6:
        flags.append(f"LOW_SWARM_CONFIDENCE:{confidence:.2f}")

    for issue in issues:
        flags.append(f"SWARM_ISSUE:{issue}")

    if summary:
        recommendations.append(summary)

    if failed:
        recommendations.append(
            f"{len(failed)} swarm task(s) failed: {[t.name for t in failed]}"
        )

    status_map = {"APPROVE": "completed", "FLAG": "needs_review", "REJECT": "parked"}
    status = status_map.get(verdict, "needs_review")

    return TierResult(
        tier=2,
        status=status,
        ai_analyses=ai_analyses,
        flags=flags,
        recommendations=recommendations,
        swarm_task_count=len(tasks),
        supervisor_verdict=verdict,
    )


def _run_ollama(task: SwarmTask, url: str, model: str) -> None:
    """Execute a single Ollama generation call."""
    start = time.perf_counter()
    payload = json.dumps({
        "model": model,
        "prompt": task.prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": _MAX_PREDICT,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=_CALL_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            task.response = body.get("response", "")
    except urllib.error.URLError as exc:
        task.error = f"Ollama unreachable: {exc}"
        raise
    except Exception as exc:
        task.error = str(exc)
        raise
    finally:
        task.duration_ms = (time.perf_counter() - start) * 1000


def _run_supervisor(
    tasks: list[SwarmTask],
    profile: FileProfile,
    score: ComplexityScore,
    decision: RoutingDecision,
    *,
    ollama_url: str,
    ollama_model: str,
) -> dict[str, Any]:
    """Synthesize swarm results into a single supervised verdict."""
    task_summaries = []
    for task in tasks:
        if task.response:
            # Truncate long responses for the supervisor prompt
            resp = task.response[:600]
            task_summaries.append(f"### {task.name}\n{resp}")
        elif task.error:
            task_summaries.append(f"### {task.name}\nFAILED: {task.error}")

    prompt = f"""You are a healthcare EDI quality supervisor. You have received analysis from {len(tasks)} specialist agents examining a complex EDI file.

File summary:
- Standard: {profile.document_standard}, Version: {profile.document_version}
- Transactions: {profile.transaction_count}, Segments: {profile.segment_count}, Size: {profile.byte_size} bytes
- Complexity score: {score.total_score:.1f}/100 (Tier {decision.tier})
- Routing gates triggered: {decision.gate_triggers or 'none'}
- Reason codes: {score.reason_codes or 'none'}

Agent analyses:
{chr(10).join(task_summaries)}

Based on the agent analyses, provide your verdict as a JSON object with these fields:
- "verdict": one of "APPROVE", "FLAG", or "REJECT"
  - APPROVE = safe for automated processing
  - FLAG = process it but mark for human post-review
  - REJECT = do NOT auto-process, park for manual handling
- "confidence": 0.0 to 1.0 how confident you are
- "summary": 1-2 sentence explanation
- "issues": list of specific issue strings found

Respond with ONLY the JSON object, no other text."""

    supervisor_task = SwarmTask(name="supervisor", prompt=prompt)
    try:
        # Supervisor uses slightly higher token limit
        payload = json.dumps({
            "model": ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.05,
                "num_predict": _SUPERVISOR_MAX_PREDICT,
            },
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{ollama_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_CALL_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            raw = body.get("response", "")

        # Parse the JSON from the response
        return _parse_supervisor_json(raw)

    except Exception as exc:
        logger.warning("Supervisor call failed: %s — defaulting to FLAG", exc)
        return {
            "verdict": "FLAG",
            "confidence": 0.0,
            "summary": f"Supervisor synthesis failed: {exc}",
            "issues": ["supervisor_unavailable"],
        }


def _parse_supervisor_json(raw: str) -> dict[str, Any]:
    """Extract JSON from supervisor response, handling markdown fences."""
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        result = json.loads(text)
        # Validate required fields
        if "verdict" not in result:
            result["verdict"] = "FLAG"
        if result["verdict"] not in ("APPROVE", "FLAG", "REJECT"):
            result["verdict"] = "FLAG"
        result.setdefault("confidence", 0.5)
        result.setdefault("summary", "")
        result.setdefault("issues", [])
        return result
    except json.JSONDecodeError:
        # Try to find JSON in the response
        import re
        match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {
            "verdict": "FLAG",
            "confidence": 0.3,
            "summary": f"Could not parse supervisor response",
            "issues": ["supervisor_parse_failure"],
        }


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
