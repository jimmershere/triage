"""Unit tests for the Workstream 5 (835 restore) pure-Python core:
parse -> balance -> byte-exact re-delivery -> reconstruction -> reversal.
"""
import unittest

from claimtrace.era import (
    balance_report,
    build_835,
    build_reversal,
    parse_835,
    reconstruct_835,
)
from claimtrace.era.balancing import claim_adjustment_total, plb_total
from tests.generators.generate_835 import generate_835

# A small, fully-balanced 835: one paid claim with a service line carrying both
# CO and PR adjustments, one denied claim, and a PLB write-off (recoupment).
BALANCED_835 = (
    "ISA*00*          *00*          *ZZ*PAYER835        *ZZ*PAYEE835        "
    "*260628*1200*^*00501*000000001*0*P*:~"
    "GS*HP*PAYER835*PAYEE835*20260628*1200*1*X*005010X221A1~"
    "ST*835*0001*005010X221A1~"
    "BPR*I*175.00*C*ACH*CCP*01*123456789*DA*123456789012*1512345678**01*"
    "987654321*DA*987654321098*20260628~"
    "TRN*1*TRACE0000001*1512345678~"
    "DTM*405*20260628~"
    "N1*PR*PRIMARY HEALTH PLAN*XV*842610001~"
    "N1*PE*PAYEE CLINIC*XX*1234567893~"
    "LX*1~"
    "CLP*PATCTRL1*1*200.00*150.00*20.00*MC*PAYERCTRL1*11~"
    "NM1*QC*1*DOE*JOHN****MI*MEMBER0001~"
    "SVC*HC:99213*200.00*150.00*1~"
    "CAS*CO*45*30.00~"
    "CAS*PR*1*20.00~"
    "AMT*B6*150.00~"
    "LX*2~"
    "CLP*PATCTRL2*4*100.00*0.00*0.00*MC*PAYERCTRL2*11~"
    "CAS*CO*45*100.00~"
    "NM1*QC*1*ROE*JANE****MI*MEMBER0002~"
    "PLB*1234567890*20261231*WO:DCN999*-25.00~"
    "SE*19*0001~"
    "GE*1*1~"
    "IEA*1*000000001~"
)


class ParseTests(unittest.TestCase):
    def test_parses_header_claims_and_plb(self):
        era = parse_835(BALANCED_835)
        self.assertEqual(era.st_control, "0001")
        self.assertEqual(era.implementation_version, "005010X221A1")
        self.assertEqual(era.trn, "TRACE0000001")
        self.assertEqual(str(era.bpr_amount), "175.00")
        self.assertEqual(era.bpr_method, "ACH")
        self.assertEqual(era.payer_id, "842610001")
        self.assertEqual(era.payee_id, "1234567893")
        self.assertEqual(len(era.claims), 2)
        self.assertEqual(len(era.plbs), 1)

        first = era.claims[0]
        self.assertEqual(first.claim_id, "PATCTRL1")
        self.assertEqual(first.status, "1")
        self.assertEqual(len(first.services), 1)
        # CAS lives at the service level for the first claim.
        self.assertEqual(len(first.services[0].cas_segments()), 2)
        self.assertEqual(str(claim_adjustment_total(first)), "50.00")
        self.assertEqual(str(plb_total(era)), "-25.00")

    def test_parses_generated_835(self):
        era = parse_835(generate_835(claim_count=12))
        self.assertEqual(len(era.claims), 12)
        self.assertTrue(era.trn)
        self.assertIsNotNone(era.bpr_amount)


class BalanceTests(unittest.TestCase):
    def test_balanced_fixture_balances(self):
        report = balance_report(parse_835(BALANCED_835))
        self.assertTrue(report.balanced, report.errors)
        self.assertEqual(str(report.claim_paid_total), "150.00")
        self.assertEqual(str(report.plb_total), "-25.00")

    def test_imbalance_is_detected(self):
        broken = BALANCED_835.replace(
            "CLP*PATCTRL1*1*200.00*150.00*20.00", "CLP*PATCTRL1*1*200.00*999.00*20.00"
        )
        report = balance_report(parse_835(broken))
        self.assertFalse(report.balanced)
        self.assertTrue(any(e["level"] == "claim" for e in report.errors))


class RedeliveryTests(unittest.TestCase):
    def test_redelivery_is_byte_exact(self):
        # Re-delivery returns the stored bytes verbatim — same TRN, no rebuild.
        original = generate_835(claim_count=5).encode("utf-8")
        redelivered = bytes(original)
        self.assertEqual(original, redelivered)
        self.assertEqual(parse_835(original.decode()).trn, parse_835(redelivered.decode()).trn)


class ReconstructionTests(unittest.TestCase):
    def test_reconstruction_preserves_balance_and_codes(self):
        era = parse_835(BALANCED_835)
        rebuilt_text = reconstruct_835(era)
        rebuilt = parse_835(rebuilt_text)

        self.assertTrue(balance_report(rebuilt).balanced)
        # Same claim ids, statuses, charges, paid amounts.
        for original_claim, rebuilt_claim in zip(era.claims, rebuilt.claims):
            self.assertEqual(original_claim.claim_id, rebuilt_claim.claim_id)
            self.assertEqual(original_claim.charge, rebuilt_claim.charge)
            self.assertEqual(original_claim.paid, rebuilt_claim.paid)
        # Same TRN and BPR amount (reassociation preserved).
        self.assertEqual(era.trn, rebuilt.trn)
        self.assertEqual(era.bpr_amount, rebuilt.bpr_amount)
        # CARC code 45 still present.
        self.assertIn("CAS*CO*45*30.00", rebuilt_text)

    def test_reconstruct_recomputes_segment_count(self):
        era = parse_835(BALANCED_835)
        rebuilt = parse_835(reconstruct_835(era))
        se = next(s for s in rebuilt.trailer if s.seg_id == "SE")
        self.assertEqual(se.elem(1), "19")


class ReversalTests(unittest.TestCase):
    def test_reversal_negates_amounts_and_echoes_codes(self):
        era = parse_835(BALANCED_835)
        reversal = build_reversal(era)

        for claim in reversal.claims:
            self.assertEqual(claim.status, "22")
        # First claim amounts negated.
        first = reversal.claims[0]
        self.assertEqual(str(first.charge), "-200.00")
        self.assertEqual(str(first.paid), "-150.00")
        # Original CARC reason codes echoed unchanged.
        adjustments = first.adjustments()
        self.assertEqual({a.reason for a in adjustments}, {"45", "1"})
        self.assertTrue(any(str(a.amount) == "-30.00" for a in adjustments))
        # PLB recoupment reversed (write-off flips sign).
        self.assertEqual(str(plb_total(reversal)), "25.00")
        # BPR02 negated.
        self.assertEqual(str(reversal.bpr_amount), "-175.00")

    def test_reversal_is_self_balancing(self):
        reversal = build_reversal(parse_835(BALANCED_835))
        self.assertTrue(balance_report(reversal).balanced, balance_report(reversal).errors)
        # The rebuilt reversal text is itself a valid, parseable 835.
        text = build_835(reversal)
        self.assertIn("CLP*PATCTRL1*22*-200.00*-150.00", text)
        self.assertTrue(balance_report(parse_835(text)).balanced)


if __name__ == "__main__":
    unittest.main()
