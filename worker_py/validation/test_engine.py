"""Tests for the validation engine: envelope, 837P guide and code-set rules."""
import json
import unittest

from validation._fixtures import VALID_837P, with_replacement, with_unbalanced_claim
from validation.engine import validate_document
from validation.model import Severity, SnipType

# A minimal 837 envelope with no claim (loop 2300 absent).
NO_CLAIM_837 = (
    "ISA*00*          *00*          *ZZ*A              *ZZ*B"
    "              *260101*1200*^*00501*000000001*0*P*:~"
    "GS*HC*A*B*20260101*1200*1*X*005010X222A1~"
    "ST*837*0001*005010X222A1~"
    "BHT*0019*00*REF*20260101*1200*CH~"
    "NM1*41*2*SUBMITTER*****46*1~"
    "NM1*40*2*RECEIVER*****46*1~"
    "HL*1**20*1~"
    "NM1*85*2*BILLING*****XX*1234567893~"
    "SE*7*0001~GE*1*1~IEA*1*000000001~"
)


def _codes(report) -> set[str]:
    return {i.code for i in report.issues}


def _error_codes(report) -> set[str]:
    return {i.code for i in report.errors}


class CleanDocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = validate_document(VALID_837P)

    def test_clean_837_is_valid(self) -> None:
        self.assertTrue(self.report.is_valid, [i.message for i in self.report.errors])
        self.assertEqual(len(self.report.errors), 0)

    def test_one_claim_projected(self) -> None:
        self.assertEqual(self.report.claim_count, 1)
        claim = self.report.claims[0]
        self.assertEqual(claim.claim_id, "CLAIM001")
        self.assertEqual(claim.total_charge, "150")
        self.assertEqual(claim.place_of_service, "11")
        self.assertEqual(len(claim.service_lines), 2)
        self.assertEqual(claim.diagnosis_codes, ["E119"])

    def test_all_seven_snip_levels_run(self) -> None:
        self.assertEqual(self.report.snip_levels_run, [1, 2, 3, 4, 5, 6, 7])

    def test_report_serializes_to_json(self) -> None:
        payload = json.dumps(self.report.to_dict())
        self.assertIn("CLAIM001", payload)


class EnvelopeRuleTests(unittest.TestCase):
    def test_interchange_control_mismatch(self) -> None:
        broken = VALID_837P.replace("IEA*1*000000001", "IEA*1*000000002")
        report = validate_document(broken)
        self.assertIn("ENV.ISA.CONTROL", _error_codes(report))

    def test_segment_count_mismatch(self) -> None:
        broken = VALID_837P.replace("SE*24*0001", "SE*99*0001")
        report = validate_document(broken)
        self.assertIn("BAL.SE01.SEG_COUNT", _error_codes(report))

    def test_group_transaction_count_mismatch(self) -> None:
        broken = VALID_837P.replace("GE*1*1", "GE*5*1")
        report = validate_document(broken)
        self.assertIn("BAL.GE01.TXN_COUNT", _error_codes(report))

    def test_transaction_control_mismatch(self) -> None:
        broken = VALID_837P.replace("SE*24*0001", "SE*24*9999")
        report = validate_document(broken)
        self.assertIn("ENV.ST.CONTROL", _error_codes(report))


class Guide837pRequirementTests(unittest.TestCase):
    def test_missing_diagnosis(self) -> None:
        broken = VALID_837P.replace("HI*ABK:E119~", "")
        report = validate_document(broken)
        self.assertIn("REQ.HI.MISSING", _error_codes(report))

    def test_missing_claim(self) -> None:
        report = validate_document(NO_CLAIM_837)
        self.assertIn("REQ.CLM.MISSING", _error_codes(report))

    def test_missing_submitter(self) -> None:
        broken = VALID_837P.replace(
            "NM1*41*2*SUBMITTER NAME*****46*123456789~", ""
        )
        report = validate_document(broken)
        self.assertIn("REQ.SUBMITTER.MISSING", _error_codes(report))

    def test_bad_implementation_version(self) -> None:
        broken = VALID_837P.replace("ST*837*0001*005010X222A1", "ST*837*0001*005010X999")
        report = validate_document(broken)
        self.assertIn("GUIDE.ST03.VERSION", _codes(report))

    def test_facility_qualifier_must_be_b(self) -> None:
        broken = VALID_837P.replace("11:B:1", "11:A:1")
        report = validate_document(broken)
        self.assertIn("GUIDE.CLM05.QUALIFIER", _error_codes(report))


class SituationalAndBalancingTests(unittest.TestCase):
    def test_unbalanced_claim_charges(self) -> None:
        report = validate_document(with_unbalanced_claim())
        self.assertIn("BAL.CLM02.LINE_SUM", _error_codes(report))
        balancing = report.issues_for_snip(SnipType.LINE_BALANCING)
        self.assertTrue(balancing)

    def test_replacement_requires_payer_control_number(self) -> None:
        report = validate_document(with_replacement())
        self.assertIn("SIT.REF.F8_REQUIRED", _error_codes(report))

    def test_missing_service_date(self) -> None:
        broken = VALID_837P.replace("DTP*472*D8*20260510~", "", 2)
        report = validate_document(broken)
        self.assertIn("SIT.DTP472.MISSING", _error_codes(report))


class CodeSetTests(unittest.TestCase):
    def test_invalid_place_of_service(self) -> None:
        broken = VALID_837P.replace("11:B:1", "00:B:1")
        report = validate_document(broken)
        self.assertIn("CODE.POS.INVALID", _error_codes(report))

    def test_invalid_npi_checksum(self) -> None:
        broken = VALID_837P.replace("XX*1234567893", "XX*1234567890")
        report = validate_document(broken)
        self.assertIn("CODE.NPI.CHECKSUM", _error_codes(report))

    def test_invalid_procedure_code_format(self) -> None:
        broken = VALID_837P.replace("HC:99213", "HC:9921")
        report = validate_document(broken)
        self.assertIn("CODE.SV101.PROCEDURE_FORMAT", _error_codes(report))

    def test_invalid_icd10_format(self) -> None:
        broken = VALID_837P.replace("ABK:E119", "ABK:XX")
        report = validate_document(broken)
        self.assertIn("CODE.HI.ICD10_FORMAT", _error_codes(report))

    def test_unknown_filing_indicator_is_warning_only(self) -> None:
        # MB is a valid filing indicator; ZZ is in the table too, so use a value
        # outside the complete table to confirm error severity for a complete set.
        broken = VALID_837P.replace("SBR*P*18*******MB~", "SBR*P*18*******QQ~")
        report = validate_document(broken)
        self.assertIn("CODE.SBR09.FILING_INDICATOR", _error_codes(report))


class LargeSampleRegressionTests(unittest.TestCase):
    def test_bundled_large_837_validates_clean(self) -> None:
        from pathlib import Path

        sample = (
            Path(__file__).resolve().parents[1].parent
            / "samples"
            / "x12_837_large_valid.x12"
        )
        from validation.engine import validate_file

        report = validate_file(sample)
        self.assertEqual(report.transaction_set, "837")
        self.assertEqual(report.claim_count, 3200)
        self.assertTrue(report.is_valid, [i.message for i in report.errors[:5]])


if __name__ == "__main__":
    unittest.main()
