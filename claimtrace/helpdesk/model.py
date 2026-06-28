"""Helpdesk case status machine.

Status flow (a strict, auditable progression):

    open -> submitter-notified -> awaiting-resubmission -> resolved

with two pragmatic shortcuts an operator needs in practice:

* any non-terminal status may be ``resolved`` directly (e.g. a false alarm or a
  resubmission that arrives before the submitter is formally notified), and
* ``awaiting-resubmission`` may step back to ``submitter-notified`` when a
  resubmission fails validation and the submitter must be re-notified.

A case is *closed* (resolved) only when a corrected resubmission passes; that
transition is driven by a RabbitMQ resubmission event, not a manual edit.
"""
from __future__ import annotations

STATUS_OPEN = "open"
STATUS_NOTIFIED = "submitter-notified"
STATUS_AWAITING = "awaiting-resubmission"
STATUS_RESOLVED = "resolved"

CASE_STATUSES = (STATUS_OPEN, STATUS_NOTIFIED, STATUS_AWAITING, STATUS_RESOLVED)

# Allowed forward (and the few backward) transitions.
_TRANSITIONS: dict[str, set[str]] = {
    STATUS_OPEN: {STATUS_NOTIFIED, STATUS_RESOLVED},
    STATUS_NOTIFIED: {STATUS_AWAITING, STATUS_RESOLVED},
    STATUS_AWAITING: {STATUS_NOTIFIED, STATUS_RESOLVED},
    STATUS_RESOLVED: set(),
}


class CaseStatusError(ValueError):
    """Raised when an illegal case status transition is attempted."""


def next_statuses(current: str) -> set[str]:
    """Return the set of statuses reachable from ``current``."""
    if current not in _TRANSITIONS:
        raise CaseStatusError(f"unknown case status '{current}'")
    return set(_TRANSITIONS[current])


def validate_transition(current: str, target: str) -> None:
    """Validate a status transition, raising :class:`CaseStatusError` if illegal."""
    if current not in _TRANSITIONS:
        raise CaseStatusError(f"unknown case status '{current}'")
    if target not in CASE_STATUSES:
        raise CaseStatusError(f"unknown target status '{target}'")
    if target == current:
        raise CaseStatusError(f"case is already '{current}'")
    if target not in _TRANSITIONS[current]:
        raise CaseStatusError(
            f"illegal transition '{current}' -> '{target}'; "
            f"allowed: {sorted(_TRANSITIONS[current]) or ['(none — case is closed)']}"
        )
