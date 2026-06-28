"""PHI-safe structured audit logging and correlation propagation.

This package provides the shared, tamper-aware audit-logging substrate that
Workstream 3 standardizes across the FastAPI ``api`` service and (mirrored
self-contained) the Python worker. It layers on top of the existing
``claimtrace`` correlation/journal/Merkle machinery so that every API call,
validation, acknowledgement, and rejection report is traceable end-to-end via
a single correlation id that equals (or links to) the Claimtrace lineage id.

HIPAA §164.312(b) audit controls: log *identifiers and hashes only* — never
clinical content or direct PHI in messages, query strings, or error text.
"""

from .logging import (
    AUDIT_RETENTION_YEARS,
    CORRELATION_HEADER,
    StructuredFormatter,
    bind_correlation_id,
    configure_structured_logging,
    get_correlation_id,
    hash_identifier,
    log_event,
    new_correlation_id,
    redact_text,
    reset_correlation_id,
    scrub_fields,
)

__all__ = [
    "AUDIT_RETENTION_YEARS",
    "CORRELATION_HEADER",
    "StructuredFormatter",
    "bind_correlation_id",
    "configure_structured_logging",
    "get_correlation_id",
    "hash_identifier",
    "log_event",
    "new_correlation_id",
    "redact_text",
    "reset_correlation_id",
    "scrub_fields",
]
