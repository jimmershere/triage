"""DB-backed integration smoke test for the ERA + Helpdesk services.

Guarded by the ``TRIAGE_DB_SMOKE`` env var so CI (which has no database) skips it.
It is non-destructive: it creates a throwaway schema with minimal stub parent
tables, runs every service path against it, and drops the schema at teardown — it
never touches the real ``public`` Claimtrace/imports tables.

Run on the server:
    TRIAGE_DB_SMOKE=1 DATABASE_URL=postgresql://edi:edi@127.0.0.1:15432/edi \
        /home/floor2/triage-ws/venv/bin/python -m unittest tests.test_era_helpdesk_db -v
"""
import os
import unittest
import uuid

RUN = os.getenv("TRIAGE_DB_SMOKE", "").strip().lower() in {"1", "true", "yes", "on"}


@unittest.skipUnless(RUN, "set TRIAGE_DB_SMOKE=1 (and DATABASE_URL) to run DB smoke test")
class EraHelpdeskDbSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2

        cls.schema = f"ws_smoke_{uuid.uuid4().hex[:10]}"
        dsn = os.getenv("DATABASE_URL", "postgresql://edi:edi@127.0.0.1:15432/edi")
        cls.conn = psycopg2.connect(dsn, options=f"-c search_path={cls.schema}")
        with cls.conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA {cls.schema}")
            cur.execute("CREATE TABLE imports (id SERIAL PRIMARY KEY)")
            cur.execute(
                "CREATE TABLE claimtrace_claim (tracking_id UUID PRIMARY KEY, claim_id TEXT)"
            )
            cur.execute(
                """
                CREATE TABLE claimtrace_event (
                    event_id UUID PRIMARY KEY,
                    tracking_id UUID REFERENCES claimtrace_claim(tracking_id),
                    claim_id TEXT NOT NULL,
                    bundle_id TEXT,
                    operation_type TEXT NOT NULL,
                    state_hash TEXT NOT NULL,
                    payload_location TEXT NOT NULL,
                    service_name TEXT NOT NULL,
                    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    correlation_ids JSONB NOT NULL DEFAULT '{}'
                )
                """
            )
            cls.tracking_id = str(uuid.uuid4())
            cls.claim_id = "claim-smoke-1"
            cur.execute(
                "INSERT INTO claimtrace_claim (tracking_id, claim_id) VALUES (%s,%s)",
                (cls.tracking_id, cls.claim_id),
            )
        cls.conn.commit()

        from api import era_service, helpdesk_service

        cls.era = era_service
        cls.helpdesk = helpdesk_service
        cls.era.ensure_era_tables(cls.conn)
        cls.helpdesk.ensure_helpdesk_tables(cls.conn)

    @classmethod
    def tearDownClass(cls):
        try:
            with cls.conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {cls.schema} CASCADE")
            cls.conn.commit()
        finally:
            cls.conn.close()

    def _sample_835(self):
        from tests.test_era_restore import BALANCED_835

        return BALANCED_835

    def test_store_redeliver_reconstruct_reverse(self):
        stored = self.era.store_835(
            self.conn,
            content=self._sample_835(),
            tracking_id=self.tracking_id,
            claim_id=self.claim_id,
            created_by="smoke",
        )
        self.assertEqual(stored["origin"], "original")
        self.assertTrue(stored["balanced"])
        artifact_id = stored["artifact_id"]

        # Byte-exact re-delivery.
        redelivered = self.era.redeliver(self.conn, artifact_id, actor="smoke")
        self.assertTrue(redelivered["found"])
        self.assertEqual(redelivered["raw_835_text"], self._sample_835())

        # Reconstruction is balanced and stored as a distinct artifact.
        recon = self.era.reconstruct(self.conn, artifact_id, created_by="smoke")
        self.assertTrue(recon["balanced"])
        self.assertEqual(recon["artifact"]["origin"], "reconstructed")
        self.assertNotEqual(recon["artifact"]["artifact_id"], artifact_id)

        # Reversal: CLP02=22, still balanced.
        rev = self.era.reverse(self.conn, artifact_id, created_by="smoke")
        self.assertTrue(rev["balanced"])
        self.assertEqual(rev["artifact"]["origin"], "reversal")
        self.assertIn("*22*", rev["x12"])

    def test_era_artifact_is_immutable(self):
        stored = self.era.store_835(
            self.conn, content=self._sample_835(), claim_id=self.claim_id, created_by="smoke"
        )
        import psycopg2

        with self.assertRaises(psycopg2.Error):
            with self.conn.cursor() as cur:
                cur.execute(
                    "UPDATE era_artifact SET trn='tamper' WHERE artifact_id=%s",
                    (stored["artifact_id"],),
                )
        self.conn.rollback()

    def test_helpdesk_case_lifecycle(self):
        lifecycle_claim = "claim-lifecycle-unique"
        case = self.helpdesk.create_case(
            self.conn,
            claim_id=lifecycle_claim,
            tracking_id=self.tracking_id,
            trading_partner_id="TP-SMOKE",
            submitter_id="SUB-SMOKE",
            ack_type="999",
            reason_code="8",
            reason_text="Segment has data element errors.",
            segment_id="CLM",
            element_position="05",
            loop_id="2300",
            created_by="smoke",
        )
        self.assertEqual(case["status"], "open")
        self.assertTrue(case["case_number"].startswith("HD-"))

        notified = self.helpdesk.notify_submitter(self.conn, case_id=case["case_id"], actor="smoke")
        self.assertEqual(notified["case"]["status"], "submitter-notified")
        self.assertIn("REJECTION REPORT", notified["report"]["text"])
        self.assertIn("software team", notified["report"]["text"])

        awaiting = self.helpdesk.transition_status(
            self.conn, case_id=case["case_id"], target="awaiting-resubmission", actor="smoke"
        )
        self.assertEqual(awaiting["case"]["status"], "awaiting-resubmission")

        # Corrected resubmission passes -> case resolves automatically.
        result = self.helpdesk.apply_resubmission(
            self.conn,
            resubmission_claim_id="claim-smoke-1-resub",
            original_claim_id=lifecycle_claim,
            passed=True,
            actor="rabbitmq",
        )
        self.assertEqual(result["matched"], 1)
        resolved = self.helpdesk.get_case(self.conn, case["case_id"])
        self.assertEqual(resolved["case"]["status"], "resolved")
        self.assertEqual(resolved["case"]["resubmission_claim_id"], "claim-smoke-1-resub")

    def test_illegal_transition_rejected(self):
        from claimtrace.helpdesk import CaseStatusError

        case = self.helpdesk.create_case(self.conn, claim_id=self.claim_id, created_by="smoke")
        with self.assertRaises(CaseStatusError):
            self.helpdesk.transition_status(
                self.conn, case_id=case["case_id"], target="awaiting-resubmission"
            )
        self.conn.rollback()

    def test_case_event_is_append_only(self):
        import psycopg2

        case = self.helpdesk.create_case(self.conn, claim_id=self.claim_id, created_by="smoke")
        with self.assertRaises(psycopg2.Error):
            with self.conn.cursor() as cur:
                cur.execute(
                    "UPDATE helpdesk_case_event SET event_type='x' WHERE case_id=%s",
                    (case["case_id"],),
                )
        self.conn.rollback()


if __name__ == "__main__":
    unittest.main()
