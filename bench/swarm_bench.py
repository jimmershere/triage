#!/usr/bin/env python3
"""Benchmark — coordinator (sharded swarm) vs turbo_pipeline (single-thread).

Runs the same synthetic 837 workload through both pipelines, repeated N
times, and prints p50/p95 timings side-by-side. The synthetic 837 is
built locally so the bench has no external dependencies and no sample
data requirement.

Usage::

    python3 bench/swarm_bench.py                       # default: 1 ST x 50 CLM, 30 runs
    python3 bench/swarm_bench.py --st 4 --claims 50    # 4 transactions x 50 claims each
    python3 bench/swarm_bench.py --runs 5 --smoke      # quick smoke (5 runs)

Outputs JSON when ``--json`` is set so CI can persist artifacts.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

# Ensure the bench can find worker_py when run from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "worker_py"))

from swarms import EnginePool, SwarmCoordinator  # noqa: E402
from turbo_pipeline import run_pipeline  # noqa: E402


def _build_st(*, control: str, claim_count: int) -> str:
    """Generate a single ST..SE 837P transaction with ``claim_count`` claims."""
    head = (
        f"ST*837*{control}*005010X222A1~"
        f"BHT*0019*00*{control}*20230101*1319*CH~"
        "NM1*41*2*SENDER*****46*661234567~"
        "PER*IC*CONTACT*TE*5551234567~"
        "NM1*40*2*RECEIVER*****46*RECEIVERID~"
        "HL*1**20*1~"
        "NM1*85*2*BILLING PROVIDER*****XX*1234567893~"
        "N3*1 PROVIDER WAY~"
        "N4*TOWN*ST*12345~"
        "REF*EI*123456789~"
        "HL*2*1*22*0~"
        "SBR*P*18*12345*******MC~"
        "NM1*IL*1*DOE*JANE****MI*W000000001~"
        "N3*123 MAIN STREET~"
        "N4*ANYTOWN*ST*90210~"
        "DMG*D8*19700101*F~"
    )
    claims = []
    for i in range(claim_count):
        claims.append(
            f"CLM*C{control}{i:04d}*{100 + i}.00***11:B:1*Y*A*Y*I~"
            "HI*ABK:K5789~"
        )
    body = head + "".join(claims)
    se_segment_count = body.count("~") + 1  # +1 for the SE itself
    return body + f"SE*{se_segment_count}*{control}~"


def _build_interchange(*, st_count: int, claims_per_st: int) -> str:
    isa = (
        "ISA*00*          *00*          *ZZ*SENDERID      *ZZ*RECEIVERID    "
        "*230101*1253*^*00501*000000999*0*T*:~"
    )
    gs = "GS*HC*SENDER*RECEIVER*20230101*1253*1*X*005010X222A1~"
    sts = "".join(
        _build_st(control=f"{i + 1:04d}", claim_count=claims_per_st)
        for i in range(st_count)
    )
    ge = f"GE*{st_count}*1~"
    iea = "IEA*1*000000999~"
    return isa + gs + sts + ge + iea


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


def _stats(label: str, samples: list[float]) -> dict:
    return {
        "label": label,
        "runs": len(samples),
        "p50_ms": round(statistics.median(samples), 2) if samples else 0.0,
        "p95_ms": round(_percentile(samples, 95.0), 2),
        "min_ms": round(min(samples), 2) if samples else 0.0,
        "max_ms": round(max(samples), 2) if samples else 0.0,
        "mean_ms": round(statistics.mean(samples), 2) if samples else 0.0,
    }


def bench(
    *,
    st_count: int,
    claims_per_st: int,
    runs: int,
    workers: int,
    pool_mode: str = "thread",
) -> dict:
    payload = _build_interchange(st_count=st_count, claims_per_st=claims_per_st)
    payload_bytes = len(payload.encode("utf-8"))

    baseline_times: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        run_pipeline(payload, scrub=True, to_fhir=False, generate_acks=False)
        baseline_times.append((time.perf_counter() - start) * 1000)

    coord = SwarmCoordinator(
        thread_pool=EnginePool(mode=pool_mode, max_workers=workers),
        process_pool=EnginePool(mode=pool_mode, max_workers=workers),
    )
    swarm_times: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        coord.run(payload, scrub=True, to_fhir=False, generate_acks=False)
        swarm_times.append((time.perf_counter() - start) * 1000)

    baseline = _stats("turbo_pipeline (single-thread)", baseline_times)
    swarm = _stats(f"SwarmCoordinator ({pool_mode} pool)", swarm_times)
    speedup = (baseline["p50_ms"] / swarm["p50_ms"]) if swarm["p50_ms"] > 0 else 0.0
    return {
        "fixture": {
            "st_count": st_count,
            "claims_per_st": claims_per_st,
            "total_claims": st_count * claims_per_st,
            "payload_bytes": payload_bytes,
        },
        "baseline": baseline,
        "swarm": swarm,
        "p50_speedup_x": round(speedup, 2),
        "workers": workers,
        "runs": runs,
        "pool_mode": pool_mode,
    }


def _print_human(report: dict) -> None:
    f = report["fixture"]
    print(
        f"== swarm_bench: {f['st_count']} ST × {f['claims_per_st']} claims "
        f"({f['total_claims']} total, {f['payload_bytes']} bytes, "
        f"{report['runs']} runs, {report['workers']} workers) =="
    )
    fmt = "  {label:<32} p50={p50:>7.2f}ms  p95={p95:>7.2f}ms  min={min:>7.2f}ms  max={max:>7.2f}ms"
    print(
        fmt.format(
            label=report["baseline"]["label"],
            p50=report["baseline"]["p50_ms"],
            p95=report["baseline"]["p95_ms"],
            min=report["baseline"]["min_ms"],
            max=report["baseline"]["max_ms"],
        )
    )
    print(
        fmt.format(
            label=report["swarm"]["label"],
            p50=report["swarm"]["p50_ms"],
            p95=report["swarm"]["p95_ms"],
            min=report["swarm"]["min_ms"],
            max=report["swarm"]["max_ms"],
        )
    )
    print(f"  p50 swarm speedup: {report['p50_speedup_x']:.2f}x")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--st", type=int, default=4, help="ST..SE transactions in the interchange")
    parser.add_argument("--claims", type=int, default=50, help="Claims per ST transaction")
    parser.add_argument("--runs", type=int, default=20, help="Iterations per pipeline")
    parser.add_argument("--workers", type=int, default=4, help="Coordinator thread pool size")
    parser.add_argument(
        "--pool",
        choices=("thread", "process", "serial"),
        default="thread",
        help="Coordinator engine pool mode (process beats GIL for CPU-bound work)",
    )
    parser.add_argument("--smoke", action="store_true", help="Shortcut for --runs 5")
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    args = parser.parse_args(argv)

    if args.smoke:
        args.runs = max(2, min(args.runs, 5))

    report = bench(
        st_count=args.st,
        claims_per_st=args.claims,
        runs=args.runs,
        workers=args.workers,
        pool_mode=args.pool,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
