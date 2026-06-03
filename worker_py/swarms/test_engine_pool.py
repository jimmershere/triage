"""Tests for :mod:`swarms.engine_pool`."""
from __future__ import annotations

import time
import unittest

from swarms.engine_pool import EnginePool, PoolStats


def _square(x: int) -> int:
    return x * x


def _sleepy(x: int) -> int:
    time.sleep(0.02)
    return x


class EnginePoolBasicsTests(unittest.TestCase):
    def test_serial_mode_runs_in_order(self) -> None:
        pool = EnginePool(mode="serial", max_workers=4)
        self.assertEqual(pool.map(_square, [1, 2, 3, 4]), [1, 4, 9, 16])

    def test_thread_mode_preserves_input_order(self) -> None:
        pool = EnginePool(mode="thread", max_workers=4)
        self.assertEqual(pool.map(_square, [5, 6, 7]), [25, 36, 49])

    def test_process_mode_preserves_input_order(self) -> None:
        # Process pools need a top-level callable (already true for _square).
        pool = EnginePool(mode="process", max_workers=2)
        self.assertEqual(pool.map(_square, [3, 4, 5]), [9, 16, 25])

    def test_empty_input_returns_empty_list(self) -> None:
        pool = EnginePool(mode="thread")
        self.assertEqual(pool.map(_square, []), [])

    def test_invalid_mode_raises(self) -> None:
        with self.assertRaises(ValueError):
            EnginePool(mode="async")

    def test_max_workers_floor_is_one(self) -> None:
        pool = EnginePool(mode="thread", max_workers=0)
        # The floor protects against div-by-zero / 0-worker pools.
        self.assertEqual(pool.max_workers, 1)
        self.assertEqual(pool.map(_square, [3]), [9])


class EnginePoolStatsTests(unittest.TestCase):
    def test_stats_capture_worker_count(self) -> None:
        pool = EnginePool(mode="thread", max_workers=8)
        results, stats = pool.map_with_stats(_square, [1, 2, 3])
        self.assertEqual(results, [1, 4, 9])
        self.assertIsInstance(stats, PoolStats)
        # Worker count is min(max_workers, items).
        self.assertEqual(stats.worker_count, 3)
        self.assertEqual(stats.item_count, 3)
        self.assertEqual(stats.mode, "thread")
        self.assertGreaterEqual(stats.duration_ms, 0.0)

    def test_thread_pool_runs_concurrently(self) -> None:
        # If thread fan-out actually overlaps, 4 sleeps of 20ms should
        # complete in significantly less than 4 * 20ms = 80ms. We assert a
        # generous bound to avoid CI flakiness.
        pool = EnginePool(mode="thread", max_workers=4)
        start = time.perf_counter()
        pool.map(_sleepy, [1, 2, 3, 4])
        elapsed_ms = (time.perf_counter() - start) * 1000
        self.assertLess(elapsed_ms, 60, f"thread pool did not overlap: {elapsed_ms:.1f}ms")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
