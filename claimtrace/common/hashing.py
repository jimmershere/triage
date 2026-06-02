"""Shared SHA-256 hashing primitives."""

from __future__ import annotations

import hashlib


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def hash_payload(payload_bytes: bytes) -> str:
    return sha256_hex(payload_bytes)
