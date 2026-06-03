"""EDI sharding utilities for the Triage swarm coordinator.

The sharding package turns one logical EDI work unit (a multi-transaction
interchange, or a bundle of files) into a list of self-contained
:class:`Shard` payloads that can be processed concurrently by the
:mod:`swarms.coordinator` and reassembled by :mod:`swarms.aggregator`.

Public surface:
- :class:`Shard` — one unit of swarm work.
- :func:`split_x12` — split an X12 interchange at ST/SE boundaries; each shard
  is wrapped in its own minimal ISA/GS/IEA/GE envelope so the existing
  validation, scrubbing and FHIR engines can run on it standalone.
- :func:`split_bundle` — turn a list of file payloads into bundle shards;
  each is then passed through :func:`split_x12` (or returned as-is for
  non-X12 contents).
- :func:`partition` — utility to split any sequence into evenly-sized batches
  for engine-pool fan-out (e.g. partition claims for parallel scrubbing).
"""
from __future__ import annotations

from .splitter import (
    Shard,
    ShardKind,
    partition,
    split_bundle,
    split_x12,
)

__all__ = [
    "Shard",
    "ShardKind",
    "partition",
    "split_bundle",
    "split_x12",
]
