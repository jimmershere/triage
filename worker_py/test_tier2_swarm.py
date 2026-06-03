"""Tests for the tier-2 swarm executor.

After the parallelism fix, ``execute_swarm`` must:

- run all four specialist agents through :class:`SwarmRunner` (not a
  ``ThreadPoolExecutor(max_workers=1)``),
- preserve the legacy :class:`TierResult` shape so the worker / tier
  executor keep working without changes,
- and produce deterministic results when the LLM transport is mocked.
"""
from __future__ import annotations

import json
import threading
import time
import unittest

from complexity_scorer import ComplexityScore
from file_profiler import FileProfile
from routing_engine import RoutingDecision
from swarms import MockLlmClient, SwarmRunner
from tier2_swarm import execute_swarm


def _supervisor_json(
    *, verdict: str = "APPROVE", confidence: float = 0.9, summary: str = "ok"
) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "confidence": confidence,
            "summary": summary,
            "issues": [],
            "actions": [],
        }
    )


def _make_inputs() -> tuple[FileProfile, ComplexityScore, RoutingDecision]:
    profile = FileProfile(
        file_id="test-file-1",
        received_at="2026-06-03T00:00:00Z",
        document_standard="X12",
        document_version="005010X222A1",
        transaction_sets=["837"],
        segment_count=200,
        transaction_count=1,
        byte_size=4096,
    )
    score = ComplexityScore(
        total_score=55.0,
        tier_suggestion=2,
        confidence=0.8,
        factors=[],
        reason_codes=["validation_anomalies"],
    )
    decision = RoutingDecision(
        routing_decision_id="rd-1",
        file_id=profile.file_id,
        tier=2,
        tier_label="supervised_swarm",
        score_total=55.0,
        gate_triggers=["medium_complexity"],
        explanation="medium complexity file",
        policy_version="1.0.0",
        scorer_version="1.0.0",
    )
    return profile, score, decision


class Tier2SwarmTests(unittest.TestCase):
    def test_execute_swarm_returns_tier_result_with_four_agents(self) -> None:
        profile, score, decision = _make_inputs()

        def respond(prompt: str) -> str:
            if "Reply with ONLY a JSON object" in prompt:
                return _supervisor_json(verdict="APPROVE", confidence=0.92)
            # All four specialist prompts route here.
            return f"analysis ok ({len(prompt)} chars)"

        client = MockLlmClient(respond)
        runner = SwarmRunner(client, max_workers=4, max_retries=0)

        result = execute_swarm(
            profile, score, decision, "ISA...~",
            llm_client=client,
            swarm_runner=runner,
        )

        # Four specialist agents must be reflected in ai_analyses.
        self.assertEqual(result.swarm_task_count, 4)
        self.assertEqual(len(result.ai_analyses), 4)
        names = {entry["task"] for entry in result.ai_analyses}
        self.assertEqual(
            names,
            {"structural_analysis", "anomaly_diagnosis", "compliance_check", "correction_proposal"},
        )
        self.assertEqual(result.supervisor_verdict, "APPROVE")
        self.assertEqual(result.status, "completed")
        self.assertIn("SWARM_APPROVED", result.flags)
        # Every specialist agent succeeded so no failure recommendations.
        self.assertNotIn(
            "swarm agent(s) failed",
            " ".join(result.recommendations or []),
            msg=result.recommendations,
        )

    def test_reject_verdict_blocks_auto_processing(self) -> None:
        profile, score, decision = _make_inputs()
        client = MockLlmClient(
            lambda prompt: _supervisor_json(verdict="REJECT", confidence=0.3)
            if "Reply with ONLY a JSON object" in prompt
            else "agent says trouble"
        )
        runner = SwarmRunner(client, max_workers=4, max_retries=0)

        result = execute_swarm(
            profile, score, decision, "ISA...~",
            llm_client=client,
            swarm_runner=runner,
        )

        self.assertEqual(result.supervisor_verdict, "REJECT")
        self.assertEqual(result.status, "parked")
        self.assertIn("SWARM_REJECTED", result.flags)
        self.assertIn("AUTO_PROCESSING_BLOCKED", result.flags)
        # Low-confidence flag piggy-backs on the reject.
        self.assertTrue(any(f.startswith("LOW_SWARM_CONFIDENCE") for f in result.flags))

    def test_specialist_agents_run_concurrently(self) -> None:
        """If SwarmRunner truly fans out, four 20ms sleeps complete in
        < 80ms wall-clock (and well under the legacy serial path).

        We assert a generous bound to stay non-flaky in CI."""
        profile, score, decision = _make_inputs()
        sleep_s = 0.02
        max_concurrency = 0
        active = 0
        lock = threading.Lock()

        def slow_respond(prompt: str) -> str:
            nonlocal active, max_concurrency
            if "Reply with ONLY a JSON object" in prompt:
                return _supervisor_json()
            with lock:
                active += 1
                max_concurrency = max(max_concurrency, active)
            try:
                time.sleep(sleep_s)
            finally:
                with lock:
                    active -= 1
            return "ok"

        client = MockLlmClient(slow_respond)
        runner = SwarmRunner(client, max_workers=4, max_retries=0)

        start = time.perf_counter()
        execute_swarm(
            profile, score, decision, "ISA...~",
            llm_client=client,
            swarm_runner=runner,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        self.assertGreaterEqual(
            max_concurrency,
            2,
            msg="SwarmRunner should run specialist agents concurrently",
        )
        # 4 specialist + 1 supervisor sleeps in serial would be ~100ms; in
        # parallel the four specialist sleeps overlap → ~20ms + supervisor.
        # Generous bound: must be well under the serial budget of 100ms.
        self.assertLess(elapsed_ms, 100.0, f"tier-2 swarm did not parallelize: {elapsed_ms:.1f}ms")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
