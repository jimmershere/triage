"""Tests for the X12 -> CMS authenticity harness."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from tools_x12_cms_harness import run_harness


class X12CMSHarnessTests(unittest.TestCase):
    def test_large_837_sample_projects_claims(self) -> None:
        sample = Path(__file__).resolve().parents[1] / "samples" / "x12_837_large_valid.x12"
        report = run_harness(sample)
        self.assertEqual(report.detected_transaction, "837")
        self.assertGreater(report.segment_count, 1000)
        self.assertEqual(report.claim_count, 3200)
        self.assertTrue(report.valid)
        self.assertTrue(report.claims)
        first = report.claims[0]
        self.assertIsNotNone(first.claim_id)
        self.assertIsNotNone(first.billing_provider_name)

    def test_spec_bundle_path_when_present(self) -> None:
        # The X222 research bundle is a licensed artifact and is not committed to
        # the repo. Assert its path shape only when it is actually installed.
        sample = Path(__file__).resolve().parents[1] / "samples" / "x12_837_large_valid.x12"
        report = run_harness(sample)
        pdf = report.spec_bundle.get("pdf")
        if pdf:
            self.assertTrue(pdf.endswith("x222-005010-PDF.pdf"))
        else:
            self.skipTest("X222 spec bundle not installed")


if __name__ == "__main__":
    unittest.main()
