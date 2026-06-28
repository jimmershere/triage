"""Tests for the flat-file rejection report (Workstream 7)."""
import csv
import io
import json
import unittest

from validation import validate_document
from validation._fixtures import VALID_837P, with_unbalanced_claim
from validation.acks import generate_999
from validation.engine import validate_parsed
from validation.parser import parse
from reporting import (
    build_report,
    build_report_from_999,
    position_map_from_flatfile,
    position_map_from_x12,
)


class PositionMapTests(unittest.TestCase):
    def test_position_map_from_x12_uses_claim_ordinal(self) -> None:
        pos = position_map_from_x12(VALID_837P)
        self.assertIn("CLAIM001", pos)
        self.assertEqual(pos["CLAIM001"].position, 1)

    def test_position_map_from_flatfile_defaults_ordinal(self) -> None:
        pos = position_map_from_flatfile(
            [
                {"claim_id": "CLAIM001", "patient_control_number": "PCN1"},
                {"claim_id": "CLAIM002", "position": 7},
            ]
        )
        self.assertEqual(pos["CLAIM001"].position, 1)
        self.assertEqual(pos["CLAIM002"].position, 7)


class BuildFromReportTests(unittest.TestCase):
    def test_clean_claim_passes(self) -> None:
        pos = position_map_from_x12(VALID_837P)
        report = validate_document(VALID_837P)
        rejection = build_report(pos, report, source="batch.txt")
        self.assertEqual(rejection.passed_count, 1)
        self.assertEqual(rejection.failed_count, 0)

    def test_failed_claim_reports_position_and_reason(self) -> None:
        bad = with_unbalanced_claim()
        pos = position_map_from_x12(bad)
        report = validate_document(bad)
        rejection = build_report(pos, report, source="batch.txt")
        self.assertEqual(rejection.failed_count, 1)
        row = rejection.rows[0]
        self.assertEqual(row.flat_file_position, 1)
        self.assertFalse(row.passed)
        self.assertTrue(row.errors)
        self.assertTrue(any(e.error_code == "BAL.CLM02.LINE_SUM" for e in row.errors))

    def test_json_is_position_keyed(self) -> None:
        bad = with_unbalanced_claim()
        pos = position_map_from_x12(bad)
        report = validate_document(bad)
        payload = json.loads(build_report(pos, report).to_json())
        self.assertIn("claims", payload)
        self.assertIn("1", payload["claims"])
        self.assertEqual(payload["claims"]["1"]["claim_id"], "CLAIM001")
        self.assertEqual(payload["failed"], 1)

    def test_csv_has_one_row_per_error(self) -> None:
        bad = with_unbalanced_claim()
        pos = position_map_from_x12(bad)
        report = validate_document(bad)
        text = build_report(pos, report).to_csv()
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertTrue(rows)
        self.assertEqual(rows[0]["claim_id"], "CLAIM001")
        self.assertEqual(rows[0]["status"], "fail")


class BuildFrom999Tests(unittest.TestCase):
    def test_999_join_back_to_flat_file_position(self) -> None:
        bad = with_unbalanced_claim()
        doc = parse(bad)
        report = validate_parsed(doc)
        ack = generate_999(doc, report)
        # The 999 carries CTX*CLM01:CLAIM001 after the IK3 for the failed claim.
        self.assertIn("CTX", ack)
        pos = position_map_from_x12(bad)
        rejection = build_report_from_999(ack, pos)
        failed = [r for r in rejection.rows if not r.passed]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].claim_id, "CLAIM001")
        self.assertEqual(failed[0].flat_file_position, 1)


if __name__ == "__main__":
    unittest.main()
