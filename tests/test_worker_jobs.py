"""Unit tests for the ERA + helpdesk RabbitMQ job dispatchers (no broker / no DB).

The dispatch logic is exercised with injected fake services and a fake DB
connection so it runs in the stdlib unittest suite.
"""
import base64
import tempfile
import unittest

from worker_py import era_jobs, helpdesk_jobs


class FakeEraService:
    def __init__(self):
        self.calls = []

    def redeliver(self, conn, artifact_id, *, actor=None):
        self.calls.append(("redeliver", artifact_id, actor))
        return {
            "found": True,
            "artifact_id": artifact_id,
            "trn": "TRN123",
            "raw_835_b64": base64.b64encode(b"ISA*RAW~").decode("ascii"),
            "filename": "redeliver_TRN123.835",
        }

    def reconstruct(self, conn, artifact_id, *, created_by=None):
        self.calls.append(("reconstruct", artifact_id, created_by))
        return {
            "found": True,
            "artifact": {"artifact_id": "new-artifact-1"},
            "balanced": True,
            "x12": "ISA*REBUILT~",
        }


class FakeHelpdeskService:
    def __init__(self):
        self.calls = []

    def apply_resubmission(self, conn, **kwargs):
        self.calls.append(kwargs)
        return {"matched": 1, "updated": [{"case_id": "c1", "status": "resolved"}]}


class EraJobTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        era_jobs.ARCHIVE_DIR = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_redeliver_writes_byte_exact_output(self):
        svc = FakeEraService()
        result = era_jobs.process_era_job(
            {"job": "era.redeliver", "artifact_id": "a1"}, conn=None, service=svc
        )
        self.assertTrue(result["found"])
        self.assertEqual(result["trn"], "TRN123")
        with open(result["output"], "rb") as fh:
            self.assertEqual(fh.read(), b"ISA*RAW~")

    def test_reconstruct_dispatch(self):
        svc = FakeEraService()
        result = era_jobs.process_era_job(
            {"job": "era.reconstruct", "artifact_id": "a2"}, conn=None, service=svc
        )
        self.assertEqual(result["new_artifact_id"], "new-artifact-1")
        self.assertTrue(result["balanced"])

    def test_unknown_job_rejected(self):
        with self.assertRaises(ValueError):
            era_jobs.process_era_job({"job": "era.bogus", "artifact_id": "x"}, conn=None, service=FakeEraService())

    def test_missing_artifact_id_rejected(self):
        with self.assertRaises(ValueError):
            era_jobs.process_era_job({"job": "era.redeliver"}, conn=None, service=FakeEraService())


class HelpdeskJobTests(unittest.TestCase):
    def test_resubmission_dispatch(self):
        svc = FakeHelpdeskService()
        result = helpdesk_jobs.process_resubmission_event(
            {"resubmission_claim_id": "c1", "passed": True}, conn=None, service=svc
        )
        self.assertEqual(result["matched"], 1)
        self.assertEqual(svc.calls[0]["resubmission_claim_id"], "c1")
        self.assertTrue(svc.calls[0]["passed"])

    def test_missing_claim_id_rejected(self):
        with self.assertRaises(ValueError):
            helpdesk_jobs.process_resubmission_event({"passed": True}, conn=None, service=FakeHelpdeskService())


if __name__ == "__main__":
    unittest.main()
