"""Journal-to-lineage graph projection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from claimtrace.journal.store import ClaimEvent


class GraphSink(Protocol):
    def merge_node(self, label: str, key: str, value: str, **properties: Any) -> None: ...

    def merge_edge(
        self,
        from_label: str,
        from_key: str,
        from_value: str,
        rel: str,
        to_label: str,
        to_key: str,
        to_value: str,
        **properties: Any,
    ) -> None: ...


@dataclass
class InMemoryGraphSink:
    nodes: set[tuple[str, str, str]] = field(default_factory=set)
    edges: set[tuple[str, str, str, str, str, str, str]] = field(default_factory=set)

    def merge_node(self, label: str, key: str, value: str, **properties: Any) -> None:
        self.nodes.add((label, key, value))

    def merge_edge(
        self,
        from_label: str,
        from_key: str,
        from_value: str,
        rel: str,
        to_label: str,
        to_key: str,
        to_value: str,
        **properties: Any,
    ) -> None:
        self.merge_node(from_label, from_key, from_value)
        self.merge_node(to_label, to_key, to_value)
        self.edges.add((from_label, from_key, from_value, rel, to_label, to_key, to_value))


class LineageProjector:
    def __init__(self, sink: GraphSink) -> None:
        self.sink = sink
        self.processed_event_ids: set[str] = set()

    def project(self, events: Iterable[ClaimEvent]) -> None:
        for event in sorted(events, key=lambda item: (item.ts, str(item.event_id))):
            event_key = str(event.event_id)
            if event_key in self.processed_event_ids:
                continue
            self._project_event(event)
            self.processed_event_ids.add(event_key)

    def _project_event(self, event: ClaimEvent) -> None:
        self.sink.merge_node("Claim", "claim_id", event.claim_id)
        if event.operation_type == "SPLIT":
            parent = event.correlation_ids.get("parent")
            if parent:
                self.sink.merge_edge(
                    "Claim",
                    "claim_id",
                    event.claim_id,
                    "CLAIM_SPLIT_FROM",
                    "Claim",
                    "claim_id",
                    str(parent),
                )
        if event.operation_type == "BUNDLE" and event.bundle_id:
            self.sink.merge_node("Bundle", "bundle_id", event.bundle_id)
            members = event.correlation_ids.get("children") or [event.claim_id]
            for member in members:
                self.sink.merge_edge(
                    "Claim",
                    "claim_id",
                    str(member),
                    "CLAIM_PART_OF_BUNDLE",
                    "Bundle",
                    "bundle_id",
                    event.bundle_id,
                )
                self.sink.merge_edge(
                    "Claim",
                    "claim_id",
                    str(member),
                    "CLAIM_AGGREGATED_INTO",
                    "Bundle",
                    "bundle_id",
                    event.bundle_id,
                )
        if event.operation_type == "MERKLE_ROOT":
            root = event.correlation_ids.get("batch_root_hash") or event.new_state_hash
            self.sink.merge_node("Batch", "batch_root_hash", str(root))
            for member in event.correlation_ids.get("members", []) or []:
                self.sink.merge_edge(
                    "Claim",
                    "claim_id",
                    str(member),
                    "CLAIM_AGGREGATED_INTO",
                    "Batch",
                    "batch_root_hash",
                    str(root),
                )
        if event.operation_type == "ADJUDICATE":
            payment_id = event.correlation_ids.get("payment_id")
            if payment_id:
                self.sink.merge_node("Payment", "payment_id", str(payment_id))
                members = event.correlation_ids.get("children") or [event.claim_id]
                for member in members:
                    self.sink.merge_edge(
                        "Claim",
                        "claim_id",
                        str(member),
                        "RESULTED_IN_PAYMENT",
                        "Payment",
                        "payment_id",
                        str(payment_id),
                    )
                trn = event.correlation_ids.get("trn")
                if trn:
                    self.sink.merge_node("X835", "trn", str(trn))
                    self.sink.merge_edge(
                        "X835",
                        "trn",
                        str(trn),
                        "RESULTED_IN_PAYMENT",
                        "Payment",
                        "payment_id",
                        str(payment_id),
                    )
