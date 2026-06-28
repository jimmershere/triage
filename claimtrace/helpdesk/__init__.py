"""Operational Helpdesk domain (Workstream 4).

A clearinghouse operator works rejected claims off 999/277CA/rejection-report
events: each *case* references a specific Claimtrace claim and the offending
event, surfaces the rejection reason and segment/element location, and is tracked
through a fixed status flow to a corrected resubmission.

Cases **reference, never edit** claim data. To avoid PHI sprawl, cases store
reference identifiers and hashes — not clinical content. This package holds the
pure (dependency-light) status machine and the human-readable rejection-report
packager; the database-backed CRUD lives in ``api/helpdesk_service.py`` and the
HTTP/RabbitMQ surfaces in ``api`` and ``worker_py``.
"""
from __future__ import annotations

from .model import (
    CASE_STATUSES,
    STATUS_AWAITING,
    STATUS_NOTIFIED,
    STATUS_OPEN,
    STATUS_RESOLVED,
    CaseStatusError,
    next_statuses,
    validate_transition,
)
from .rejection_report import RejectionContext, RejectionItem, build_rejection_report

__all__ = [
    "CASE_STATUSES",
    "STATUS_AWAITING",
    "STATUS_NOTIFIED",
    "STATUS_OPEN",
    "STATUS_RESOLVED",
    "CaseStatusError",
    "RejectionContext",
    "RejectionItem",
    "build_rejection_report",
    "next_statuses",
    "validate_transition",
]
