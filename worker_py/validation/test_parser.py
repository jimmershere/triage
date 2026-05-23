"""Tests for the loop-aware X12 parser."""
import unittest

from validation._fixtures import VALID_837P
from validation.parser import detect_delimiters, parse


class DelimiterDetectionTests(unittest.TestCase):
    def test_detects_standard_delimiters(self) -> None:
        delim, issue = detect_delimiters(VALID_837P)
        self.assertEqual(delim.element, "*")
        self.assertEqual(delim.component, ":")
        self.assertEqual(delim.segment, "~")
        self.assertEqual(delim.repetition, "^")
        self.assertIsNone(issue)

    def test_missing_isa_falls_back_with_issue(self) -> None:
        delim, issue = detect_delimiters("GS*HC*A*B*20260101*1200*1*X*005010X222A1~")
        self.assertIsNotNone(issue)
        self.assertEqual(delim.element, "*")

    def test_non_standard_delimiters(self) -> None:
        # Re-delimit with a pipe element separator and apostrophe terminator.
        text = VALID_837P.replace("*", "|").replace("~", "'")
        delim, issue = detect_delimiters(text)
        self.assertEqual(delim.element, "|")
        self.assertEqual(delim.segment, "'")
        self.assertEqual(delim.component, ":")
        self.assertIsNone(issue)

    def test_non_standard_delimiters_parse_round_trip(self) -> None:
        text = VALID_837P.replace("*", "|").replace("~", "'")
        doc = parse(text)
        self.assertEqual(doc.parse_issues, [])
        self.assertEqual(doc.primary_transaction_set, "837")


class EnvelopeTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = parse(VALID_837P)

    def test_single_interchange_group_transaction(self) -> None:
        self.assertEqual(len(self.doc.interchanges), 1)
        ic = self.doc.interchanges[0]
        self.assertEqual(len(ic.groups), 1)
        self.assertEqual(len(ic.groups[0].transactions), 1)

    def test_interchange_metadata(self) -> None:
        ic = self.doc.interchanges[0]
        self.assertEqual(ic.sender_id, "SUBMITTER")
        self.assertEqual(ic.receiver_id, "RECEIVER")
        self.assertEqual(ic.control_number, "000000001")
        self.assertEqual(ic.usage_indicator, "P")

    def test_transaction_metadata(self) -> None:
        txn = self.doc.transactions[0]
        self.assertEqual(txn.set_code, "837")
        self.assertEqual(txn.control_number, "0001")
        self.assertEqual(txn.implementation_version, "005010X222A1")

    def test_declared_counts(self) -> None:
        txn = self.doc.transactions[0]
        self.assertEqual(txn.declared_segment_count, 24)
        self.assertEqual(txn.actual_segment_count, 24)
        self.assertEqual(self.doc.interchanges[0].declared_group_count, 1)
        self.assertEqual(self.doc.interchanges[0].groups[0].declared_transaction_count, 1)

    def test_no_parse_issues_for_clean_document(self) -> None:
        self.assertEqual(self.doc.parse_issues, [])

    def test_primary_transaction_helpers(self) -> None:
        self.assertEqual(self.doc.primary_transaction_set, "837")
        self.assertEqual(self.doc.primary_version, "005010X222A1")


class SegmentAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.txn = parse(VALID_837P).transactions[0]

    def test_element_one_based_access(self) -> None:
        clm = self.txn.first("CLM")
        self.assertIsNotNone(clm)
        self.assertEqual(clm.elem(1), "CLAIM001")
        self.assertEqual(clm.elem(2), "150")
        self.assertEqual(clm.elem(99), "")  # out of range is empty, not an error

    def test_composite_component_access(self) -> None:
        clm = self.txn.first("CLM")
        self.assertEqual(clm.comp(5, 1), "11")  # place of service
        self.assertEqual(clm.comp(5, 2), "B")   # facility code qualifier
        self.assertEqual(clm.comp(5, 3), "1")   # claim frequency

    def test_segment_positions_are_sequential(self) -> None:
        positions = [s.position for s in self.txn.segments]
        self.assertEqual(positions, list(range(1, len(positions) + 1)))

    def test_find_all(self) -> None:
        self.assertEqual(len(self.txn.find_all("SV1")), 2)
        self.assertEqual(len(self.txn.find_all("LX")), 2)


class MalformedInputTests(unittest.TestCase):
    def test_empty_input(self) -> None:
        doc = parse("")
        self.assertTrue(any(i.code == "INT.EMPTY" for i in doc.parse_issues))

    def test_orphan_segment_outside_transaction(self) -> None:
        text = (
            "ISA*00*          *00*          *ZZ*A              *ZZ*B"
            "              *260101*1200*^*00501*000000001*0*P*:~"
            "GS*HC*A*B*20260101*1200*1*X*005010X222A1~"
            "NM1*85*2*ORPHAN~"
            "GE*0*1~IEA*1*000000001~"
        )
        doc = parse(text)
        self.assertTrue(any(i.code == "INT.SEG.ORPHAN" for i in doc.parse_issues))


if __name__ == "__main__":
    unittest.main()
