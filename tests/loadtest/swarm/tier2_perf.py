"""Tier-2 LLM swarm parallelism perf module.

Quantifies the impact of replacing ``ThreadPoolExecutor(max_workers=1)``
with the real :class:`swarms.SwarmRunner` parallel fan-out, without
touching a live Ollama cluster.

Strategy
--------

We construct a :class:`MockLlmClient` whose ``generate`` callable sleeps
for a configurable latency (default 80ms — representative of a small
Ollama generation on a warm GPU). Then we:

1. Build the same four specialist agents that ``tier2_swarm`` runs.
2. Time a serialized baseline (sequential calls) and a
   :class:`SwarmRunner` parallel run with ``max_workers=4``.
3. Report wall-clock time + the implied speedup.

The serialized baseline approximates the pre-fix behaviour. The
parallel run approximates the post-fix behaviour. The point of this
module is to ship the measurement alongside the production
implementation so regressions are caught.

Usage::

    from tests.loadtest.swarm import tier2_perf
    report = tier2_perf(simulated_latency_ms=80, repeats=5)
    print(report["speedup_x"])
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

# worker_py needs to be importable; the swarm runner already adds it,
# but this module can be imported on its own so we add it here too.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_WORKER_DIR = _REPO_ROOT / "worker_py"
if str(_WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKER_DIR))

from swarms import MockLlmClient, SwarmAgent, SwarmRunner  # noqa: E402


def _supervisor_json() -> str:
    return json.dumps(
        {
            "verdict": "APPROVE",
            "confidence": 0.9,
            "summary": "perf harness verdict",
            "issues": [],
            "actions": [],
        }
    )


def _build_agents() -> list[SwarmAgent]:
    return [
        SwarmAgent(
            name=name,
            prompt=f"Specialist {name} prompt — examine the synthetic EDI excerpt below.",
            options={"temperature": 0.1, "num_predict": 400},
        )
        for name in (
            "structural_analysis",
            "anomaly_diagnosis",
            "compliance_check",
            "correction_proposal",
        )
    ]


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


def _stats(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {"runs": 0, "p50_ms": 0.0, "p95_ms": 0.0, "mean_ms": 0.0}
    return {
        "runs": len(samples),
        "p50_ms": round(statistics.median(samples), 2),
        "p95_ms": round(_percentile(samples, 95.0), 2),
        "mean_ms": round(statistics.mean(samples), 2),
        "min_ms": round(min(samples), 2),
        "max_ms": round(max(samples), 2),
    }


def tier2_perf(
    *,
    simulated_latency_ms: float = 80.0,
    repeats: int = 5,
    max_workers: int = 4,
) -> dict[str, Any]:
    """Compare serialized (max_workers=1) vs parallel SwarmRunner."""
    latency_s = simulated_latency_ms / 1000.0

    def respond(prompt: str) -> str:
        # Supervisor responds with valid JSON; specialists return free text.
        # In both cases we sleep to simulate the LLM call latency.
        time.sleep(latency_s)
        if "Reply with ONLY a JSON object" in prompt:
            return _supervisor_json()
        return "specialist analysis ok"

    client = MockLlmClient(respond)

    serial_times: list[float] = []
    parallel_times: list[float] = []

    serial_runner = SwarmRunner(client, max_workers=1, max_retries=0)
    parallel_runner = SwarmRunner(client, max_workers=max_workers, max_retries=0)

    for _ in range(repeats):
        agents = _build_agents()
        t0 = time.perf_counter()
        serial_runner.run(workload="tier2_perf_serial", agents=agents)
        serial_times.append((time.perf_counter() - t0) * 1000)

    for _ in range(repeats):
        agents = _build_agents()
        t0 = time.perf_counter()
        parallel_runner.run(workload="tier2_perf_parallel", agents=agents)
        parallel_times.append((time.perf_counter() - t0) * 1000)

    serial_stats = _stats(serial_times)
    parallel_stats = _stats(parallel_times)
    speedup = (
        round(serial_stats["p50_ms"] / parallel_stats["p50_ms"], 2)
        if parallel_stats["p50_ms"] > 0
        else 0.0
    )

    return {
        "simulated_latency_ms": simulated_latency_ms,
        "agents": 4,
        "supervisor_calls": 1,
        "max_workers": max_workers,
        "serial": serial_stats,
        "parallel": parallel_stats,
        "speedup_x": speedup,
        "expected_min_speedup_x": 2.0,
        "passed": speedup >= 2.0,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latency-ms", type=float, default=80.0)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = tier2_perf(
        simulated_latency_ms=args.latency_ms,
        repeats=args.repeats,
        max_workers=args.workers,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        s, p = report["serial"], report["parallel"]
        print(
            f"tier2 perf — latency={report['simulated_latency_ms']:.0f}ms x 5 LLM calls\n"
            f"  serial   p50={s['p50_ms']:>7.2f}ms  p95={s['p95_ms']:>7.2f}ms  mean={s['mean_ms']:>7.2f}ms\n"
            f"  parallel p50={p['p50_ms']:>7.2f}ms  p95={p['p95_ms']:>7.2f}ms  mean={p['mean_ms']:>7.2f}ms\n"
            f"  speedup_x={report['speedup_x']}  passed={report['passed']}"
        )
    return 0 if report["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
