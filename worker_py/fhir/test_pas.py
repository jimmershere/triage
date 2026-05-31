"""Tests for Da Vinci PAS resource builders and mappers."""
import unittest

from validation._fixtures import VALID_278
from validation.engine import validate_document

from fhir import (
    build_pas_attachment_bundle,
    build_pas_claim,
    build_pas_claim_response,
    build_pas_request_bundle,
    pas_claim_to_278_request,
    x12_278_request_to_pas_bundle,
    x12_278_response_to_claim_response,
)


def _first(bundle: dict, resource_type: str) -> dict | None:
    for entry in bundle.get("entry", []):
        if entry["resource"].get("resourceType") == resource_type:
            return entry["resource"]
    return None


ENRICHED_278 = VALID_278.replace(
    "HL*4*3*EV*1~UM*HS*I*1~",
    (
        "REF*G1*AUTH777~"
        "REF*9F*REFERRAL42~"
        "DTP*472*D8*20260510~"
        "HI*ABK:E119*ABF:I10~"
        "HL*4*3*EV*1~"
        "UM*HS*I*1*11~"
        "SV1*HC:99213*150.00*UN*1~"
    ),
)


class PasResourceTests(unittest.TestCase):
    def test_pas_claim_has_profile_and_preauthorization_use(self) -> None:
        claim = build_pas_claim(
            id="pas-1",
            patient_ref="Patient/patient-1",
            insurer_ref="Organization/payer-1",
            provider_ref="Practitioner/provider-1",
            um_category="HS",
            certification_type="I",
            service_level="1",
            service_date="20260515",
            trace_number="TRACE001",
        )
        self.assertEqual(claim["resourceType"], "Claim")
        self.assertEqual(claim["use"], "preauthorization")
        self.assertIn("davinci-pas", claim["meta"]["profile"][0])
        codes = {
            info["category"]["coding"][0]["code"]
            for info in claim["supportingInfo"]
        }
        self.assertEqual(codes, {"um-category", "certification-type", "service-level"})

    def test_pas_claim_response_has_profile_and_request_link(self) -> None:
        response = build_pas_claim_response(
            id="resp-1",
            claim_ref="Claim/pas-1",
            patient_ref="Patient/patient-1",
            insurer_ref="Organization/payer-1",
            disposition="A1",
            preauth_ref="AUTH123",
        )
        self.assertEqual(response["resourceType"], "ClaimResponse")
        self.assertEqual(response["use"], "preauthorization")
        self.assertEqual(response["request"]["reference"], "Claim/pas-1")
        self.assertEqual(response["preAuthRef"], "AUTH123")

    def test_pas_attachment_bundle_carries_document_and_binary(self) -> None:
        bundle = build_pas_attachment_bundle(
            id="attach-1",
            claim_ref="Claim/pas-1",
            patient_ref="Patient/patient-1",
            content="clinical note",
            content_type="text/plain",
        )
        types = {e["resource"]["resourceType"] for e in bundle["entry"]}
        self.assertEqual(types, {"DocumentReference", "Binary"})
        binary = _first(bundle, "Binary")
        self.assertEqual(binary["contentType"], "text/plain")
        self.assertTrue(binary["data"])

    def test_pas_request_bundle_wraps_entries(self) -> None:
        claim = build_pas_claim(
            id="pas-1",
            patient_ref="Patient/patient-1",
            insurer_ref="Organization/payer-1",
            provider_ref="Practitioner/provider-1",
        )
        bundle = build_pas_request_bundle([claim], id="bundle-1")
        self.assertEqual(bundle["resourceType"], "Bundle")
        self.assertEqual(bundle["id"], "bundle-1")
        self.assertEqual(_first(bundle, "Claim")["id"], "pas-1")


class PasMapperTests(unittest.TestCase):
    def test_x12_278_request_maps_to_pas_bundle(self) -> None:
        bundle = x12_278_request_to_pas_bundle(VALID_278)
        self.assertEqual(bundle["resourceType"], "Bundle")
        types = {e["resource"]["resourceType"] for e in bundle["entry"]}
        self.assertEqual(types, {"Patient", "Organization", "Practitioner", "Claim"})
        claim = _first(bundle, "Claim")
        self.assertEqual(claim["use"], "preauthorization")
        self.assertEqual(claim["identifier"][0]["value"], "REF278")

    def test_pas_claim_to_278_request_validates(self) -> None:
        bundle = x12_278_request_to_pas_bundle(VALID_278)
        claim = _first(bundle, "Claim")
        x12 = pas_claim_to_278_request(claim)
        report = validate_document(x12)
        self.assertEqual(report.transaction_set, "278")
        self.assertTrue(report.is_valid, [i.message for i in report.errors])

    def test_278_response_mapper_returns_claim_response_shape(self) -> None:
        response_278 = VALID_278.replace(
            "UM*HS*I*1~",
            "HCR*A1*AUTH123~UM*HS*I*1~",
        )
        response = x12_278_response_to_claim_response(response_278)
        self.assertEqual(response["resourceType"], "ClaimResponse")
        self.assertEqual(response["preAuthRef"], "AUTH123")
        self.assertEqual(response["disposition"], "A1")

    def test_pas_claim_to_278_rejects_non_pas_claim(self) -> None:
        with self.assertRaises(ValueError):
            pas_claim_to_278_request({"resourceType": "Claim", "use": "claim"})

    def test_field_level_um_sv_hi_dtp_ref_crosswalk(self) -> None:
        bundle = x12_278_request_to_pas_bundle(ENRICHED_278)
        claim = _first(bundle, "Claim")
        identifiers = {
            item["system"]: item["value"]
            for item in claim.get("identifier", [])
        }
        self.assertEqual(identifiers["urn:x12:ref:g1"], "AUTH777")
        self.assertEqual(identifiers["urn:x12:ref:9f"], "REFERRAL42")
        self.assertEqual(
            claim["diagnosis"][0]["diagnosisCodeableConcept"]["coding"][0]["code"],
            "E119",
        )
        self.assertEqual(
            claim["diagnosis"][1]["diagnosisCodeableConcept"]["coding"][0]["code"],
            "I10",
        )
        self.assertEqual(
            claim["item"][0]["productOrService"]["coding"][0]["code"],
            "99213",
        )
        self.assertEqual(claim["item"][0]["unitPrice"]["value"], 150.0)
        self.assertEqual(claim["item"][0]["quantity"]["value"], 1.0)
        supporting_categories = {
            info["category"]["coding"][0]["code"]: info
            for info in claim["supportingInfo"]
        }
        self.assertIn("um-category", supporting_categories)
        self.assertIn("certification-type", supporting_categories)
        self.assertIn("service-level", supporting_categories)
        self.assertEqual(
            supporting_categories["dtp-472"]["timingDate"],
            "2026-05-10",
        )
        self.assertEqual(
            supporting_categories["um04"]["code"]["coding"][0]["code"],
            "11",
        )

        x12 = pas_claim_to_278_request(claim)
        self.assertIn("REF*G1*AUTH777~", x12)
        self.assertIn("REF*9F*REFERRAL42~", x12)
        self.assertIn("DTP*472*D8*20260510~", x12)
        self.assertIn("HI*ABK:E119*ABF:I10~", x12)
        self.assertIn("SV1*HC:99213*150.0*UN*1.0~", x12)
        report = validate_document(x12)
        self.assertTrue(report.is_valid, [i.message for i in report.errors])


if __name__ == "__main__":
    unittest.main()
