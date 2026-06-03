"""Swarm-aware load and performance testing modules.

These modules mirror the contract of :mod:`tests.loadtest.runner` (size
buckets, profile names, ``LoadTestRunner``-shaped state dict) so the
existing CLI / API / UI harness can drive the new sharded swarm
pipeline with the same generators and reporting.

Three modules ship here:

- :mod:`tests.loadtest.swarm.runner` — :class:`SwarmLoadRunner` runs
  generated EDI payloads through ``turbo_pipeline.run_pipeline``
  (baseline) and ``swarms.SwarmCoordinator.run`` (sharded) in-process
  and records p50/p95 timings + a speedup factor per size bucket.
- :mod:`tests.loadtest.swarm.tier2_perf` — measures the tier-2 LLM
  swarm parallelism via a simulated-latency :class:`MockLlmClient` so
  the fix for ``ThreadPoolExecutor(max_workers=1)`` is observable
  without touching a real Ollama cluster.
- :mod:`tests.loadtest.swarm.shard_perf` — measures shard throughput
  (split + per-shard validation/scrubbing) across the available
  :class:`EnginePool` modes (serial / thread / process).
"""
from __future__ import annotations

from .runner import (
    PROFILE_SIZES,
    SIZE_BYTES,
    SWARM_VALID_TYPES,
    SwarmLoadRunner,
    summarize_speedup,
)
from .shard_perf import shard_perf
from .tier2_perf import tier2_perf

__all__ = [
    "PROFILE_SIZES",
    "SIZE_BYTES",
    "SWARM_VALID_TYPES",
    "SwarmLoadRunner",
    "summarize_speedup",
    "shard_perf",
    "tier2_perf",
]
