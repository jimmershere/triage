"""Smoke test: ``worker.py`` imports cleanly with the turbo-pipeline hook.

The full ``worker.process_payload`` flow needs RabbitMQ + Postgres so we can't
exercise it from the unit suite, but we can prove the integration point exists
and the module still loads after the Phase-6 edit.
"""
import importlib
import unittest


class WorkerTurboHookTests(unittest.TestCase):
    def test_worker_module_imports(self) -> None:
        try:
            import dotenv  # noqa: F401
            import pika  # noqa: F401
            import psycopg2  # noqa: F401
        except ImportError as exc:
            self.skipTest(f"worker runtime dep missing: {exc}")
        worker = importlib.import_module("worker")
        self.assertTrue(callable(getattr(worker, "turbo_run_pipeline", None)))
        self.assertIn("TURBO_PIPELINE_ENABLED", dir(worker))
        self.assertTrue(callable(getattr(worker, "process_payload", None)))


if __name__ == "__main__":
    unittest.main()
