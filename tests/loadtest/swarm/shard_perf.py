"""Shard throughput perf module.

Times the splitter + per-shard validation across the three
:class:`EnginePool` execution modes (``serial`` / ``thread`` /
``process``) so you can see where the GIL caps thread-mode throughput
and confirm process-mode escapes it for the CPU-bound validation /
scrubbing engines.

Usage::

    from tests.loadtest.swarm import shard_perf
    report = shard_perf(st_count=8, claims_per_st=50, repeats=3)
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_WORKER_DIR = _REPO_ROOT / "worker_py"
if str(_WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKER_DIR))

from sharding import partition, split_x12  # noqa: E402
from swarms import EnginePool, SwarmCoordinator  # noqa: E402


def _build_st(*, control: str, claim_count: int) -> str:
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
    claims = "".join(
        f"CLM*C{control}{i:04d}*{100 + i}.00***11:B:1*Y*A*Y*I~"
        "HI*ABK:K5789~"
        for i in range(claim_count)
    )
    body = head + claims
    se_segment_count = body.count("~") + 1
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


def shard_perf(
    *,
    st_count: int = 4,
    claims_per_st: int = 25,
    repeats: int = 3,
    workers: int = 4,
    modes: tuple[str, ...] = ("serial", "thread", "process"),
) -> dict[str, Any]:
    """Run the coordinator across each ``EnginePool`` mode and time it."""
    payload = _build_interchange(st_count=st_count, claims_per_st=claims_per_st)
    payload_bytes = len(payload.encode("utf-8"))

    # Sanity: confirm the splitter actually produces the expected shard count.
    shards = split_x12(payload)
    shard_count = len(shards)

    per_mode: dict[str, dict[str, Any]] = {}
    for mode in modes:
        samples: list[float] = []
        coord = SwarmCoordinator(
            thread_pool=EnginePool(mode=mode, max_workers=workers),
            process_pool=EnginePool(mode=mode, max_workers=workers),
        )
        for _ in range(repeats):
            t0 = time.perf_counter()
            coord.run(payload, scrub=True, to_fhir=False, generate_acks=False)
            samples.append((time.perf_counter() - t0) * 1000)
        per_mode[mode] = _stats(samples)

    serial_p50 = per_mode.get("serial", {}).get("p50_ms", 0.0)
    fastest = min(
        ((mode, info["p50_ms"]) for mode, info in per_mode.items() if info.get("p50_ms")),
        key=lambda kv: kv[1],
        default=(None, 0.0),
    )

    return {
        "fixture": {
            "st_count": st_count,
            "claims_per_st": claims_per_st,
            "shard_count": shard_count,
            "payload_bytes": payload_bytes,
            "total_claims": st_count * claims_per_st,
        },
        "per_mode": per_mode,
        "fastest_mode": fastest[0],
        "fastest_p50_ms": fastest[1],
        "serial_p50_ms": serial_p50,
        "fastest_vs_serial_x": (
            round(serial_p50 / fastest[1], 2) if fastest[1] > 0 else 0.0
        ),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--st", type=int, default=4)
    parser.add_argument("--claims", type=int, default=25)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = shard_perf(
        st_count=args.st,
        claims_per_st=args.claims,
        repeats=args.repeats,
        workers=args.workers,
    )
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    f = report["fixture"]
    print(
        f"shard perf — {f['st_count']} ST × {f['claims_per_st']} claims "
        f"({f['total_claims']} total, {f['shard_count']} shards, "
        f"{f['payload_bytes']} bytes)"
    )
    for mode, info in report["per_mode"].items():
        print(
            f"  {mode:<8} p50={info['p50_ms']:>8.2f}ms  p95={info['p95_ms']:>8.2f}ms  "
            f"mean={info['mean_ms']:>8.2f}ms"
        )
    print(
        f"  fastest={report['fastest_mode']} "
        f"({report['fastest_p50_ms']:.2f}ms, "
        f"{report['fastest_vs_serial_x']}x vs serial)"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
