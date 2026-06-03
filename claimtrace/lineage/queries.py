"""Lineage query helpers for in-memory and Neo4j-backed graphs."""

from __future__ import annotations

from .projector import InMemoryGraphSink


def claims_for_payment(payment_id: str, sink: InMemoryGraphSink) -> set[str]:
    return {
        from_value
        for from_label, from_key, from_value, rel, to_label, to_key, to_value in sink.edges
        if from_label == "Claim"
        and from_key == "claim_id"
        and rel == "RESULTED_IN_PAYMENT"
        and to_label == "Payment"
        and to_key == "payment_id"
        and to_value == payment_id
    }


def lineage_835_to_837(trn: str, sink: InMemoryGraphSink) -> set[str]:
    payment_ids = {
        to_value
        for from_label, from_key, from_value, rel, to_label, to_key, to_value in sink.edges
        if from_label == "X835"
        and from_key == "trn"
        and from_value == trn
        and rel == "RESULTED_IN_PAYMENT"
        and to_label == "Payment"
    }
    claims: set[str] = set()
    for payment_id in payment_ids:
        claims.update(claims_for_payment(payment_id, sink))
    return claims


def intersection(claim_id_a: str, claim_id_b: str, sink: InMemoryGraphSink) -> set[tuple[str, str]]:
    targets_a = {
        (to_label, to_value)
        for from_label, from_key, from_value, rel, to_label, to_key, to_value in sink.edges
        if from_label == "Claim" and from_value == claim_id_a
    }
    targets_b = {
        (to_label, to_value)
        for from_label, from_key, from_value, rel, to_label, to_key, to_value in sink.edges
        if from_label == "Claim" and from_value == claim_id_b
    }
    return targets_a.intersection(targets_b)
