"""Thread- or process-pool wrapper for shard-level engine work.

The :class:`SwarmRunner` is intentionally thread-based because it exists to
overlap *I/O-bound* Ollama HTTP calls. The pure-Python engines under
``validation/``, ``scrubbing/`` and ``fhir/`` are *CPU-bound* (parsing,
regex, NCCI table lookups, FHIR resource construction) and the GIL caps
their throughput on a thread pool.

:class:`EnginePool` is a deliberately small façade that lets the
:mod:`swarms.coordinator` pick the right executor type per fan-out:

- ``mode="thread"`` — for I/O-bound work or for tests where forking a
  process is undesirable. Backed by :class:`concurrent.futures.ThreadPoolExecutor`.
- ``mode="process"`` — for CPU-bound shard work. Backed by
  :class:`concurrent.futures.ProcessPoolExecutor`. Defaults to one worker
  per CPU.
- ``mode="serial"`` — runs work in-process, in order. Useful as a baseline
  in the benchmark harness and as the deterministic mode for unit tests.

The pool's :meth:`map` preserves input order in its result list.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import (
    ProcessPoolExecutor,
    ThreadPoolExecutor,
)
from dataclasses import dataclass
from typing import Any, Callable, Sequence, TypeVar

T = TypeVar("T")
R = TypeVar("R")

logger = logging.getLogger("swarms.engine_pool")

_VALID_MODES = ("thread", "process", "serial")


@dataclass
class PoolStats:
    """Lightweight per-run statistics returned by :meth:`EnginePool.map_with_stats`."""

    item_count: int
    worker_count: int
    mode: str
    duration_ms: float


class EnginePool:
    """Configurable parallel-map helper used by the swarm coordinator.

    Construct one ``EnginePool`` per kind of fan-out the coordinator
    needs (e.g. one for scrubbing, one for FHIR mapping, one for LLM
    calls if you prefer to bypass :class:`SwarmRunner`).
    """

    def __init__(
        self,
        *,
        max_workers: int | None = None,
        mode: str = "thread",
    ) -> None:
        if mode not in _VALID_MODES:
            raise ValueError(
                f"EnginePool mode must be one of {_VALID_MODES!r}, got {mode!r}"
            )
        self.mode = mode
        if max_workers is None:
            max_workers = os.cpu_count() or 4
        self.max_workers = max(1, int(max_workers))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def map(self, fn: Callable[[T], R], items: Sequence[T]) -> list[R]:
        """Apply ``fn`` to each item; preserve input order in the result list."""
        results, _stats = self.map_with_stats(fn, items)
        return results

    def map_with_stats(
        self, fn: Callable[[T], R], items: Sequence[T]
    ) -> tuple[list[R], PoolStats]:
        """Like :meth:`map`, but also return :class:`PoolStats` for audit/bench."""
        import time

        start = time.perf_counter()
        n = len(items)
        if n == 0:
            return [], PoolStats(0, 0, self.mode, 0.0)

        worker_count = min(self.max_workers, n)

        if self.mode == "serial" or worker_count == 1:
            results: list[R] = [fn(item) for item in items]
        elif self.mode == "thread":
            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                # Submit and collect in input order to preserve ordering.
                futures = [pool.submit(fn, item) for item in items]
                results = [f.result() for f in futures]
        else:  # process
            try:
                with ProcessPoolExecutor(max_workers=worker_count) as pool:
                    futures = [pool.submit(fn, item) for item in items]
                    results = [f.result() for f in futures]
            except (OSError, RuntimeError) as exc:
                # Process pools can fail in environments where forking is
                # restricted (some sandboxes, certain CI runners). Fall back
                # to a thread pool so the coordinator never crashes — we log
                # the fall-back so the operator can tune.
                logger.warning(
                    "ProcessPoolExecutor unavailable (%s); falling back to threads",
                    exc,
                )
                with ThreadPoolExecutor(max_workers=worker_count) as pool:
                    futures = [pool.submit(fn, item) for item in items]
                    results = [f.result() for f in futures]

        duration_ms = (time.perf_counter() - start) * 1000
        return results, PoolStats(
            item_count=n,
            worker_count=worker_count,
            mode=self.mode,
            duration_ms=duration_ms,
        )
