"""In-process swarm load test runner.

Generates EDI payloads at the same size buckets the HTTP load-test
runner uses (``tests.loadtest.runner.SIZE_BYTES``), runs them through
both pipelines, and records side-by-side timings:

- baseline: ``turbo_pipeline.run_pipeline`` (single-thread).
- swarm: ``swarms.SwarmCoordinator.run`` (sharded + engine-pool
  fan-out).

The runner exposes the same state shape as
:class:`tests.loadtest.runner.LoadTestRunner` (``status`` / ``progress``
/ ``results`` / ``summary``) so the existing API and UI can be wired
to it with minimal changes.

CLI::

    python -m tests.loadtest.swarm --profile smoke
    python -m tests.loadtest.swarm --types 837p 835 --sizes 512k 1mb
    python -m tests.loadtest.swarm --quick   # smallest size only, fast CI mode
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

# Reuse the upstream sizes/profiles so the swarm runner advertises the
# same surface as the canonical HTTP runner.
from tests.loadtest.runner import PROFILE_SIZES, SIZE_BYTES

# Worker code lives under worker_py/. Make sure it's importable whether
# the runner is invoked as ``python -m tests.loadtest.swarm`` from the
# repo root or from inside worker_py/.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_WORKER_DIR = _REPO_ROOT / "worker_py"
if str(_WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKER_DIR))

from swarms import EnginePool, SwarmCoordinator  # noqa: E402
from turbo_pipeline import run_pipeline  # noqa: E402

logger = logging.getLogger("loadtest.swarm.runner")

# Transaction types the swarm coordinator can meaningfully shard
# (validation engines are wired up for these). Adversarial / non-X12
# inputs are exercised separately by the upstream runner.
SWARM_VALID_TYPES = ("837p", "837i", "837d", "835")


def _generate_payload(tx_type: str, target_bytes: int) -> bytes:
    """Use the upstream generator if present; fall back to a stub."""
    try:
        from tests.generators import generate_file  # type: ignore
    except ImportError:
        logger.warning("tests.generators not available; using inline stub")

        def generate_file(tx_type: str, target_bytes: int, adversarial: bool = False) -> bytes:
            header = (
                "ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       "
                "*230101*1200*^*00501*000000001*0*P*:~\n"
            )
            content = header + ("NTE*ADD*" + "X" * 76 + "~\n") * max(1, target_bytes // 85)
            return content[:target_bytes].encode("ascii")

    payload = generate_file(tx_type=tx_type, target_bytes=target_bytes, adversarial=False)
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return payload


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    k = (len(sorted_v) - 1) * pct / 100.0
    f = int(k)
    c = min(f + 1, len(sorted_v) - 1)
    if f == c:
        return sorted_v[f]
    return sorted_v[f] + (sorted_v[c] - sorted_v[f]) * (k - f)


def summarize_speedup(samples: dict[str, list[float]]) -> dict[str, Any]:
    """Roll p50/p95/mean stats for a ``{label: [ms,...]}`` map."""
    summary: dict[str, Any] = {}
    for label, values in samples.items():
        if not values:
            summary[label] = {"runs": 0, "p50_ms": 0.0, "p95_ms": 0.0, "mean_ms": 0.0}
            continue
        summary[label] = {
            "runs": len(values),
            "p50_ms": round(statistics.median(values), 2),
            "p95_ms": round(_percentile(values, 95.0), 2),
            "mean_ms": round(statistics.mean(values), 2),
            "min_ms": round(min(values), 2),
            "max_ms": round(max(values), 2),
        }
    baseline_p50 = summary.get("baseline_ms", {}).get("p50_ms", 0.0)
    swarm_p50 = summary.get("swarm_ms", {}).get("p50_ms", 0.0)
    summary["p50_speedup_x"] = round(baseline_p50 / swarm_p50, 2) if swarm_p50 > 0 else 0.0
    return summary


class SwarmLoadRunner:
    """In-process load runner comparing baseline and sharded pipelines.

    Shape-compatible with :class:`tests.loadtest.runner.LoadTestRunner`:
    callers can read ``runner.state`` while ``run_batch()`` is in flight
    to drive a progress UI.
    """

    def __init__(
        self,
        *,
        repeats: int = 3,
        concurrency: int = 1,
        pool_mode: str = "thread",
        max_workers: int = 4,
        claims_per_batch: int = 25,
        cancel_event: threading.Event | None = None,
    ) -> None:
        if pool_mode not in ("thread", "process", "serial"):
            raise ValueError(f"pool_mode must be thread/process/serial, got {pool_mode!r}")
        self.repeats = max(1, int(repeats))
        self.concurrency = max(1, min(int(concurrency), 8))
        self.pool_mode = pool_mode
        self.max_workers = max(1, int(max_workers))
        self.claims_per_batch = max(1, int(claims_per_batch))
        self.cancel_event = cancel_event or threading.Event()

        self._lock = threading.Lock()
        self.state: dict[str, Any] = {
            "status": "idle",
            "progress": {"completed": 0, "total": 0, "pct": 0.0},
            "results": [],
            "summary": None,
        }

    # ------------------------------------------------------------------
    # Single (type, size) measurement
    # ------------------------------------------------------------------

    def measure(self, tx_type: str, size_label: str) -> dict[str, Any]:
        """Run baseline + swarm ``repeats`` times for one (type, size)."""
        target_bytes = SIZE_BYTES.get(size_label)
        if target_bytes is None:
            return {
                "type": tx_type,
                "size_label": size_label,
                "size_bytes": 0,
                "error": f"unknown size {size_label!r}",
                "result": "fail",
            }

        gen_start = time.perf_counter()
        try:
            payload = _generate_payload(tx_type, target_bytes)
        except Exception as exc:  # generator can raise for unknown types
            return {
                "type": tx_type,
                "size_label": size_label,
                "size_bytes": target_bytes,
                "error": f"generation_failed: {exc}",
                "result": "fail",
            }
        gen_ms = (time.perf_counter() - gen_start) * 1000

        # turbo_pipeline expects str.
        text = payload.decode("utf-8", errors="ignore")

        baseline_times: list[float] = []
        swarm_times: list[float] = []

        coord = SwarmCoordinator(
            thread_pool=EnginePool(mode=self.pool_mode, max_workers=self.max_workers),
            process_pool=EnginePool(mode=self.pool_mode, max_workers=self.max_workers),
            claims_per_batch=self.claims_per_batch,
        )

        for _ in range(self.repeats):
            if self.cancel_event.is_set():
                break
            t0 = time.perf_counter()
            run_pipeline(text, scrub=True, to_fhir=False, generate_acks=False)
            baseline_times.append((time.perf_counter() - t0) * 1000)

        for _ in range(self.repeats):
            if self.cancel_event.is_set():
                break
            t0 = time.perf_counter()
            coord.run(text, scrub=True, to_fhir=False, generate_acks=False)
            swarm_times.append((time.perf_counter() - t0) * 1000)

        stats = summarize_speedup(
            {"baseline_ms": baseline_times, "swarm_ms": swarm_times}
        )
        result_status = "pass" if baseline_times and swarm_times else "fail"
        return {
            "type": tx_type,
            "size_label": size_label,
            "size_bytes": target_bytes,
            "generation_ms": round(gen_ms, 2),
            "baseline": stats["baseline_ms"],
            "swarm": stats["swarm_ms"],
            "p50_speedup_x": stats["p50_speedup_x"],
            "result": result_status,
            "error": None if result_status == "pass" else "no samples collected",
        }

    # ------------------------------------------------------------------
    # Batch runner with the same state shape as the upstream LoadTestRunner
    # ------------------------------------------------------------------

    def run_batch(
        self,
        *,
        types: list[str],
        sizes: list[str],
    ) -> dict[str, Any]:
        """Run measurement across the (types × sizes) matrix."""
        types = [t.lower() for t in types if t.lower() in SWARM_VALID_TYPES]
        sizes = [s for s in sizes if s in SIZE_BYTES]
        if not types or not sizes:
            with self._lock:
                self.state["status"] = "complete"
                self.state["summary"] = {
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "avg_speedup_x": 0.0,
                    "by_type": {},
                    "by_size": {},
                }
            return dict(self.state)

        items: list[dict[str, Any]] = []
        for tx_type in types:
            for size in sizes:
                items.append(
                    {
                        "type": tx_type,
                        "size_label": size,
                        "size_bytes": SIZE_BYTES[size],
                        "status": "queued",
                        "baseline": None,
                        "swarm": None,
                        "p50_speedup_x": None,
                        "result": None,
                        "error": None,
                        "generation_ms": None,
                    }
                )

        total = len(items)
        with self._lock:
            self.state["status"] = "running"
            self.state["progress"] = {"completed": 0, "total": total, "pct": 0.0}
            self.state["results"] = items

        completed = 0

        def _run_item(item: dict[str, Any]) -> None:
            nonlocal completed
            if self.cancel_event.is_set():
                return
            item["status"] = "running"
            try:
                outcome = self.measure(item["type"], item["size_label"])
                item.update(outcome)
                item["status"] = "done" if outcome.get("result") == "pass" else "failed"
            except Exception as exc:
                item["status"] = "failed"
                item["result"] = "fail"
                item["error"] = str(exc)
            with self._lock:
                completed += 1
                self.state["progress"] = {
                    "completed": completed,
                    "total": total,
                    "pct": round(completed / total * 100, 1) if total else 0.0,
                }

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {pool.submit(_run_item, item): item for item in items}
            for future in as_completed(futures):
                if self.cancel_event.is_set():
                    break
                future.result()

        with self._lock:
            self.state["status"] = (
                "stopped" if self.cancel_event.is_set() else "complete"
            )
        self._compute_summary()
        return dict(self.state)

    def _compute_summary(self) -> None:
        results: list[dict[str, Any]] = self.state.get("results") or []
        if not results:
            return
        passed = sum(1 for r in results if r.get("result") == "pass")
        failed = sum(1 for r in results if r.get("result") == "fail")

        speedups = [r.get("p50_speedup_x") for r in results if isinstance(r.get("p50_speedup_x"), (int, float))]
        avg_speedup = round(statistics.mean(speedups), 2) if speedups else 0.0

        by_size: dict[str, dict[str, Any]] = {}
        for r in results:
            label = r.get("size_label", "unknown")
            slot = by_size.setdefault(label, {"runs": 0, "speedups": []})
            slot["runs"] += 1
            if isinstance(r.get("p50_speedup_x"), (int, float)):
                slot["speedups"].append(r["p50_speedup_x"])

        for label, slot in by_size.items():
            slot["p50_speedup_x"] = (
                round(statistics.median(slot["speedups"]), 2) if slot["speedups"] else 0.0
            )
            del slot["speedups"]

        by_type: dict[str, dict[str, Any]] = {}
        for r in results:
            t = r.get("type", "unknown")
            slot = by_type.setdefault(t, {"runs": 0, "passed": 0, "failed": 0})
            slot["runs"] += 1
            slot["passed" if r.get("result") == "pass" else "failed"] += 1

        with self._lock:
            self.state["summary"] = {
                "total": len(results),
                "passed": passed,
                "failed": failed,
                "avg_speedup_x": avg_speedup,
                "by_size": by_size,
                "by_type": by_type,
                "pool_mode": self.pool_mode,
                "max_workers": self.max_workers,
                "repeats": self.repeats,
            }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Triage swarm load runner (in-process baseline vs sharded coordinator)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--profile",
        choices=("smoke", "standard", "full", "custom", "quick"),
        default="smoke",
        help="Test profile. 'quick' is the smallest single bucket for CI.",
    )
    parser.add_argument(
        "--types",
        nargs="+",
        default=list(SWARM_VALID_TYPES),
        help=f"Transaction types ({', '.join(SWARM_VALID_TYPES)})",
    )
    parser.add_argument(
        "--sizes",
        nargs="+",
        default=None,
        help="Sizes for custom profile (e.g. 512k 1mb 5mb)",
    )
    parser.add_argument("--repeats", type=int, default=3, help="Runs per (type, size) per pipeline")
    parser.add_argument("--concurrency", type=int, default=1, help="Concurrent (type, size) cells")
    parser.add_argument(
        "--pool",
        choices=("thread", "process", "serial"),
        default="thread",
        help="Coordinator engine pool mode",
    )
    parser.add_argument("--workers", type=int, default=4, help="Engine pool worker count")
    parser.add_argument("--output", default=None, help="Write results JSON to this path")
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.profile == "quick":
        sizes = ["512k"]
    elif args.profile == "custom" and args.sizes:
        sizes = [s for s in args.sizes if s in SIZE_BYTES]
    else:
        sizes = PROFILE_SIZES.get(args.profile, PROFILE_SIZES["smoke"])

    types = [t.lower() for t in args.types if t.lower() in SWARM_VALID_TYPES]

    logger.info(
        "starting swarm load test: profile=%s types=%s sizes=%s pool=%s workers=%d repeats=%d",
        args.profile, types, sizes, args.pool, args.workers, args.repeats,
    )

    runner = SwarmLoadRunner(
        repeats=args.repeats,
        concurrency=args.concurrency,
        pool_mode=args.pool,
        max_workers=args.workers,
    )
    state = runner.run_batch(types=types, sizes=sizes)

    if args.json:
        print(json.dumps(state, indent=2, default=str))
    else:
        _print_human(state)

    if args.output:
        Path(args.output).write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        logger.info("wrote results to %s", args.output)

    summary = state.get("summary") or {}
    failed = int(summary.get("failed") or 0)
    return 1 if failed > 0 else 0


def _print_human(state: dict[str, Any]) -> None:
    summary = state.get("summary") or {}
    print()
    print("=" * 72)
    print(f"Swarm Load Run — {summary.get('total', 0)} cells "
          f"(passed={summary.get('passed', 0)}, failed={summary.get('failed', 0)})")
    print(f"pool={summary.get('pool_mode')} workers={summary.get('max_workers')} "
          f"repeats={summary.get('repeats')}  avg_speedup={summary.get('avg_speedup_x')}x")
    print("=" * 72)
    for r in state.get("results", []):
        baseline = r.get("baseline") or {}
        swarm = r.get("swarm") or {}
        speedup = r.get("p50_speedup_x")
        print(
            f"  {r['type']:<5} {r['size_label']:<6} "
            f"baseline_p50={baseline.get('p50_ms', 0):>8.2f}ms  "
            f"swarm_p50={swarm.get('p50_ms', 0):>8.2f}ms  "
            f"speedup={speedup if speedup is not None else '-'}x  "
            f"{r.get('result', '-')}"
        )
    by_size = summary.get("by_size") or {}
    if by_size:
        print()
        print("Speedup by size bucket:")
        for label, info in sorted(by_size.items(), key=lambda kv: SIZE_BYTES.get(kv[0], 0)):
            print(f"  {label:<6}  p50_speedup={info.get('p50_speedup_x')}x  runs={info.get('runs')}")
    print()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
