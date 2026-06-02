"""Merkle tree integrity helpers for claim batches and bundles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from claimtrace.common.hashing import hash_payload, sha256_hex


Direction = Literal["L", "R"]


@dataclass(frozen=True)
class Tree:
    levels: list[list[str]]


def compute_hash(payload_bytes: bytes) -> str:
    return hash_payload(payload_bytes)


def _parent(left: str, right: str) -> str:
    return sha256_hex(f"{left}:{right}".encode("utf-8"))


def build_merkle_tree(list_of_hashes: Iterable[str]) -> Tree:
    leaves = [str(value) for value in list_of_hashes]
    if not leaves:
        raise ValueError("at least one leaf hash is required")
    levels = [leaves]
    current = leaves
    while len(current) > 1:
        if len(current) % 2:
            current = current + [current[-1]]
        parents = [_parent(current[index], current[index + 1]) for index in range(0, len(current), 2)]
        levels.append(parents)
        current = parents
    return Tree(levels=levels)


def merkle_root(tree: Tree) -> str:
    return tree.levels[-1][0]


def get_proof_path(tree: Tree, index: int) -> list[tuple[str, Direction]]:
    if index < 0 or index >= len(tree.levels[0]):
        raise IndexError("leaf index out of range")
    proof: list[tuple[str, Direction]] = []
    cursor = index
    for level in tree.levels[:-1]:
        width = len(level)
        effective = level + ([level[-1]] if width % 2 else [])
        sibling_index = cursor + 1 if cursor % 2 == 0 else cursor - 1
        direction: Direction = "R" if cursor % 2 == 0 else "L"
        proof.append((effective[sibling_index], direction))
        cursor //= 2
    return proof


def verify_proof(leaf_hash: str, proof: list[tuple[str, Direction]], root: str) -> bool:
    cursor = leaf_hash
    for sibling_hash, direction in proof:
        if direction == "R":
            cursor = _parent(cursor, sibling_hash)
        elif direction == "L":
            cursor = _parent(sibling_hash, cursor)
        else:
            return False
    return cursor == root


def build_batch_mapping(claim_hashes: dict[str, str]) -> dict[str, object]:
    ordered_claims = sorted(claim_hashes)
    tree = build_merkle_tree([claim_hashes[claim_id] for claim_id in ordered_claims])
    return {
        "batch_root_hash": merkle_root(tree),
        "claims": {
            claim_id: {"leaf_index": index, "state_hash": claim_hashes[claim_id]}
            for index, claim_id in enumerate(ordered_claims)
        },
    }
