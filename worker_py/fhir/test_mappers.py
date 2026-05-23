"""Tests for the X12 <-> FHIR mappers."""
import unittest

from validation._fixtures import (
    VALID_270,
    VALID_271,
    VALID_835,
    VALID_837D,
    VALID_837I,
    VALID_837P,
)
from validation.engine import validate_document

from fhir import (
    eligibility_request_to_fhir,
    eligibility_response_to_fhir,
    fhir_claim_to_837,
    remittance_to_fhir,
    submission_to_fhir,
)


def _types(bundle: dict) -> set[str]:
    return {e["resource"]["resourceType"] for e in bundle.get("entry", [])}


def _first(bundle: dict, resource_type: str) -> dict | None:
    for entry in bundle.get("entry", []):
        if entry["resource"].get("resourceType") == resource_type:
            return entry["resource"]
    return None


class SubmissionToFhirTests(unittest.TestCase):
    def test_professional_bundle_structure(self) -> None:
        bundle = submission_to_fhir(VALID_837P)
        self.assertEqual(bundle["resourceType"], "Bundle")
        self.assertEqual(bundle["type"], "transaction")
        self.assertEqual(_types(bundle), {"Patient", "Organization", "Claim"})

    def test_claim_uses_professional_type(self) -> None:
        bundle = submission_to_fhir(VALID_837P)
        claim = _first(bundle, "Claim")
        self.assertEqual(claim["type"]["coding"][0]["code"], "professional")
        self.assertEqual(claim["total"]["value"], 150.0)
        self.assertEqual(len(claim["item"]), 2)

    def test_institutional_variant(self) -> None:
        bundle = submission_to_fhir(VALID_837I)
        claim = _first(bundle, "Claim")
        self.assertEqual(claim["type"]["coding"][0]["code"], "institutional")
        # Type-of-bill is carried in supportingInfo for institutional claims.
        self.assertIn("supportingInfo", claim)

    def test_dental_variant(self) -> None:
        bundle = submission_to_fhir(VALID_837D)
        claim = _first(bundle, "Claim")
        self.assertEqual(claim["type"]["coding"][0]["code"], "oral")

    def test_patient_carries_member_id(self) -> None:
        bundle = submission_to_fhir(VALID_837P)
        patient = _first(bundle, "Patient")
        self.assertEqual(patient["identifier"][0]["value"], "MEM123")


class RemittanceToFhirTests(unittest.TestCase):
    def test_eob_bundle(self) -> None:
        bundle = remittance_to_fhir(VALID_835)
        self.assertIn("ExplanationOfBenefit", _types(bundle))
        eob = _first(bundle, "ExplanationOfBenefit")
        self.assertEqual(eob["resourceType"], "ExplanationOfBenefit")
        self.assertEqual(eob["payment"]["amount"]["value"], 200.0)

    def test_eob_items_carry_charge_and_paid(self) -> None:
        bundle = remittance_to_fhir(VALID_835)
        eob = _first(bundle, "ExplanationOfBenefit")
        item = eob["item"][0]
        cats = {a["category"]["coding"][0]["code"] for a in item["adjudication"]}
        self.assertIn("submitted", cats)
        self.assertIn("benefit", cats)


class EligibilityMapperTests(unittest.TestCase):
    def test_270_to_eligibility_request(self) -> None:
        bundle = eligibility_request_to_fhir(VALID_270)
        self.assertIn("CoverageEligibilityRequest", _types(bundle))
        req = _first(bundle, "CoverageEligibilityRequest")
        # 270 EQ*30 -> service type 30 (Health Benefit Plan Coverage).
        self.assertEqual(req["item"][0]["category"]["coding"][0]["code"], "30")

    def test_271_to_eligibility_response(self) -> None:
        bundle = eligibility_response_to_fhir(VALID_271)
        self.assertIn("CoverageEligibilityResponse", _types(bundle))
        resp = _first(bundle, "CoverageEligibilityResponse")
        self.assertEqual(resp["outcome"], "complete")


class RoundTripTests(unittest.TestCase):
    def test_837_to_fhir_to_837_validates_clean(self) -> None:
        bundle = submission_to_fhir(VALID_837P)
        x12 = fhir_claim_to_837(bundle)
        report = validate_document(x12)
        self.assertTrue(
            report.is_valid,
            f"Round-tripped 837 must validate clean; errors: "
            f"{[i.code for i in report.errors]}",
        )
        self.assertEqual(report.claim_count, 1)
        self.assertEqual(report.claims[0].total_charge, "150.00")

    def test_fhir_to_x12_rejects_non_claim_input(self) -> None:
        with self.assertRaises(ValueError):
            fhir_claim_to_837({"resourceType": "Patient", "id": "p-1"})


if __name__ == "__main__":
    unittest.main()
