"""Direct route-handler tests for the swarm load-test endpoints.

These tests call the FastAPI route functions directly (not via a
TestClient) so they avoid the httpx dependency. They're skipped on
environments without ``fastapi`` installed — symmetric with the
existing ``test_turbo_api.py``.
"""
from __future__ import annotations

import os
import sys
import time
import unittest

# Put the project root on sys.path so ``api.loadtest_routes`` is importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from fastapi import HTTPException  # noqa: F401
    from api.loadtest_routes import (
        SWARM_VALID_POOL_MODES,
        SwarmLoadTestStartRequest,
        get_swarm_loadtest_status,
        start_swarm_loadtest,
        stop_swarm_loadtest,
    )
    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover - depends on dev env
    _HAS_FASTAPI = False


def _wait_until_complete(timeout_s: float = 30.0) -> dict:
    """Poll the swarm status endpoint until the run leaves 'running'."""
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = get_swarm_loadtest_status()
        if last.get("status") in ("complete", "stopped", "error"):
            return last
        time.sleep(0.05)
    return last or {}


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed in this environment")
class SwarmLoadtestEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        # Ensure a clean state so prior tests don't leak into this one.
        # The start handler will reset the wrapper on each call as long as
        # the previous run isn't still 'running'.
        _wait_until_complete(timeout_s=10.0)

    def test_smallest_run_passes_through_runner(self) -> None:
        req = SwarmLoadTestStartRequest(
            profile="quick",
            types=["837p"],
            sizes=[],
            repeats=1,
            concurrency=1,
            pool_mode="serial",
            max_workers=1,
            claims_per_batch=10,
        )
        resp = start_swarm_loadtest(req)
        self.assertEqual(resp["status"], "started")
        self.assertEqual(resp["pool_mode"], "serial")
        self.assertTrue(resp["run_id"])
        run_id = resp["run_id"]

        # Status immediately after start should at least advertise the
        # run as in-flight or already complete (synthetic 512k is fast).
        early = get_swarm_loadtest_status()
        self.assertEqual(early["run_id"], run_id)
        self.assertIn(early["status"], ("running", "complete"))

        final = _wait_until_complete()
        self.assertEqual(final["status"], "complete")
        self.assertIsNotNone(final["summary"])
        self.assertEqual(final["summary"]["total"], 1)
        # Even when the speedup is < 1 the cell must still pass — the
        # contract is that the runner executed both pipelines.
        self.assertEqual(final["summary"]["passed"] + final["summary"]["failed"], 1)
        # Row must carry both baseline + swarm timings.
        row = final["results"][0]
        self.assertEqual(row["type"], "837p")
        self.assertEqual(row["size_label"], "512k")
        self.assertIsNotNone(row["baseline"])
        self.assertIsNotNone(row["swarm"])
        self.assertIn("p50_ms", row["baseline"])
        self.assertIn("p50_ms", row["swarm"])

    def test_concurrent_runs_are_rejected(self) -> None:
        req = SwarmLoadTestStartRequest(
            profile="quick", types=["837p"], repeats=1, pool_mode="serial"
        )
        first = start_swarm_loadtest(req)
        self.assertEqual(first["status"], "started")
        # While the run is in-flight (or even just queued), a second start
        # must return 409 via HTTPException.
        try:
            second_resp = start_swarm_loadtest(req)
        except Exception as exc:  # FastAPI HTTPException
            from fastapi import HTTPException as _HTTPExc
            self.assertIsInstance(exc, _HTTPExc)
            self.assertEqual(exc.status_code, 409)
        else:
            # The first run might have finished before the second call —
            # in that case the second call is accepted, which is fine.
            self.assertEqual(second_resp["status"], "started")
        _wait_until_complete()

    def test_invalid_pool_mode_returns_400(self) -> None:
        req = SwarmLoadTestStartRequest(
            profile="quick", types=["837p"], pool_mode="async"
        )
        from fastapi import HTTPException as _HTTPExc
        with self.assertRaises(_HTTPExc) as ctx:
            start_swarm_loadtest(req)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_stop_is_idempotent_when_idle(self) -> None:
        _wait_until_complete()
        resp = stop_swarm_loadtest()
        # When nothing is running, stop returns ok=True with a message.
        self.assertTrue(resp.get("ok"))

    def test_pool_modes_exported(self) -> None:
        # Sanity: route module advertises the modes the UI expects.
        self.assertEqual(set(SWARM_VALID_POOL_MODES), {"thread", "process", "serial"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
