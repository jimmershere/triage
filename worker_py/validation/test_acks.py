"""Tests for TA1, 999 and 277CA acknowledgment generation."""
import unittest

from validation._fixtures import VALID_837P
from validation.acks import generate_277ca, generate_999, generate_ta1
from validation.engine import validate_parsed
from validation.parser import parse

BROKEN_837P = VALID_837P.replace("HI*ABK:E119~", "").replace("SE*24*0001", "SE*23*0001")


def _validated(text: str):
    doc = parse(text)
    report = validate_parsed(doc)
    return doc, report


class Ta1Tests(unittest.TestCase):
    def test_clean_interchange_is_accepted(self) -> None:
        doc, report = _validated(VALID_837P)
        ta1 = generate_ta1(doc, report)
        self.assertIn("*A*000~", ta1)

    def test_control_mismatch_is_rejected(self) -> None:
        broken = VALID_837P.replace("IEA*1*000000001", "IEA*1*000000002")
        doc, report = _validated(broken)
        ta1 = generate_ta1(doc, report)
        self.assertIn("*R*001~", ta1)

    def test_ta1_round_trips(self) -> None:
        doc, report = _validated(VALID_837P)
        reparsed = parse(generate_ta1(doc, report))
        self.assertEqual(len(reparsed.interchanges), 1)
        self.assertEqual(reparsed.parse_issues, [])


class Ack999Tests(unittest.TestCase):
    def test_clean_transaction_accepted(self) -> None:
        doc, report = _validated(VALID_837P)
        ack = generate_999(doc, report)
        txn = parse(ack).transactions[0]
        self.assertEqual(txn.set_code, "999")
        ak9 = txn.first("AK9")
        ik5 = txn.first("IK5")
        self.assertEqual(ak9.elem(1), "A")
        self.assertEqual(ik5.elem(1), "A")

    def test_broken_transaction_rejected_with_detail(self) -> None:
        doc, report = _validated(BROKEN_837P)
        ack = generate_999(doc, report)
        txn = parse(ack).transactions[0]
        self.assertEqual(txn.first("IK5").elem(1), "R")
        self.assertIn(txn.first("AK9").elem(1), ("R", "P"))
        self.assertTrue(txn.find_all("IK3"), "expected IK3 segment detail")

    def test_999_references_original_group(self) -> None:
        doc, report = _validated(VALID_837P)
        txn = parse(generate_999(doc, report)).transactions[0]
        ak1 = txn.first("AK1")
        self.assertEqual(ak1.elem(1), "HC")

    def test_999_round_trips(self) -> None:
        doc, report = _validated(VALID_837P)
        reparsed = parse(generate_999(doc, report))
        self.assertEqual(reparsed.parse_issues, [])


class Ack277caTests(unittest.TestCase):
    def test_accepted_claim_status(self) -> None:
        doc, report = _validated(VALID_837P)
        ack = generate_277ca(doc, report)
        txn = parse(ack).transactions[0]
        self.assertEqual(txn.set_code, "277")
        stc_categories = {s.comp(1, 1) for s in txn.find_all("STC")}
        self.assertIn("A2", stc_categories)

    def test_rejected_claim_status(self) -> None:
        doc, report = _validated(BROKEN_837P)
        ack = generate_277ca(doc, report)
        txn = parse(ack).transactions[0]
        stc_categories = {s.comp(1, 1) for s in txn.find_all("STC")}
        self.assertTrue(stc_categories & {"A6", "A7"})

    def test_277ca_round_trips(self) -> None:
        doc, report = _validated(VALID_837P)
        reparsed = parse(generate_277ca(doc, report))
        self.assertEqual(reparsed.parse_issues, [])
        self.assertEqual(len(reparsed.transactions), 1)


if __name__ == "__main__":
    unittest.main()
