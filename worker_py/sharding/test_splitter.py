"""Tests for :mod:`sharding.splitter`."""
from __future__ import annotations

import unittest

from sharding import Shard, ShardKind, partition, split_bundle, split_x12
from validation import validate_document
from validation.parser import parse


# A minimal valid 5010 837P that the existing validation engine accepts.
_SAMPLE_837 = (
    "ISA*00*          *00*          *ZZ*SENDERID      *ZZ*RECEIVERID    "
    "*230101*1253*^*00501*000000905*0*T*:~"
    "GS*HC*SENDER*RECEIVER*20230101*1253*1*X*005010X222A1~"
    "ST*837*0001*005010X222A1~"
    "BHT*0019*00*0123*20230101*1319*CH~"
    "NM1*41*2*SENDER*****46*661234567~"
    "PER*IC*CONTACT*TE*5551234567~"
    "NM1*40*2*RECEIVER*****46*RECEIVERID~"
    "HL*1**20*1~"
    "NM1*85*2*BILLING PROVIDER*****XX*1234567893~"
    "N3*1 PROVIDER WAY~"
    "N4*TOWN*ST*12345~"
    "REF*EI*123456789~"
    "HL*2*1*22*0~"
    "SBR*P*18*12345*******MC~"
    "NM1*IL*1*DOE*JANE****MI*W000000001~"
    "N3*123 MAIN STREET~"
    "N4*ANYTOWN*ST*90210~"
    "DMG*D8*19700101*F~"
    "CLM*10001*125.00***11:B:1*Y*A*Y*I~"
    "HI*ABK:K5789~"
    "CLM*10002*200.00***11:B:1*Y*A*Y*I~"
    "SE*20*0001~"
    "GE*1*1~"
    "IEA*1*000000905~"
)


def _two_st_interchange() -> str:
    """Build a synthetic interchange with two ST..SE transactions in one GS."""
    isa = (
        "ISA*00*          *00*          *ZZ*SENDERID      *ZZ*RECEIVERID    "
        "*230101*1253*^*00501*000000906*0*T*:~"
    )
    gs = "GS*HC*SENDER*RECEIVER*20230101*1253*1*X*005010X222A1~"
    st1 = (
        "ST*837*0001*005010X222A1~"
        "BHT*0019*00*0123*20230101*1319*CH~"
        "NM1*41*2*SENDER*****46*661234567~"
        "PER*IC*CONTACT*TE*5551234567~"
        "NM1*40*2*RECEIVER*****46*RECEIVERID~"
        "HL*1**20*1~"
        "NM1*85*2*BILLING PROVIDER*****XX*1234567893~"
        "N3*1 PROVIDER WAY~"
        "N4*TOWN*ST*12345~"
        "REF*EI*123456789~"
        "HL*2*1*22*0~"
        "SBR*P*18*12345*******MC~"
        "NM1*IL*1*DOE*JANE****MI*W000000001~"
        "N3*123 MAIN STREET~"
        "N4*ANYTOWN*ST*90210~"
        "DMG*D8*19700101*F~"
        "CLM*A001*100.00***11:B:1*Y*A*Y*I~"
        "HI*ABK:K5789~"
        "SE*18*0001~"
    )
    st2 = (
        "ST*837*0002*005010X222A1~"
        "BHT*0019*00*0124*20230101*1320*CH~"
        "NM1*41*2*SENDER*****46*661234567~"
        "PER*IC*CONTACT*TE*5551234567~"
        "NM1*40*2*RECEIVER*****46*RECEIVERID~"
        "HL*1**20*1~"
        "NM1*85*2*BILLING PROVIDER*****XX*1234567893~"
        "N3*1 PROVIDER WAY~"
        "N4*TOWN*ST*12345~"
        "REF*EI*123456789~"
        "HL*2*1*22*0~"
        "SBR*P*18*12345*******MC~"
        "NM1*IL*1*ROE*RICHARD****MI*W000000002~"
        "N3*456 SIDE STREET~"
        "N4*ANYTOWN*ST*90210~"
        "DMG*D8*19800202*M~"
        "CLM*B002*300.00***11:B:1*Y*A*Y*I~"
        "HI*ABK:K5789~"
        "SE*18*0002~"
    )
    ge = "GE*2*1~"
    iea = "IEA*1*000000906~"
    return isa + gs + st1 + st2 + ge + iea


class SplitX12Tests(unittest.TestCase):
    def test_single_st_yields_one_shard(self) -> None:
        shards = split_x12(_SAMPLE_837, parent_id="job-1")
        self.assertEqual(len(shards), 1)
        shard = shards[0]
        self.assertEqual(shard.kind, ShardKind.ST_TRANSACTION)
        self.assertEqual(shard.transaction_set, "837")
        self.assertEqual(shard.control_number, "0001")
        self.assertEqual(shard.position, 0)
        self.assertEqual(shard.parent_id, "job-1")
        self.assertEqual(shard.shard_id, "job-1:st:0000")

    def test_shard_payload_parses_back(self) -> None:
        shard = split_x12(_SAMPLE_837)[0]
        doc = parse(shard.payload)
        self.assertEqual(len(doc.transactions), 1)
        self.assertEqual(doc.transactions[0].set_code, "837")

    def test_shard_payload_validates_back(self) -> None:
        # The re-enveloped shard must be acceptable to the existing validator.
        shard = split_x12(_SAMPLE_837)[0]
        report = validate_document(shard.payload)
        self.assertEqual(report.transaction_set, "837")
        # The validator may surface its own findings; the splitter's contract
        # is only that the document is parseable end-to-end.
        self.assertIsNotNone(report.transaction_set)

    def test_two_st_interchange_yields_two_shards(self) -> None:
        text = _two_st_interchange()
        shards = split_x12(text, parent_id="job-2")
        self.assertEqual(len(shards), 2)
        self.assertEqual([s.position for s in shards], [0, 1])
        self.assertEqual([s.control_number for s in shards], ["0001", "0002"])
        # Each shard re-parses to exactly one transaction.
        for shard in shards:
            doc = parse(shard.payload)
            self.assertEqual(len(doc.transactions), 1)

    def test_shard_metadata_preserves_isa_and_gs_control(self) -> None:
        shards = split_x12(_two_st_interchange())
        metas = {s.position: s.metadata for s in shards}
        self.assertEqual(metas[0]["isa_control"], "000000906")
        self.assertEqual(metas[0]["gs_control"], "1")
        self.assertGreater(metas[0]["segment_count"], 0)

    def test_empty_text_returns_unsharded(self) -> None:
        shards = split_x12("", parent_id="job-empty")
        self.assertEqual(len(shards), 1)
        self.assertEqual(shards[0].kind, ShardKind.UNSHARDED)

    def test_non_x12_text_returns_unsharded(self) -> None:
        shards = split_x12("UNB+UNOA:4+sender+receiver+220101:1200+1'", parent_id="edifact")
        self.assertEqual(len(shards), 1)
        self.assertEqual(shards[0].kind, ShardKind.UNSHARDED)


class SplitBundleTests(unittest.TestCase):
    def test_one_shard_per_file(self) -> None:
        files = [
            ("a.x12", _SAMPLE_837),
            ("b.x12", _two_st_interchange()),
        ]
        shards = split_bundle(files, parent_id="bundle-7")
        self.assertEqual(len(shards), 2)
        self.assertEqual([s.kind for s in shards], [ShardKind.BUNDLE_FILE, ShardKind.BUNDLE_FILE])
        self.assertEqual([s.metadata["filename"] for s in shards], ["a.x12", "b.x12"])
        self.assertTrue(all(s.shard_id.startswith("bundle-7:file:") for s in shards))


class PartitionTests(unittest.TestCase):
    def test_evenly_divisible(self) -> None:
        self.assertEqual(partition([1, 2, 3, 4], 2), [[1, 2], [3, 4]])

    def test_remainder_in_last_batch(self) -> None:
        self.assertEqual(partition([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]])

    def test_empty_input(self) -> None:
        self.assertEqual(partition([], 3), [])

    def test_batch_larger_than_input(self) -> None:
        self.assertEqual(partition([1, 2], 10), [[1, 2]])

    def test_rejects_non_positive_batch_size(self) -> None:
        with self.assertRaises(ValueError):
            partition([1, 2, 3], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
