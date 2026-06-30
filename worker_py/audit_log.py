"""PHI-safe structured logging + correlation propagation for the worker.

This is a self-contained mirror of ``claimtrace.audit.logging``. The worker's
deployment image is intentionally curated and does **not** ship the
``claimtrace`` package, so the worker carries its own copy of the audit-logging
contract. Both emit the *same* JSON schema and use the same correlation header
so api -> worker -> claimtrace events stitch into one lineage across the
RabbitMQ hop.

Keep this module dependency-free (standard library only) and keep its JSON
output schema in sync with ``claimtrace/audit/logging.py``.
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

AUDIT_RETENTION_YEARS = 6
CORRELATION_HEADER = "X-Correlation-ID"

_correlation_id: ContextVar[Optional[str]] = ContextVar(
    "triage_correlation_id", default=None
)

_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_PAN_RE = re.compile(r"\b\d{13,19}\b")
_REDACTED = "[REDACTED]"
_MAX_TEXT = 512


def get_correlation_id() -> Optional[str]:
    return _correlation_id.get()


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def bind_correlation_id(value: Optional[str]) -> Token:
    return _correlation_id.set(value or new_correlation_id())


def reset_correlation_id(token: Token) -> None:
    try:
        _correlation_id.reset(token)
    except (ValueError, LookupError):  # pragma: no cover - defensive
        pass


def hash_identifier(value: Any, *, length: int = 16) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return digest[: max(8, min(length, len(digest)))]


def redact_text(text: Any) -> str:
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
# cannot catch a plain patient name, an alphanumeric member id, or an 8-digit
# DOB, so when the *key* says the value is one of these we hash it (stable +
# non-reversible) regardless of its shape. Kept in sync with
# claimtrace/audit/logging.py.
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
    logger.log(
        level,
        event,
        extra={
            "event": event,
            "audit": dict(fields),
            "correlation_id": correlation_id or get_correlation_id(),
        },
    )
