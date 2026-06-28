"""Tests for the worker's self-contained PHI-safe audit logging (Workstream 3).

The worker carries a mirror of ``claimtrace.audit.logging`` because its
deployment image does not ship the ``claimtrace`` package. These tests pin the
PHI-safe behavior and the correlation-id propagation contract that lets the
worker continue the lineage the API started across the RabbitMQ hop.
"""

import json
import logging
import unittest

import audit_log


class WorkerRedactionTests(unittest.TestCase):
    def test_masks_direct_identifiers(self) -> None:
        out = audit_log.redact_text("ssn 123-45-6789 mail x@y.com")
        self.assertNotIn("123-45-6789", out)
        self.assertNotIn("x@y.com", out)

    def test_scrub_fields_preserves_scalars(self) -> None:
        scrubbed = audit_log.scrub_fields({"n": 5, "s": "123-45-6789"})
        self.assertEqual(scrubbed["n"], 5)
        self.assertNotIn("123-45-6789", scrubbed["s"])


class WorkerCorrelationTests(unittest.TestCase):
    def test_propagation_chain_from_payload(self) -> None:
        # Simulate the API -> RMQ -> worker hop: the API publishes a payload
        # carrying ``correlation_id`` equal to the job id; the worker binds it.
        payload = {"job_id": "job-xyz", "correlation_id": "job-xyz"}
        correlation_id = payload.get("correlation_id") or payload.get("job_id")
        token = audit_log.bind_correlation_id(correlation_id)
        try:
            self.assertEqual(audit_log.get_correlation_id(), "job-xyz")
        finally:
            audit_log.reset_correlation_id(token)
        self.assertIsNone(audit_log.get_correlation_id())

    def test_falls_back_to_job_id_when_correlation_absent(self) -> None:
        payload = {"job_id": "job-abc"}
        correlation_id = payload.get("correlation_id") or payload.get("job_id")
        token = audit_log.bind_correlation_id(correlation_id)
        try:
            self.assertEqual(audit_log.get_correlation_id(), "job-abc")
        finally:
            audit_log.reset_correlation_id(token)


class WorkerFormatterTests(unittest.TestCase):
    def test_json_schema_matches_contract(self) -> None:
        fmt = audit_log.StructuredFormatter("triage-worker")
        record = logging.LogRecord(
            name="worker",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="processed",
            args=(),
            exc_info=None,
        )
        record.event = "worker_message_processed"
        record.correlation_id = "job-xyz"
        record.audit = {"import_id": 7}
        payload = json.loads(fmt.format(record))
        for key in ("ts", "level", "logger", "service", "event", "msg", "correlation_id"):
            self.assertIn(key, payload)
        self.assertEqual(payload["service"], "triage-worker")
        self.assertEqual(payload["correlation_id"], "job-xyz")
        self.assertEqual(payload["fields"], {"import_id": 7})


if __name__ == "__main__":
    unittest.main()
