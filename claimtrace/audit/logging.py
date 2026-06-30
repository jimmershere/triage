"""PHI-safe structured JSON logging + correlation-id propagation.

Design goals (HIPAA §164.312(b) audit controls):

* **Structured.** Every record is a single JSON object carrying a UTC
  timestamp, level, logger name, service name, event name, message, and a
  ``correlation_id`` so distributed events (api -> worker -> claimtrace,
  including RabbitMQ hops) can be stitched into one lineage.
* **PHI-safe.** Records must carry identifiers and hashes only. Free-text
  messages are passed through :func:`redact_text`, and structured fields
  through :func:`scrub_fields`, which mask common direct-identifier patterns
  (SSN, email, phone, PAN-like digit runs) before anything is serialized.
* **Tamper-evident substrate.** This logger is the human/operational view; the
  authoritative, append-only audit trail is the Claimtrace journal + Merkle
  proofs. The two share the same correlation id so a log line can always be
  matched to an immutable journal event.

The module depends only on the standard library so it can be imported from the
API service (which ships the ``claimtrace`` package) without dragging in heavy
dependencies. The worker keeps a self-contained mirror (``worker_py.audit_log``)
because its deployment image does not include ``claimtrace``; both emit the same
JSON schema.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import re
import uuid
from contextvars import ContextVar, Token
from typing import Any, Mapping, Optional

# HIPAA requires required audit documentation/logs be retained a minimum of six
# years. This is the single source of truth for the retention window; purge
# tooling and the Claimtrace journal retention policy must honor it.
AUDIT_RETENTION_YEARS = 6

CORRELATION_HEADER = "X-Correlation-ID"

# Per-context correlation id. Bound by the API middleware on each request and by
# the worker for each consumed message; falls back to ``None`` when unset.
_correlation_id: ContextVar[Optional[str]] = ContextVar(
    "triage_correlation_id", default=None
)

# Compiled direct-identifier patterns. These intentionally err toward masking:
# the logger should never be the place PHI leaks, even accidentally.
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
# 13-19 digit runs (credit-card / long member identifiers) — mask the value but
# keep a length hint so operators can still reason about it.
_PAN_RE = re.compile(r"\b\d{13,19}\b")

_REDACTED = "[REDACTED]"

# Maximum characters retained for any single free-text field. Long blobs are the
# most likely vector for clinical content slipping into a log line.
_MAX_TEXT = 512


def get_correlation_id() -> Optional[str]:
    """Return the correlation id bound to the current context, if any."""
    return _correlation_id.get()


def new_correlation_id() -> str:
    """Generate a fresh correlation id (hex uuid4)."""
    return uuid.uuid4().hex


def bind_correlation_id(value: Optional[str]) -> Token:
    """Bind ``value`` as the current correlation id; return a reset token."""
    return _correlation_id.set(value or new_correlation_id())


def reset_correlation_id(token: Token) -> None:
    """Restore the correlation id to its prior value using ``token``."""
    try:
        _correlation_id.reset(token)
    except (ValueError, LookupError):  # pragma: no cover - defensive
        pass


def hash_identifier(value: Any, *, length: int = 16) -> str:
    """Return a stable, non-reversible short hash for a correlatable token.

    Use this when an identifier must be logged for correlation but should not
    appear in cleartext (e.g. a raw subscriber id).
    """
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return digest[: max(8, min(length, len(digest)))]


def redact_text(text: Any) -> str:
    """Mask direct-identifier patterns and bound the length of free text."""
    if text is None:
        return ""
    s = text if isinstance(text, str) else str(text)
    s = _SSN_RE.sub(_REDACTED, s)
    s = _EMAIL_RE.sub(_REDACTED, s)
    s = _PHONE_RE.sub(_REDACTED, s)
    s = _PAN_RE.sub(lambda m: f"[REDACTED:{len(m.group(0))}d]", s)
    if len(s) > _MAX_TEXT:
        s = s[:_MAX_TEXT] + f"...(+{len(s) - _MAX_TEXT} chars)"
    return s


def _scrub_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return scrub_fields(value)
    if isinstance(value, (list, tuple)):
        return [_scrub_value(v) for v in value]
    return redact_text(str(value))


# Field names that denote a direct PHI identifier. Pattern-based value redaction
# cannot catch a plain patient name, an alphanumeric member id (e.g. MEMBER0001),
# or an 8-digit DOB, so when the *key* says the value is one of these we hash it
# (stable + non-reversible) regardless of its shape.
_SENSITIVE_KEY_RE = re.compile(
    r"ssn|social_security|date_of_birth|\bdob\b|birth_date|birthdate|"
    r"first_name|last_name|full_name|patient_name|member|subscriber|"
    r"beneficiary|patient|guarantor|mrn|medical_record|account_number|"
    r"street|address|postal|zip",
    re.IGNORECASE,
)


def _is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(key))


def scrub_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively scrub a mapping of structured log fields for PHI safety.

    Scalar values under a PHI-identifier key name are hashed; everything else
    flows through pattern-based redaction (recursing into nested maps/lists).
    """
    out: dict[str, Any] = {}
    for key, val in fields.items():
        skey = str(key)
        if (
            _is_sensitive_key(skey)
            and val is not None
            and not isinstance(val, (bool, Mapping, list, tuple))
        ):
            out[skey] = hash_identifier(val)
        else:
            out[skey] = _scrub_value(val)
    return out


class StructuredFormatter(logging.Formatter):
    """Render log records as compact, PHI-safe JSON objects."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        ts = _dt.datetime.fromtimestamp(
            record.created, tz=_dt.timezone.utc
        ).isoformat()
        correlation_id = getattr(record, "correlation_id", None) or get_correlation_id()
        event = getattr(record, "event", None) or record.name
        payload: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "service": self.service,
            "event": event,
            "msg": redact_text(record.getMessage()),
            "correlation_id": correlation_id,
        }
        audit = getattr(record, "audit", None)
        if isinstance(audit, Mapping) and audit:
            payload["fields"] = scrub_fields(audit)
        if record.exc_info:
            payload["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, sort_keys=True, default=str)


def _truthy(value: str) -> bool:
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def configure_structured_logging(
    service: str, *, level: Optional[str] = None, force: bool = False
) -> logging.Logger:
    """Install the structured JSON formatter on the root logger.

    Idempotent: repeated calls reconfigure the single Triage audit handler
    rather than stacking handlers. Honors ``TRIAGE_STRUCTURED_LOGS`` (default
    on); when disabled the caller's plain ``logging.basicConfig`` style is left
    untouched so local debugging stays readable.
    """
    root = logging.getLogger()
    level_name = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    root.setLevel(level_name)

    if not _truthy(os.getenv("TRIAGE_STRUCTURED_LOGS", "1")):
        return root

    handler: Optional[logging.Handler] = None
    for existing in root.handlers:
        if getattr(existing, "_triage_audit_handler", False):
            handler = existing
            break
    if handler is None or force:
        if handler is not None:
            root.removeHandler(handler)
        handler = logging.StreamHandler()
        handler._triage_audit_handler = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    handler.setFormatter(StructuredFormatter(service))
    handler.setLevel(level_name)
    return root


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    correlation_id: Optional[str] = None,
    **fields: Any,
) -> None:
    """Emit a structured audit event.

    ``event`` is a short, stable machine name (e.g. ``"http_request"``).
    ``fields`` are arbitrary structured attributes that are scrubbed for PHI
    before serialization. The active correlation id is attached automatically.
    """
    logger.log(
        level,
        event,
        extra={
            "event": event,
            "audit": dict(fields),
            "correlation_id": correlation_id or get_correlation_id(),
        },
    )
