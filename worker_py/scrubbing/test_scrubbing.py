"""Tests for the claim-scrubbing engine and its CMS payment edits."""
import unittest

from validation.model import ClaimProjection, ServiceLineProjection

from scrubbing import scrub_claims, scrub_document
from scrubbing.model import EditCategory, ScrubSeverity, age_on, parse_x12_date


def _line(
    proc: str,
    *,
    line_no: str = "1",
    units: str = "1",
    modifiers: list[str] | None = None,
    service_date: str = "20260510",
) -> ServiceLineProjection:
    return ServiceLineProjection(
        line_no=line_no,
        procedure_code=proc,
        modifiers=modifiers or [],
        units=units,
        service_date=service_date,
    )


def _claim(
    claim_id: str = "C1",
    *,
    lines: list[ServiceLineProjection] | None = None,
    diagnoses: list[str] | None = None,
    gender: str | None = None,
    dob: str | None = None,
    subscriber_id: str | None = None,
    billing_npi: str = "1234567893",
) -> ClaimProjection:
    return ClaimProjection(
        claim_id=claim_id,
        patient_control_number=claim_id,
        billing_provider_npi=billing_npi,
        subscriber_id=subscriber_id,
        patient_gender=gender,
        patient_dob=dob,
        diagnosis_codes=diagnoses or ["E119"],
        service_lines=lines or [_line("99213")],
    )


def _codes(report) -> set[str]:
    return {f.code for f in report.findings}


class ModelHelperTests(unittest.TestCase):
    def test_parse_x12_date(self) -> None:
        self.assertEqual(parse_x12_date("20260510").isoformat(), "2026-05-10")
        self.assertIsNone(parse_x12_date("badvalue"))
        self.assertIsNone(parse_x12_date("20261332"))

    def test_age_on(self) -> None:
        self.assertEqual(age_on("19800101", "20260510"), 46)
        self.assertEqual(age_on("19800601", "20260510"), 45)  # birthday not reached
        self.assertIsNone(age_on(None, "20260510"))


class NcciPtpTests(unittest.TestCase):
    def test_bundled_pair_flagged_for_review(self) -> None:
        claim = _claim(lines=[_line("27447", line_no="1"), _line("99214", line_no="2")])
        report = scrub_claims([claim])
        self.assertIn("PTP.BUNDLED", _codes(report))

    def test_override_modifier_bypasses_edit(self) -> None:
        claim = _claim(
            lines=[
                _line("27447", line_no="1"),
                _line("99214", line_no="2", modifiers=["25"]),
            ]
        )
        report = scrub_claims([claim])
        ptp = report.findings_for_category(EditCategory.NCCI_PTP)
        self.assertTrue(ptp)
        self.assertEqual(ptp[0].code, "PTP.BYPASSED")
        self.assertEqual(ptp[0].severity, ScrubSeverity.ADVISORY)

    def test_modifier_indicator_zero_is_hard_deny(self) -> None:
        claim = _claim(lines=[_line("80053", line_no="1"), _line("80048", line_no="2")])
        report = scrub_claims([claim])
        self.assertIn("PTP.NOT_PAYABLE", _codes(report))
        self.assertFalse(report.is_clean)


class MueTests(unittest.TestCase):
    def test_units_over_limit_flagged(self) -> None:
        claim = _claim(lines=[_line("99213", units="3")])
        report = scrub_claims([claim])
        self.assertIn("MUE.EXCEEDED", _codes(report))

    def test_units_within_limit_clean(self) -> None:
        claim = _claim(lines=[_line("99213", units="1")])
        report = scrub_claims([claim])
        self.assertNotIn("MUE.EXCEEDED", _codes(report))


class CoverageTests(unittest.TestCase):
    def test_statutory_exclusion_denied(self) -> None:
        claim = _claim(lines=[_line("0001U")])
        report = scrub_claims([claim])
        self.assertIn("COV.STATUTORY_EXCLUSION", _codes(report))

    def test_medical_necessity_unmet(self) -> None:
        claim = _claim(lines=[_line("93000")], diagnoses=["E119"])
        report = scrub_claims([claim])
        self.assertIn("COV.MEDICAL_NECESSITY", _codes(report))

    def test_medical_necessity_met(self) -> None:
        claim = _claim(lines=[_line("93000")], diagnoses=["I2510"])
        report = scrub_claims([claim])
        self.assertNotIn("COV.MEDICAL_NECESSITY", _codes(report))


class DemographicTests(unittest.TestCase):
    def test_gender_restricted_procedure(self) -> None:
        claim = _claim(lines=[_line("59400")], gender="M", dob="19800101")
        report = scrub_claims([claim])
        self.assertIn("DEMO.GENDER_PROCEDURE", _codes(report))

    def test_age_restricted_procedure(self) -> None:
        claim = _claim(lines=[_line("99391")], gender="F", dob="19800101")
        report = scrub_claims([claim])
        self.assertIn("DEMO.AGE_PROCEDURE", _codes(report))

    def test_gender_appropriate_clean(self) -> None:
        claim = _claim(lines=[_line("59400")], gender="F", dob="19900101")
        report = scrub_claims([claim])
        self.assertNotIn("DEMO.GENDER_PROCEDURE", _codes(report))


class DiagnosisSequencingTests(unittest.TestCase):
    def test_external_cause_cannot_be_principal(self) -> None:
        claim = _claim(diagnoses=["V8001XA", "S0000XA"])
        report = scrub_claims([claim])
        self.assertIn("DXSEQ.EXTERNAL_CAUSE_PRINCIPAL", _codes(report))

    def test_normal_principal_diagnosis_clean(self) -> None:
        claim = _claim(diagnoses=["E119"])
        report = scrub_claims([claim])
        self.assertNotIn("DXSEQ.EXTERNAL_CAUSE_PRINCIPAL", _codes(report))


class ModifierTests(unittest.TestCase):
    def test_malformed_modifier(self) -> None:
        claim = _claim(lines=[_line("99213", modifiers=["ZZZ"])])
        report = scrub_claims([claim])
        self.assertIn("MOD.MALFORMED", _codes(report))

    def test_unrecognized_modifier(self) -> None:
        claim = _claim(lines=[_line("99213", modifiers=["QJ"])])
        report = scrub_claims([claim])
        self.assertIn("MOD.UNRECOGNIZED", _codes(report))

    def test_known_modifier_clean(self) -> None:
        claim = _claim(lines=[_line("99213", modifiers=["25"])])
        report = scrub_claims([claim])
        self.assertNotIn("MOD.MALFORMED", _codes(report))
        self.assertNotIn("MOD.UNRECOGNIZED", _codes(report))


class DuplicateTests(unittest.TestCase):
    def test_cross_claim_duplicate(self) -> None:
        a = _claim("CA", subscriber_id="MEM1", lines=[_line("99213")])
        b = _claim("CB", subscriber_id="MEM1", lines=[_line("99213")])
        report = scrub_claims([a, b])
        self.assertIn("DUP.CROSS_CLAIM", _codes(report))

    def test_intra_claim_duplicate(self) -> None:
        claim = _claim(
            lines=[_line("99213", line_no="1"), _line("99213", line_no="2")]
        )
        report = scrub_claims([claim])
        self.assertIn("DUP.INTRA_CLAIM", _codes(report))

    def test_distinct_services_clean(self) -> None:
        claim = _claim(
            lines=[
                _line("99213", line_no="1"),
                _line("85025", line_no="2"),
            ]
        )
        report = scrub_claims([claim])
        self.assertNotIn("DUP.INTRA_CLAIM", _codes(report))


class EligibilityTests(unittest.TestCase):
    def test_lapsed_member_denied(self) -> None:
        claim = _claim(subscriber_id="MEMLAPSED")
        report = scrub_claims([claim])
        self.assertIn("ELIG.NOT_ELIGIBLE_ON_DOS", _codes(report))

    def test_unknown_member_advisory(self) -> None:
        claim = _claim(subscriber_id="NOTAMEMBER")
        report = scrub_claims([claim])
        self.assertIn("ELIG.NOT_VERIFIED", _codes(report))

    def test_active_member_clean(self) -> None:
        claim = _claim(subscriber_id="MEM123")
        report = scrub_claims([claim])
        self.assertNotIn("ELIG.NOT_ELIGIBLE_ON_DOS", _codes(report))

    def test_custom_roster_override(self) -> None:
        claim = _claim(subscriber_id="CUSTOM1")
        roster = {"CUSTOM1": [{"start": "20240101", "end": "20271231"}]}
        report = scrub_claims([claim], roster=roster)
        self.assertNotIn("ELIG.NOT_VERIFIED", _codes(report))


class EngineTests(unittest.TestCase):
    def test_clean_claim_produces_no_findings(self) -> None:
        from validation._fixtures import VALID_837P

        report = scrub_document(VALID_837P)
        self.assertEqual(report.claim_count, 1)
        self.assertTrue(report.is_clean, [f.message for f in report.findings])

    def test_all_edits_run(self) -> None:
        report = scrub_claims([_claim()])
        self.assertEqual(len(report.edits_run), 8)

    def test_report_serializes(self) -> None:
        import json

        report = scrub_claims([_claim(lines=[_line("27447"), _line("99214")])])
        json.dumps(report.to_dict())
        self.assertIn("ncci_ptp", report.category_summary())


if __name__ == "__main__":
    unittest.main()
