"""Tests for the PHI-safe structured audit logging substrate (Workstream 3)."""

import json
import logging
import unittest

from claimtrace.audit import (
    AUDIT_RETENTION_YEARS,
    CORRELATION_HEADER,
    StructuredFormatter,
    bind_correlation_id,
    get_correlation_id,
    hash_identifier,
    log_event,
    new_correlation_id,
    redact_text,
    reset_correlation_id,
    scrub_fields,
)


class RedactionTests(unittest.TestCase):
    def test_masks_direct_identifiers(self) -> None:
        text = "patient ssn 123-45-6789 email a.b@example.com phone 555-867-5309"
        out = redact_text(text)
        self.assertNotIn("123-45-6789", out)
        self.assertNotIn("a.b@example.com", out)
        self.assertNotIn("555-867-5309", out)
        self.assertIn("[REDACTED]", out)

    def test_masks_long_digit_runs_but_keeps_length_hint(self) -> None:
        out = redact_text("member 4111111111111111 enrolled")
        self.assertNotIn("4111111111111111", out)
        self.assertIn("[REDACTED:16d]", out)

    def test_bounds_free_text_length(self) -> None:
        out = redact_text("x" * 5000)
        self.assertLess(len(out), 600)
        self.assertIn("chars)", out)

    def test_scrub_fields_recurses_and_preserves_scalars(self) -> None:
        scrubbed = scrub_fields(
            {
                "claim_count": 3,
                "ok": True,
                "note": "call 555-867-5309",
                "nested": {"ssn": "123-45-6789"},
                "items": ["a@b.com", 7],
            }
        )
        self.assertEqual(scrubbed["claim_count"], 3)
        self.assertTrue(scrubbed["ok"])
        self.assertNotIn("555-867-5309", scrubbed["note"])
        self.assertNotIn("123-45-6789", scrubbed["nested"]["ssn"])
        self.assertNotIn("a@b.com", scrubbed["items"][0])
        self.assertEqual(scrubbed["items"][1], 7)

    def test_hash_identifier_is_stable_and_non_reversible(self) -> None:
        h1 = hash_identifier("SUBSCRIBER-123")
        h2 = hash_identifier("SUBSCRIBER-123")
        self.assertEqual(h1, h2)
        self.assertNotIn("SUBSCRIBER-123", h1)
        self.assertNotEqual(h1, hash_identifier("SUBSCRIBER-124"))


class CorrelationContextTests(unittest.TestCase):
    def test_bind_get_reset(self) -> None:
        self.assertIsNone(get_correlation_id())
        token = bind_correlation_id("abc123")
        try:
            self.assertEqual(get_correlation_id(), "abc123")
        finally:
            reset_correlation_id(token)
        self.assertIsNone(get_correlation_id())

    def test_bind_none_generates_value(self) -> None:
        token = bind_correlation_id(None)
        try:
            self.assertTrue(get_correlation_id())
        finally:
            reset_correlation_id(token)

    def test_header_constant(self) -> None:
        self.assertEqual(CORRELATION_HEADER, "X-Correlation-ID")


class FormatterTests(unittest.TestCase):
    def _record(self, **extra) -> logging.LogRecord:
        record = logging.LogRecord(
            name="api",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=extra.pop("msg", "hello"),
            args=(),
            exc_info=None,
        )
        for key, value in extra.items():
            setattr(record, key, value)
        return record

    def test_formats_json_with_required_audit_fields(self) -> None:
        fmt = StructuredFormatter("triage-api")
        record = self._record(
            event="http_request",
            correlation_id="cid-1",
            audit={"path": "/jobs", "ssn": "123-45-6789"},
        )
        payload = json.loads(fmt.format(record))
        self.assertEqual(payload["service"], "triage-api")
        self.assertEqual(payload["event"], "http_request")
        self.assertEqual(payload["correlation_id"], "cid-1")
        self.assertIn("ts", payload)
        self.assertEqual(payload["level"], "INFO")
        # Structured fields are scrubbed for PHI.
        self.assertNotIn("123-45-6789", json.dumps(payload))

    def test_log_event_attaches_correlation_id(self) -> None:
        logger = logging.getLogger("test.audit.evt")
        logger.setLevel(logging.INFO)
        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = _Capture()
        logger.addHandler(handler)
        token = bind_correlation_id("cid-evt")
        try:
            log_event(logger, "thing_happened", count=2)
        finally:
            reset_correlation_id(token)
            logger.removeHandler(handler)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].event, "thing_happened")
        self.assertEqual(records[0].correlation_id, "cid-evt")
        self.assertEqual(records[0].audit, {"count": 2})


class RetentionTests(unittest.TestCase):
    def test_retention_window_is_six_years(self) -> None:
        self.assertEqual(AUDIT_RETENTION_YEARS, 6)


if __name__ == "__main__":
    unittest.main()
