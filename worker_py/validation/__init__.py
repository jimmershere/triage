"""TurboHEDI CMS-grade X12 validation engine.

This package implements WEDI SNIP type 1-7 validation for HIPAA X12 healthcare
transactions, conformant acknowledgment generation (TA1, 999, 277CA), and
external code-set checking.

Public surface:
- :func:`validation.engine.validate_document` — validate raw X12 text.
- :class:`validation.model.ValidationReport` — the validation result.
- :mod:`validation.acks` — TA1 / 999 / 277CA generators.
"""
from __future__ import annotations

from .model import (
    ClaimProjection,
    Severity,
    ServiceLineProjection,
    SnipType,
    ValidationIssue,
    ValidationReport,
)
from .engine import validate_document, validate_file

__all__ = [
    "ClaimProjection",
    "Severity",
    "ServiceLineProjection",
    "SnipType",
    "ValidationIssue",
    "ValidationReport",
    "validate_document",
    "validate_file",
]
