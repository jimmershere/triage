"""Tests for the TurboHEDI end-to-end pipeline."""
import unittest

from validation._fixtures import (
    VALID_278,
    VALID_835,
    VALID_837P,
    with_unbalanced_claim,
)
from turbo_pipeline import run_pipeline


class CleanPipelineTests(unittest.TestCase):
    def test_clean_837_passes_validation_and_scrubbing(self) -> None:
        result = run_pipeline(VALID_837P)
        self.assertTrue(result.valid)
        self.assertTrue(result.scrubbing_clean)
        self.assertTrue(result.ready_to_submit)
        self.assertEqual(result.transaction_set, "837")

    def test_pipeline_emits_fhir_when_requested(self) -> None:
        result = run_pipeline(VALID_837P, to_fhir=True)
        self.assertIsNotNone(result.fhir)
        self.assertEqual(result.fhir["resourceType"], "Bundle")
        types = {e["resource"]["resourceType"] for e in result.fhir["entry"]}
        self.assertIn("Claim", types)

    def test_pipeline_emits_acknowledgments_for_837(self) -> None:
        result = run_pipeline(VALID_837P, generate_acks=True)
        self.assertIsNotNone(result.acknowledgments)
        self.assertSetEqual(
            set(result.acknowledgments.keys()), {"TA1", "999", "277CA"}
        )
        self.assertIn("AK1", result.acknowledgments["999"])


class BrokenPipelineTests(unittest.TestCase):
    def test_invalid_claim_blocks_submission(self) -> None:
        result = run_pipeline(with_unbalanced_claim())
        self.assertFalse(result.valid)
        self.assertFalse(result.ready_to_submit)


class RemittancePipelineTests(unittest.TestCase):
    def test_835_pipeline_maps_to_eob_bundle(self) -> None:
        result = run_pipeline(VALID_835, to_fhir=True)
        self.assertEqual(result.transaction_set, "835")
        self.assertTrue(result.valid)
        self.assertIsNotNone(result.fhir)
        types = {e["resource"]["resourceType"] for e in result.fhir["entry"]}
        self.assertIn("ExplanationOfBenefit", types)


class PriorAuthorizationPipelineTests(unittest.TestCase):
    def test_278_pipeline_maps_to_pas_bundle(self) -> None:
        result = run_pipeline(VALID_278, to_fhir=True)
        self.assertEqual(result.transaction_set, "278")
        self.assertTrue(result.valid)
        self.assertIsNotNone(result.fhir)
        self.assertEqual(result.fhir["resourceType"], "Bundle")
        types = {e["resource"]["resourceType"] for e in result.fhir["entry"]}
        self.assertIn("Claim", types)
        claim = next(
            e["resource"] for e in result.fhir["entry"]
            if e["resource"]["resourceType"] == "Claim"
        )
        self.assertEqual(claim["use"], "preauthorization")


class ScrubbingSwitchTests(unittest.TestCase):
    def test_scrub_disabled_returns_none(self) -> None:
        result = run_pipeline(VALID_837P, scrub=False)
        self.assertIsNone(result.scrubbing)
        self.assertTrue(result.scrubbing_clean)  # vacuously


if __name__ == "__main__":
    unittest.main()
