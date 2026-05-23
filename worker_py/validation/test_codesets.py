"""Tests for external code-set loading and format validators."""
import unittest

from validation.codesets import (
    get_codeset,
    is_valid_hcpcs_cpt,
    is_valid_icd10_cm,
    is_valid_npi,
    is_valid_revenue_code,
    is_valid_taxonomy,
)
from validation.codesets.loader import available_codesets


class CodeSetLoadingTests(unittest.TestCase):
    def test_place_of_service_is_complete(self) -> None:
        pos = get_codeset("place_of_service")
        self.assertTrue(pos.complete)
        self.assertTrue(pos.is_valid("11"))
        self.assertEqual(pos.describe("11"), "Office")
        self.assertFalse(pos.is_valid("00"))

    def test_subset_codeset_marked_incomplete(self) -> None:
        carc = get_codeset("carc")
        self.assertFalse(carc.complete)
        self.assertTrue(carc.is_valid("1"))

    def test_missing_codeset_degrades_gracefully(self) -> None:
        cs = get_codeset("does_not_exist")
        self.assertFalse(cs.complete)
        self.assertEqual(len(cs), 0)
        self.assertFalse(cs.is_valid("anything"))

    def test_codesets_are_cached(self) -> None:
        self.assertIs(get_codeset("gender"), get_codeset("gender"))

    def test_expected_codesets_are_bundled(self) -> None:
        names = set(available_codesets())
        for required in (
            "place_of_service",
            "claim_frequency",
            "filing_indicator",
            "gender",
            "carc",
        ):
            self.assertIn(required, names)


class NpiValidationTests(unittest.TestCase):
    def test_valid_npi_passes_luhn(self) -> None:
        self.assertTrue(is_valid_npi("1234567893"))

    def test_invalid_check_digit_fails(self) -> None:
        self.assertFalse(is_valid_npi("1234567890"))

    def test_wrong_length_fails(self) -> None:
        self.assertFalse(is_valid_npi("123456789"))
        self.assertFalse(is_valid_npi("12345678901"))

    def test_non_numeric_fails(self) -> None:
        self.assertFalse(is_valid_npi("12345A7893"))
        self.assertFalse(is_valid_npi(None))


class Icd10FormatTests(unittest.TestCase):
    def test_valid_codes(self) -> None:
        for code in ("E119", "I10", "E11.9", "S72.001A", "Z0000"):
            self.assertTrue(is_valid_icd10_cm(code), code)

    def test_invalid_codes(self) -> None:
        for code in ("", "123", "99213", "UU99", None):
            self.assertFalse(is_valid_icd10_cm(code), code)


class ProcedureFormatTests(unittest.TestCase):
    def test_valid_cpt_and_hcpcs(self) -> None:
        for code in ("99213", "85025", "0001F", "0002T", "J1885", "A0428"):
            self.assertTrue(is_valid_hcpcs_cpt(code), code)

    def test_invalid_procedure_codes(self) -> None:
        for code in ("", "9921", "ABCDE", "123456", None):
            self.assertFalse(is_valid_hcpcs_cpt(code), code)


class OtherFormatTests(unittest.TestCase):
    def test_taxonomy(self) -> None:
        self.assertTrue(is_valid_taxonomy("207Q00000X"))
        self.assertFalse(is_valid_taxonomy("207Q0000"))

    def test_revenue_code(self) -> None:
        self.assertTrue(is_valid_revenue_code("450"))
        self.assertTrue(is_valid_revenue_code("0450"))
        self.assertFalse(is_valid_revenue_code("45"))
        self.assertFalse(is_valid_revenue_code("ABCD"))


if __name__ == "__main__":
    unittest.main()
