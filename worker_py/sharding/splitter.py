"""X12 / bundle splitter.

The splitter consumes raw EDI text and emits a list of :class:`Shard`
payloads. Each X12 shard wraps exactly one ST..SE transaction in its own
ISA/GS/IEA/GE envelope so the downstream validation, scrubbing and FHIR
engines can process it independently — which is what lets the
:mod:`swarms.coordinator` fan shards out across a thread or process pool.

The splitter is deliberately small and dependency-light: it reuses
:mod:`validation.parser` for the heavy lifting of finding ISA/GS/ST/SE
boundaries (which already tolerates malformed input) and limits itself to
serializing those structures back out as self-contained envelopes.

If the input cannot be parsed as X12 at all the splitter returns a single
``kind=ShardKind.UNSHARDED`` shard containing the original text — the
coordinator then falls back to the unsharded code path so we never
silently drop data.
"""
from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence, TypeVar

from validation.parser import (
    Delimiters,
    FunctionalGroup,
    Interchange,
    Transaction,
    X12Document,
    parse,
)

T = TypeVar("T")


class ShardKind(str, enum.Enum):
    """What a :class:`Shard` actually contains."""

    ST_TRANSACTION = "st_transaction"
    """One ST..SE transaction, re-enveloped in a fresh ISA/GS/IEA/GE."""

    BUNDLE_FILE = "bundle_file"
    """One file from a multi-file bundle submission."""

    UNSHARDED = "unsharded"
    """The input could not be sharded (e.g. malformed); pass-through."""


@dataclass
class Shard:
    """One unit of work for the swarm coordinator.

    A shard's ``payload`` is always a complete, standalone payload — for
    X12 it is a syntactically valid interchange that the existing
    :func:`validation.validate_document` accepts without modification.

    ``parent_id`` and ``position`` together let the aggregator stitch shard
    results back into a single per-import outcome with stable ordering.
    """

    shard_id: str
    parent_id: str
    kind: ShardKind
    payload: str
    position: int  # 0-based shard index within the parent
    transaction_set: str | None = None
    control_number: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "shard_id": self.shard_id,
            "parent_id": self.parent_id,
            "kind": self.kind.value,
            "position": self.position,
            "transaction_set": self.transaction_set,
            "control_number": self.control_number,
            "byte_size": len(self.payload),
            "metadata": dict(self.metadata),
        }


def split_x12(
    text: str,
    *,
    parent_id: str = "x12",
) -> list[Shard]:
    """Split an X12 interchange into per-transaction shards.

    Each shard is a complete ISA/GS/ST..SE/GE/IEA payload containing
    exactly one transaction. Re-envelope control numbers (GE01, IEA01)
    are always ``1`` because the new envelope carries a single
    transaction; the original ISA13 / GS06 control numbers are
    preserved so downstream acknowledgments can correlate.

    If the text does not parse as X12 (no ISA, no transactions, or a
    fatal parse issue), a single ``ShardKind.UNSHARDED`` shard wrapping
    the original payload is returned so the coordinator can fall back
    to the legacy path.
    """
    if not text or not text.strip():
        return [
            Shard(
                shard_id=f"{parent_id}:unsharded:0",
                parent_id=parent_id,
                kind=ShardKind.UNSHARDED,
                payload=text,
                position=0,
            )
        ]

    doc = parse(text)
    if not doc.interchanges or not doc.transactions:
        return [
            Shard(
                shard_id=f"{parent_id}:unsharded:0",
                parent_id=parent_id,
                kind=ShardKind.UNSHARDED,
                payload=text,
                position=0,
                metadata={"reason": "no_transactions"},
            )
        ]

    shards: list[Shard] = []
    position = 0
    for interchange in doc.interchanges:
        if interchange.isa is None:
            # Orphan interchange with no ISA — skip rather than fabricate one.
            continue
        for group in interchange.groups:
            if group.gs is None:
                continue
            for txn in group.transactions:
                if txn.st is None:
                    continue
                payload = _serialize_single_txn(
                    interchange=interchange,
                    group=group,
                    transaction=txn,
                    delim=doc.delimiters,
                )
                shard_id = f"{parent_id}:st:{position:04d}"
                shards.append(
                    Shard(
                        shard_id=shard_id,
                        parent_id=parent_id,
                        kind=ShardKind.ST_TRANSACTION,
                        payload=payload,
                        position=position,
                        transaction_set=txn.set_code or None,
                        control_number=txn.control_number or None,
                        metadata={
                            "isa_control": interchange.control_number,
                            "gs_control": group.control_number,
                            "implementation_version": txn.implementation_version,
                            "segment_count": len(txn.segments),
                        },
                    )
                )
                position += 1

    if not shards:
        return [
            Shard(
                shard_id=f"{parent_id}:unsharded:0",
                parent_id=parent_id,
                kind=ShardKind.UNSHARDED,
                payload=text,
                position=0,
                metadata={"reason": "no_complete_transactions"},
            )
        ]

    return shards


def split_bundle(
    files: Sequence[tuple[str, str]],
    *,
    parent_id: str = "bundle",
) -> list[Shard]:
    """Turn a list of ``(name, payload)`` files into bundle shards.

    Each entry becomes one ``ShardKind.BUNDLE_FILE`` shard; the
    coordinator then re-feeds each one through :func:`split_x12` to
    take the next level of parallelism, if the payload is X12.
    """
    shards: list[Shard] = []
    for index, (name, payload) in enumerate(files):
        shards.append(
            Shard(
                shard_id=f"{parent_id}:file:{index:04d}",
                parent_id=parent_id,
                kind=ShardKind.BUNDLE_FILE,
                payload=payload,
                position=index,
                metadata={"filename": name},
            )
        )
    return shards


def partition(items: Sequence[T], batch_size: int) -> list[list[T]]:
    """Partition ``items`` into batches of at most ``batch_size`` elements.

    Used by the coordinator to fan claim-projection lists out across an
    :class:`~swarms.engine_pool.EnginePool` for parallel scrubbing /
    FHIR mapping when one transaction carries many claims.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if not items:
        return []
    n = len(items)
    batches = math.ceil(n / batch_size)
    return [list(items[i * batch_size : (i + 1) * batch_size]) for i in range(batches)]


# ---------------------------------------------------------------------------
# Internal — envelope serialization
# ---------------------------------------------------------------------------


def _serialize_single_txn(
    *,
    interchange: Interchange,
    group: FunctionalGroup,
    transaction: Transaction,
    delim: Delimiters,
) -> str:
    """Serialize one transaction in its own minimal ISA/GS/IEA/GE envelope.

    The original ISA and GS raw text is preserved verbatim — including
    sender/receiver IDs, control numbers, and the version code — so that
    downstream acknowledgments can correlate the shard back to its
    original interchange. Only the trailer counts (GE01, IEA01) are
    rewritten to ``1`` because the new envelope carries exactly one
    transaction.
    """
    assert interchange.isa is not None
    assert group.gs is not None
    assert transaction.st is not None

    seg_term = delim.segment or "~"
    elem_sep = delim.element or "*"

    pieces: list[str] = [interchange.isa.raw, group.gs.raw]
    # transaction.segments already includes the ST as the first segment and
    # the SE as the last (when present); preserve them verbatim.
    pieces.extend(seg.raw for seg in transaction.segments if seg is not None and seg.raw)
    pieces.append(f"GE{elem_sep}1{elem_sep}{group.control_number or '1'}")
    pieces.append(f"IEA{elem_sep}1{elem_sep}{interchange.control_number or '1'}")
    # Each segment is followed by the segment terminator, including the last one.
    return seg_term.join(pieces) + seg_term


def shard_summary(shards: Iterable[Shard]) -> dict[str, Any]:
    """Small helper used by the coordinator for audit logging."""
    counts: dict[str, int] = {}
    total_bytes = 0
    for shard in shards:
        counts[shard.kind.value] = counts.get(shard.kind.value, 0) + 1
        total_bytes += len(shard.payload)
    return {"counts": counts, "total_bytes": total_bytes}
