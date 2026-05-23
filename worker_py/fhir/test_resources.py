"""Tests for the FHIR R4 resource builders."""
import unittest

from fhir.model import (
    CPT,
    ICD10_CM,
    NPI,
    codeable_concept,
    coding,
    fhir_date,
    gender_from_x12,
    gender_to_x12,
    human_name,
    identifier,
    money,
)
from fhir.resources import (
    build_bundle,
    build_claim,
    build_coverage_eligibility_request,
    build_coverage_eligibility_response,
    build_explanation_of_benefit,
    build_organization,
    build_patient,
    build_practitioner,
)


class DatatypeTests(unittest.TestCase):
    def test_codeable_concept_structure(self) -> None:
        cc = codeable_concept([coding(ICD10_CM, "E119", "Type 2 diabetes")])
        self.assertEqual(cc["coding"][0]["system"], ICD10_CM)
        self.assertEqual(cc["coding"][0]["code"], "E119")

    def test_codeable_concept_text_only(self) -> None:
        cc = codeable_concept(text="free text")
        self.assertEqual(cc, {"text": "free text"})

    def test_identifier_with_system(self) -> None:
        ident = identifier("1234567893", system=NPI, type_code="NPI")
        self.assertEqual(ident["system"], NPI)
        self.assertEqual(ident["value"], "1234567893")
        self.assertEqual(ident["type"]["coding"][0]["code"], "NPI")

    def test_identifier_empty_returns_none(self) -> None:
        self.assertIsNone(identifier(None))
        self.assertIsNone(identifier(""))

    def test_human_name(self) -> None:
        self.assertEqual(human_name(family="DOE", given="JANE")["family"], "DOE")
        self.assertIsNone(human_name())

    def test_fhir_date_conversion(self) -> None:
        self.assertEqual(fhir_date("20260515"), "2026-05-15")
        self.assertIsNone(fhir_date("bad"))

    def test_gender_round_trip(self) -> None:
        self.assertEqual(gender_from_x12("F"), "female")
        self.assertEqual(gender_to_x12("female"), "F")

    def test_money(self) -> None:
        self.assertEqual(money("150.00")["value"], 150.0)
        self.assertEqual(money(150)["currency"], "USD")
        self.assertIsNone(money(""))


class PatientResourceTests(unittest.TestCase):
    def test_patient_fields(self) -> None:
        patient = build_patient(
            id="patient-1",
            member_id="MEM123",
            family="DOE",
            given="JANE",
            gender="F",
            birth_date="19800101",
        )
        self.assertEqual(patient["resourceType"], "Patient")
        self.assertEqual(patient["id"], "patient-1")
        self.assertEqual(patient["birthDate"], "1980-01-01")
        self.assertEqual(patient["gender"], "female")
        self.assertEqual(patient["identifier"][0]["value"], "MEM123")

    def test_patient_with_missing_optional_fields(self) -> None:
        patient = build_patient(id="patient-2")
        self.assertEqual(patient["resourceType"], "Patient")
        self.assertNotIn("name", patient)


class OrganizationResourceTests(unittest.TestCase):
    def test_organization_carries_npi_and_tax_id(self) -> None:
        org = build_organization(
            id="org-1", npi="1234567893", tax_id="123456789", name="CLINIC"
        )
        ident_systems = {i["system"] for i in org["identifier"]}
        self.assertIn(NPI, ident_systems)
        self.assertEqual(org["name"], "CLINIC")


class PractitionerResourceTests(unittest.TestCase):
    def test_practitioner_npi(self) -> None:
        pract = build_practitioner(id="p-1", npi="1234567893", family="SMITH")
        self.assertEqual(pract["identifier"][0]["value"], "1234567893")
        self.assertEqual(pract["name"][0]["family"], "SMITH")


class ClaimResourceTests(unittest.TestCase):
    def test_minimal_claim(self) -> None:
        claim = build_claim(
            id="c-1",
            patient_ref="Patient/p-1",
            insurer_ref="Organization/payer-1",
            provider_ref="Organization/billing-1",
            diagnosis_codes=["E119"],
            items=[{"code": "99213", "charge": "150", "units": "1", "sequence": 1}],
            total_charge="150",
        )
        self.assertEqual(claim["resourceType"], "Claim")
        self.assertEqual(claim["type"]["coding"][0]["code"], "professional")
        self.assertEqual(
            claim["diagnosis"][0]["diagnosisCodeableConcept"]["coding"][0]["code"],
            "E119",
        )
        self.assertEqual(claim["item"][0]["productOrService"]["coding"][0]["system"], CPT)
        self.assertEqual(claim["total"]["value"], 150.0)

    def test_institutional_claim_carries_type_of_bill(self) -> None:
        claim = build_claim(
            id="c-i",
            variant="I",
            patient_ref="Patient/p",
            insurer_ref="Organization/i",
            provider_ref="Organization/p",
            type_of_bill="0111",
            items=[],
        )
        self.assertEqual(claim["type"]["coding"][0]["code"], "institutional")
        self.assertEqual(claim["supportingInfo"][0]["code"]["coding"][0]["code"], "0111")


class EobResourceTests(unittest.TestCase):
    def test_eob_with_items_and_totals(self) -> None:
        eob = build_explanation_of_benefit(
            id="eob-1",
            patient_ref="Patient/p-1",
            insurer_ref="Organization/payer-1",
            provider_ref="Organization/payee-1",
            items=[{"code": "99213", "charge": "150", "paid": "120"}],
            totals=[{"category": "benefit", "amount": "120"}],
            payment_amount="120",
            payment_date="20260515",
        )
        self.assertEqual(eob["resourceType"], "ExplanationOfBenefit")
        self.assertIn("profile", eob["meta"])
        self.assertEqual(eob["item"][0]["adjudication"][0]["category"]["coding"][0]["code"], "submitted")
        self.assertEqual(eob["payment"]["amount"]["value"], 120.0)


class EligibilityResourceTests(unittest.TestCase):
    def test_request(self) -> None:
        req = build_coverage_eligibility_request(
            id="er-1",
            patient_ref="Patient/p-1",
            insurer_ref="Organization/payer-1",
            provider_ref="Organization/provider-1",
            service_types=["30"],
            service_date="20260515",
        )
        self.assertEqual(req["resourceType"], "CoverageEligibilityRequest")
        self.assertEqual(req["servicedDate"], "2026-05-15")
        self.assertEqual(req["item"][0]["category"]["coding"][0]["code"], "30")

    def test_response(self) -> None:
        resp = build_coverage_eligibility_response(
            id="er-r-1",
            request_ref="CoverageEligibilityRequest/er-1",
            patient_ref="Patient/p-1",
            insurer_ref="Organization/payer-1",
            benefits=[{"service_type": "30", "eligibility": "active"}],
        )
        self.assertEqual(resp["resourceType"], "CoverageEligibilityResponse")
        self.assertEqual(resp["insurance"][0]["item"][0]["category"]["coding"][0]["code"], "30")


class BundleTests(unittest.TestCase):
    def test_collection_bundle(self) -> None:
        bundle = build_bundle(
            [build_patient(id="p-1"), build_organization(id="org-1", name="P")]
        )
        self.assertEqual(bundle["resourceType"], "Bundle")
        self.assertEqual(bundle["type"], "collection")
        self.assertEqual(len(bundle["entry"]), 2)

    def test_transaction_bundle_adds_request_entries(self) -> None:
        bundle = build_bundle(
            [build_patient(id="p-1")], bundle_type="transaction"
        )
        self.assertEqual(bundle["entry"][0]["request"]["method"], "POST")
        self.assertEqual(bundle["entry"][0]["request"]["url"], "Patient")


if __name__ == "__main__":
    unittest.main()
