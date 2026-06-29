"""Empirically-extracted X12 TR3 reference (Workstream 2 keystone).

Loads the authoritative WPC/DISA Table Data (005010X222 Health Care Claim:
Professional) into a position-addressable structural model that drives
table-driven SNIP validation (Type 1 element attributes, Type 2 required usage,
Type 5 enumerated value bindings) instead of a handful of hand-coded rules.

The reference is *advisory / validation-only*: it describes what the guide
permits; it never rewrites a claim.
"""
from __future__ import annotations

from .x12_reference import (
    ElementSpec,
    TransactionReference,
    clear_cache,
    extract_internal_codesets,
    get_reference,
    load_reference_csv,
)

__all__ = [
    "ElementSpec",
    "TransactionReference",
    "clear_cache",
    "extract_internal_codesets",
    "get_reference",
    "load_reference_csv",
]
