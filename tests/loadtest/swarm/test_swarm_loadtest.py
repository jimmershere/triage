"""Smoke tests for the swarm load suite.

These tests exercise the three new modules end-to-end at the smallest
configuration so they're safe to run as part of `scripts/run-tests.sh`
without taking minutes. Larger profile runs are exercised manually
through the CLI.
"""
from __future__ import annotations

import unittest

from tests.loadtest.swarm import (
    PROFILE_SIZES,
    SIZE_BYTES,
    SwarmLoadRunner,
    shard_perf,
    summarize_speedup,
    tier2_perf,
)


class SwarmLoadRunnerTests(unittest.TestCase):
    def test_runner_reuses_upstream_size_buckets(self) -> None:
        # Sanity: the new runner re-exports the upstream profile/size
        # tables so callers never see drifted values.
        self.assertEqual(SIZE_BYTES["512k"], 512 * 1024)
        self.assertIn("512k", PROFILE_SIZES["smoke"])

    def test_quick_run_produces_summary(self) -> None:
        runner = SwarmLoadRunner(repeats=1, concurrency=1, pool_mode="serial")
        state = runner.run_batch(types=["837p"], sizes=["512k"])
        self.assertEqual(state["status"], "complete")
        self.assertIsNotNone(state["summary"])
        summary = state["summary"]
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["passed"] + summary["failed"], 1)
        # One row in the results matrix.
        self.assertEqual(len(state["results"]), 1)
        row = state["results"][0]
        self.assertEqual(row["type"], "837p")
        self.assertEqual(row["size_label"], "512k")
        self.assertIsNotNone(row["baseline"])
        self.assertIsNotNone(row["swarm"])
        self.assertIn("p50_ms", row["baseline"])
        self.assertIn("p50_ms", row["swarm"])

    def test_invalid_pool_mode_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SwarmLoadRunner(pool_mode="async")

    def test_unknown_types_or_sizes_filtered(self) -> None:
        runner = SwarmLoadRunner(repeats=1, concurrency=1, pool_mode="serial")
        state = runner.run_batch(types=["999"], sizes=["512k"])
        # 999 isn't in SWARM_VALID_TYPES — runner should short-circuit
        # to a clean summary with zero rows rather than crashing.
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["summary"]["total"], 0)

    def test_summary_helper_returns_baseline_swarm_and_speedup(self) -> None:
        summary = summarize_speedup(
            {"baseline_ms": [10.0, 12.0, 8.0], "swarm_ms": [4.0, 5.0, 3.0]}
        )
        self.assertIn("baseline_ms", summary)
        self.assertIn("swarm_ms", summary)
        self.assertGreater(summary["p50_speedup_x"], 1.0)


class Tier2PerfTests(unittest.TestCase):
    def test_tier2_perf_shows_real_parallel_speedup(self) -> None:
        # 20ms latency × 4 specialists + 1 supervisor:
        #   serial   ≈ 100ms (5 × 20ms)
        #   parallel ≈ 20ms (specialists) + 20ms (supervisor) = ~40ms
        # We assert a generous lower bound so this is stable in CI.
        report = tier2_perf(simulated_latency_ms=20.0, repeats=3, max_workers=4)
        self.assertEqual(report["agents"], 4)
        self.assertEqual(report["max_workers"], 4)
        self.assertGreaterEqual(
            report["speedup_x"],
            1.5,
            msg=f"expected >=1.5x speedup, got {report}",
        )
        self.assertGreater(report["serial"]["p50_ms"], report["parallel"]["p50_ms"])


class ShardPerfTests(unittest.TestCase):
    def test_shard_perf_runs_all_modes(self) -> None:
        report = shard_perf(st_count=2, claims_per_st=4, repeats=1)
        self.assertEqual(set(report["per_mode"].keys()), {"serial", "thread", "process"})
        self.assertEqual(report["fixture"]["st_count"], 2)
        self.assertEqual(report["fixture"]["shard_count"], 2)
        # Each mode must record a p50 time.
        for mode, info in report["per_mode"].items():
            self.assertGreater(info["p50_ms"], 0.0, msg=f"{mode} did not record samples")
        self.assertIn(report["fastest_mode"], {"serial", "thread", "process"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
