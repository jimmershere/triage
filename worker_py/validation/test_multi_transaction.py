"""Tests for multi-transaction support: 837I, 837D, 835, 270/271, 276/277, 278."""
import unittest

from validation import _fixtures as F
from validation.engine import validate_document


def _error_codes(report) -> set[str]:
    return {i.code for i in report.errors}


class CleanFixtureTests(unittest.TestCase):
    """Every bundled multi-transaction fixture must validate clean."""

    def test_all_fixtures_validate_clean(self) -> None:
        for name in (
            "VALID_837I", "VALID_837D", "VALID_835",
            "VALID_270", "VALID_271", "VALID_276", "VALID_277", "VALID_278",
        ):
            report = validate_document(getattr(F, name))
            self.assertTrue(
                report.is_valid,
                f"{name} should be clean: {[i.message for i in report.errors]}",
            )


class Guide837InstitutionalTests(unittest.TestCase):
    def test_clean_institutional_claim(self) -> None:
        report = validate_document(F.VALID_837I)
        self.assertEqual(report.transaction_set, "837")
        self.assertEqual(report.claim_count, 1)
        self.assertEqual(report.claims[0].type_of_bill, "0111")

    def test_facility_qualifier_must_be_a(self) -> None:
        broken = F.VALID_837I.replace("0111:A:1", "0111:B:1")
        report = validate_document(broken)
        self.assertIn("GUIDE.CLM05.QUALIFIER", _error_codes(report))

    def test_type_of_bill_must_be_numeric(self) -> None:
        broken = F.VALID_837I.replace("0111:A:1", "ABC:A:1")
        report = validate_document(broken)
        self.assertIn("GUIDE.CLM05.TYPE_OF_BILL", _error_codes(report))

    def test_revenue_code_required_on_service_line(self) -> None:
        broken = F.VALID_837I.replace("SV2*0120*HC:99231", "SV2**HC:99231")
        report = validate_document(broken)
        self.assertIn("REQ.SV201.REVENUE", _error_codes(report))

    def test_revenue_code_captured_in_projection(self) -> None:
        report = validate_document(F.VALID_837I)
        revenue_codes = {ln.revenue_code for ln in report.claims[0].service_lines}
        self.assertEqual(revenue_codes, {"0120", "0250"})


class Guide837DentalTests(unittest.TestCase):
    def test_clean_dental_claim(self) -> None:
        report = validate_document(F.VALID_837D)
        self.assertTrue(report.is_valid)
        self.assertEqual(report.claims[0].place_of_service, "22")

    def test_dental_d_codes_accepted(self) -> None:
        report = validate_document(F.VALID_837D)
        procs = {ln.procedure_code for ln in report.claims[0].service_lines}
        self.assertEqual(procs, {"D0120", "D1110"})

    def test_dental_facility_qualifier_must_be_b(self) -> None:
        broken = F.VALID_837D.replace("22:B:1", "22:A:1")
        report = validate_document(broken)
        self.assertIn("GUIDE.CLM05.QUALIFIER", _error_codes(report))


class Guide835Tests(unittest.TestCase):
    def test_clean_remittance_balances(self) -> None:
        report = validate_document(F.VALID_835)
        self.assertTrue(report.is_valid, [i.message for i in report.errors])

    def test_claim_imbalance_detected(self) -> None:
        broken = F.VALID_835.replace(
            "CLP*CLAIM001*1*250.00*200.00", "CLP*CLAIM001*1*250.00*180.00"
        )
        report = validate_document(broken)
        self.assertIn("BAL.CLP.CLAIM", _error_codes(report))

    def test_transaction_imbalance_detected(self) -> None:
        broken = F.VALID_835.replace("BPR*I*200.00", "BPR*I*999.00")
        report = validate_document(broken)
        self.assertIn("BAL.BPR02.TRANSACTION", _error_codes(report))

    def test_service_imbalance_detected(self) -> None:
        broken = F.VALID_835.replace(
            "SVC*HC:99213*250.00*200.00**1", "SVC*HC:99213*250.00*100.00**1"
        )
        report = validate_document(broken)
        self.assertIn("BAL.SVC.SERVICE", _error_codes(report))

    def test_missing_bpr(self) -> None:
        broken = F.VALID_835.replace(
            "BPR*I*200.00*C*ACH*CTX*01*999999999*DA*123456789*1512345678*"
            "*01*999988888*DA*987654321*20260515~",
            "",
        )
        report = validate_document(broken)
        self.assertIn("REQ.BPR.MISSING", _error_codes(report))

    def test_invalid_payment_method(self) -> None:
        broken = F.VALID_835.replace("BPR*I*200.00*C*ACH", "BPR*I*200.00*C*XYZ")
        report = validate_document(broken)
        self.assertIn("CODE.BPR04.PAYMENT_METHOD", _error_codes(report))

    def test_invalid_adjustment_group_code(self) -> None:
        broken = F.VALID_835.replace("CAS*CO*45*25.00", "CAS*ZZ*45*25.00")
        report = validate_document(broken)
        self.assertIn("CODE.CAS01.GROUP", _error_codes(report))


class EligibilityTests(unittest.TestCase):
    def test_270_requires_eq(self) -> None:
        broken = F.VALID_270.replace("EQ*30~", "")
        report = validate_document(broken)
        self.assertIn("REQ.EQ.MISSING", _error_codes(report))

    def test_271_requires_eb(self) -> None:
        broken = F.VALID_271.replace("EB*1*IND*30**29~", "")
        report = validate_document(broken)
        self.assertIn("REQ.EB.MISSING", _error_codes(report))

    def test_eligibility_requires_payer(self) -> None:
        broken = F.VALID_270.replace(
            "NM1*PR*2*PRIMARY PAYER*****PI*PAYER01~", ""
        )
        report = validate_document(broken)
        self.assertIn("REQ.NM1PR.MISSING", _error_codes(report))


class ClaimStatusTests(unittest.TestCase):
    def test_276_requires_trn(self) -> None:
        broken = F.VALID_276.replace("TRN*1*TRACE001~", "")
        report = validate_document(broken)
        self.assertIn("REQ.TRN.MISSING", _error_codes(report))

    def test_277_requires_stc(self) -> None:
        broken = F.VALID_277.replace("STC*A2:20*20260515*WQ*150~", "")
        report = validate_document(broken)
        self.assertIn("REQ.STC.MISSING", _error_codes(report))

    def test_277_status_category_code_checked(self) -> None:
        broken = F.VALID_277.replace("STC*A2:20*", "STC*ZZ:20*")
        report = validate_document(broken)
        self.assertIn("CODE.STC01.CATEGORY", {i.code for i in report.issues})


class Review278Tests(unittest.TestCase):
    def test_278_requires_um(self) -> None:
        broken = F.VALID_278.replace("UM*HS*I*1~", "")
        report = validate_document(broken)
        self.assertIn("REQ.UM.MISSING", _error_codes(report))


class VariantSelectionTests(unittest.TestCase):
    def test_837_version_selects_institutional_variant(self) -> None:
        # An 837 declaring X223 but using the professional facility qualifier 'B'
        # must be rejected by the institutional guide.
        broken = F.VALID_837I.replace("0111:A:1", "0111:B:1")
        report = validate_document(broken)
        qualifier_issue = next(
            i for i in report.errors if i.code == "GUIDE.CLM05.QUALIFIER"
        )
        self.assertEqual(qualifier_issue.expected, "A")

    def test_837_version_selects_dental_variant(self) -> None:
        report = validate_document(F.VALID_837D)
        self.assertIn("005010X224", report.implementation_version)


class MalformedIsaRecoveryTests(unittest.TestCase):
    def test_short_isa_padding_is_recovered(self) -> None:
        # Drop one pad character from the receiver field -> 105-char ISA.
        broken = F.VALID_276.replace("*ZZ*RECEIVER       *", "*ZZ*RECEIVER      *", 1)
        report = validate_document(broken)
        self.assertEqual(report.transaction_set, "276")
        self.assertIn("INT.ISA.LENGTH", {i.code for i in report.issues})


if __name__ == "__main__":
    unittest.main()
