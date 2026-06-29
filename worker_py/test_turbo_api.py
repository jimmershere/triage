"""Tests for the TurboHEDI HTTP API surface (``api/turbo_routes.py``).

Route handlers are called directly with their Pydantic request models so the
suite stays free of httpx / TestClient. The integration with FastAPI itself
is exercised by simply importing the module (which registers the router).
"""
import os
import sys
import unittest

# Put the project root on sys.path so ``api.turbo_routes`` is importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from api.turbo_routes import (  # noqa: E402
    FhirClaimRequest,
    PipelineRequest,
    RejectionReportRequest,
    ValidateRequest,
    X12Body,
    capability,
    fhir_claim_submit,
    fhir_from_x12,
    pipeline,
    rejection_report,
    validate,
)
from fastapi import HTTPException  # noqa: E402

from validation._fixtures import VALID_837P, VALID_835, with_unbalanced_claim  # noqa: E402


class CapabilityTests(unittest.TestCase):
    def test_capability_lists_supported_transactions(self) -> None:
        cap = capability()
        self.assertEqual(cap["resourceType"], "CapabilityStatement")
        joined = " ".join(cap["transactionSetsSupported"])
        for required in ("837P", "837I", "837D", "835", "270/271", "276/277", "278"):
            self.assertIn(required, joined)
        # Capability statement is honest about per-type enforcement depth
        # (no blanket "SNIP 1-7 validated" overclaim).
        self.assertNotIn("snipLevelsValidated", cap)
        snip = cap["snipValidation"]
        self.assertEqual(set(snip["byType"]), {"1", "2", "3", "4", "5", "6", "7"})
        self.assertEqual(snip["byType"]["1"]["status"], "enforced")
        statuses = {t["status"] for t in snip["byType"].values()}
        self.assertTrue(statuses & {"partial", "framework"})


class ValidateRouteTests(unittest.TestCase):
    def test_clean_837_validates(self) -> None:
        body = validate(ValidateRequest(x12=VALID_837P))
        self.assertTrue(body["valid"])
        self.assertEqual(body["transaction_set"], "837")

    def test_empty_body_returns_400(self) -> None:
        with self.assertRaises(HTTPException) as exc:
            validate(ValidateRequest(x12="   "))
        self.assertEqual(exc.exception.status_code, 400)

    def test_edig_parity_policy_accepts_lenient_claim(self) -> None:
        unbalanced = VALID_837P.replace("CLM*CLAIM001*150", "CLM*CLAIM001*999")
        strict = validate(ValidateRequest(x12=unbalanced))
        self.assertFalse(strict["valid"])
        lenient = validate(
            ValidateRequest(x12=unbalanced, snip_policy="edig-parity-v1")
        )
        self.assertTrue(lenient["valid"])


class PipelineRouteTests(unittest.TestCase):
    def test_pipeline_validates_and_scrubs(self) -> None:
        body = pipeline(PipelineRequest(x12=VALID_837P))
        self.assertTrue(body["validation"]["valid"])
        self.assertTrue(body["scrubbing"]["clean"])

    def test_pipeline_can_emit_fhir_and_acks(self) -> None:
        body = pipeline(
            PipelineRequest(x12=VALID_837P, to_fhir=True, generate_acks=True)
        )
        self.assertIsNotNone(body["fhir"])
        # Default ack profile is 999_only; 277CA stays dark.
        self.assertSetEqual(set(body["acknowledgments"]), {"TA1", "999"})

    def test_pipeline_emits_277ca_on_opt_in(self) -> None:
        body = pipeline(
            PipelineRequest(
                x12=VALID_837P, generate_acks=True, ack_profile="999_plus_277CA"
            )
        )
        self.assertSetEqual(
            set(body["acknowledgments"]), {"TA1", "999", "277CA"}
        )


class RejectionReportRouteTests(unittest.TestCase):
    def test_json_report_is_position_keyed(self) -> None:
        body = rejection_report(
            RejectionReportRequest(x12=with_unbalanced_claim(), source="batch.txt")
        )
        self.assertEqual(body["failed"], 1)
        self.assertIn("1", body["claims"])
        self.assertEqual(body["claims"]["1"]["claim_id"], "CLAIM001")
        self.assertIn("claimtrace_event", body)
        self.assertEqual(
            body["claimtrace_event"]["operation_type"], "rejection.report.generated"
        )

    def test_csv_report_returns_text(self) -> None:
        resp = rejection_report(
            RejectionReportRequest(x12=with_unbalanced_claim()), fmt="csv"
        )
        self.assertEqual(resp.media_type, "text/csv")
        self.assertIn(b"CLAIM001", resp.body)

    def test_clean_claim_passes_in_report(self) -> None:
        body = rejection_report(RejectionReportRequest(x12=VALID_837P))
        self.assertEqual(body["failed"], 0)
        self.assertEqual(body["passed"], 1)

    def test_explicit_positions_are_honoured(self) -> None:
        body = rejection_report(
            RejectionReportRequest(
                x12=with_unbalanced_claim(),
                positions=[{"claim_id": "CLAIM001", "position": 42, "patient_control_number": "PCN9"}],
            )
        )
        self.assertIn("42", body["claims"])
        self.assertEqual(body["claims"]["42"]["patient_control_number"], "PCN9")


class FhirRouteTests(unittest.TestCase):
    def test_fhir_from_x12_bundles_claim(self) -> None:
        body = fhir_from_x12(X12Body(x12=VALID_837P))
        self.assertEqual(body["bundle"]["resourceType"], "Bundle")
        types = {e["resource"]["resourceType"] for e in body["bundle"]["entry"]}
        self.assertIn("Claim", types)

    def test_fhir_from_x12_rejects_unsupported_transaction(self) -> None:
        unsupported = VALID_835.replace("ST*835*0001", "ST*999*0001")
        with self.assertRaises(HTTPException) as exc:
            fhir_from_x12(X12Body(x12=unsupported))
        self.assertEqual(exc.exception.status_code, 415)

    def test_fhir_claim_submit_round_trip(self) -> None:
        # First get a FHIR bundle from a clean 837 ...
        fhir_body = fhir_from_x12(X12Body(x12=VALID_837P))
        # ... then submit it back. The resulting X12 must validate clean.
        response = fhir_claim_submit(
            FhirClaimRequest(resource=fhir_body["bundle"])
        )
        self.assertIn("x12", response)
        self.assertTrue(response["validation"]["valid"])

    def test_fhir_claim_submit_rejects_non_claim(self) -> None:
        with self.assertRaises(HTTPException) as exc:
            fhir_claim_submit(
                FhirClaimRequest(resource={"resourceType": "Patient", "id": "p-1"})
            )
        self.assertEqual(exc.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
