"""Tests for :mod:`swarms.coordinator` and :mod:`swarms.aggregator`."""
from __future__ import annotations

import json
import unittest

from swarms import (
    EnginePool,
    MockLlmClient,
    ShardedPipelineResult,
    SwarmCoordinator,
    SwarmRunner,
)
from swarms.aggregator import (
    CompositeFhirBundle,
    merge_fhir_bundles,
    merge_scrub_reports,
    merge_validation_reports,
    min_confidence,
    worst_of_verdict,
)
from validation import validate_document


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


def _two_st_837() -> str:
    isa = (
        "ISA*00*          *00*          *ZZ*SENDERID      *ZZ*RECEIVERID    "
        "*230101*1253*^*00501*000000906*0*T*:~"
    )
    gs = "GS*HC*SENDER*RECEIVER*20230101*1253*1*X*005010X222A1~"
    st_template = (
        "ST*837*{ctrl}*005010X222A1~"
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
        "CLM*{clm_id}*{amount}***11:B:1*Y*A*Y*I~"
        "HI*ABK:K5789~"
        "SE*18*{ctrl}~"
    )
    st1 = st_template.format(ctrl="0001", clm_id="A001", amount="100.00")
    st2 = st_template.format(ctrl="0002", clm_id="B002", amount="300.00")
    return isa + gs + st1 + st2 + "GE*2*1~IEA*1*000000906~"


class _DeterministicCoordinator(SwarmCoordinator):
    """SwarmCoordinator with the engine pools forced to serial mode for tests."""

    def __init__(self, **kwargs) -> None:
        super().__init__(
            thread_pool=EnginePool(mode="serial", max_workers=1),
            process_pool=EnginePool(mode="serial", max_workers=1),
            **kwargs,
        )


class CoordinatorBasicsTests(unittest.TestCase):
    def test_returns_sharded_pipeline_result(self) -> None:
        coord = _DeterministicCoordinator()
        result = coord.run(_SAMPLE_837)
        self.assertIsInstance(result, ShardedPipelineResult)
        self.assertEqual(result.transaction_set, "837")
        self.assertEqual(result.shard_count, 1)
        self.assertEqual(result.fallback_reason, None)
        self.assertIsNotNone(result.scrubbing)
        # The composite validation view must carry our two claims.
        self.assertEqual(result.validation["claim_count"], 2)

    def test_two_st_interchange_runs_two_shards(self) -> None:
        coord = _DeterministicCoordinator()
        result = coord.run(_two_st_837(), parent_id="big")
        self.assertEqual(result.shard_count, 2)
        # Composite claim count is the sum across shards.
        self.assertEqual(result.validation["claim_count"], 2)
        self.assertEqual(
            [s.position for s in result.shards],
            [0, 1],
            msg="shard outcomes must come back in deterministic position order",
        )

    def test_generate_acks_emits_canonical_acks(self) -> None:
        coord = _DeterministicCoordinator()
        result = coord.run(_SAMPLE_837, generate_acks=True)
        self.assertIsNotNone(result.acknowledgments)
        self.assertIn("TA1", result.acknowledgments)
        self.assertIn("999", result.acknowledgments)
        self.assertIn("277CA", result.acknowledgments)
        # The ack content must reference the original ISA13 control number.
        self.assertIn("000000905", result.acknowledgments["TA1"])

    def test_falls_back_for_non_x12_input(self) -> None:
        coord = _DeterministicCoordinator()
        result = coord.run("UNB+UNOA:4+SENDER+RECEIVER+220101:1200+1'")
        self.assertEqual(result.shard_count, 1)
        # The fallback path runs the legacy pipeline.
        self.assertIsNotNone(result.fallback_reason)

    def test_to_dict_is_json_serializable(self) -> None:
        coord = _DeterministicCoordinator()
        result = coord.run(_SAMPLE_837)
        # Round-trips through JSON without raising — this is the contract for
        # persisting into the worker's audit trail.
        json.dumps(result.to_dict())

    def test_rejects_non_positive_claims_per_batch(self) -> None:
        with self.assertRaises(ValueError):
            SwarmCoordinator(
                thread_pool=EnginePool(mode="serial"),
                process_pool=EnginePool(mode="serial"),
                claims_per_batch=0,
            )


class CoordinatorParallelismTests(unittest.TestCase):
    """End-to-end sanity check that thread mode produces the same result."""

    def test_thread_pool_matches_serial_result(self) -> None:
        serial = _DeterministicCoordinator().run(_two_st_837())
        threaded = SwarmCoordinator(
            thread_pool=EnginePool(mode="thread", max_workers=4),
            process_pool=EnginePool(mode="thread", max_workers=2),
        ).run(_two_st_837())
        # Counts must match across execution modes; only ms-level timing
        # numbers differ between runs.
        self.assertEqual(serial.shard_count, threaded.shard_count)
        self.assertEqual(
            serial.validation["claim_count"], threaded.validation["claim_count"]
        )
        self.assertEqual(
            sorted(s.shard_id for s in serial.shards),
            sorted(s.shard_id for s in threaded.shards),
        )

    def test_records_parallel_worker_count(self) -> None:
        coord = SwarmCoordinator(
            thread_pool=EnginePool(mode="thread", max_workers=4),
            process_pool=EnginePool(mode="serial"),
        )
        result = coord.run(_two_st_837())
        self.assertGreaterEqual(result.parallel_workers, 1)


class CoordinatorSupervisorTests(unittest.TestCase):
    def test_supervisor_pass_invokes_swarm_runner(self) -> None:
        # Supply a deterministic supervisor LLM that always APPROVEs.
        verdict_json = json.dumps(
            {"verdict": "APPROVE", "confidence": 0.95, "summary": "ok", "issues": []}
        )

        def respond(prompt: str) -> str:
            if "Reply with ONLY a JSON object" in prompt:
                return verdict_json
            return "agent says ok"

        client = MockLlmClient(respond)
        runner = SwarmRunner(client, max_workers=2, max_retries=0)
        coord = SwarmCoordinator(
            thread_pool=EnginePool(mode="serial"),
            process_pool=EnginePool(mode="serial"),
            llm_client=client,
            swarm_runner=runner,
        )
        result = coord.run(_SAMPLE_837, run_supervisor=True)
        self.assertIsNotNone(result.supervisor)
        self.assertEqual(result.supervisor["verdict"], "APPROVE")
        self.assertGreater(result.supervisor["confidence"], 0.0)


# ---------------------------------------------------------------------------
# Aggregator unit tests
# ---------------------------------------------------------------------------


class AggregatorPureFunctionTests(unittest.TestCase):
    def test_worst_of_verdict_is_pessimistic(self) -> None:
        self.assertEqual(worst_of_verdict(["APPROVE", "APPROVE"]), "APPROVE")
        self.assertEqual(worst_of_verdict(["APPROVE", "FLAG"]), "FLAG")
        self.assertEqual(worst_of_verdict(["FLAG", "REJECT", "APPROVE"]), "REJECT")
        self.assertEqual(worst_of_verdict(["weird"]), "FLAG")
        self.assertEqual(worst_of_verdict([]), "APPROVE")

    def test_min_confidence_handles_empty(self) -> None:
        self.assertEqual(min_confidence([]), 0.0)
        self.assertEqual(min_confidence([0.7, 0.3, 0.9]), 0.3)

    def test_merge_fhir_bundles_flattens_entries(self) -> None:
        bundle_a = {"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "Claim", "id": "1"}}]}
        bundle_b = {"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "Claim", "id": "2"}}]}
        composite = merge_fhir_bundles([bundle_a, None, bundle_b])
        self.assertIsInstance(composite, CompositeFhirBundle)
        self.assertEqual(composite.bundle_type, "collection")
        self.assertEqual(len(composite.entry), 2)

    def test_merge_fhir_bundles_returns_none_when_empty(self) -> None:
        self.assertIsNone(merge_fhir_bundles([None, None]))

    def test_merge_validation_reports_concatenates_issues(self) -> None:
        a = validate_document(_SAMPLE_837)
        b = validate_document(_SAMPLE_837)
        view = merge_validation_reports([a, b])
        self.assertEqual(view.transaction_set, "837")
        # Two reports → at minimum 2x the issues / claims.
        self.assertEqual(view.claim_count, a.claim_count + b.claim_count)
        self.assertEqual(len(view.issues), len(a.issues) + len(b.issues))

    def test_merge_scrub_reports_handles_empty(self) -> None:
        view = merge_scrub_reports([])
        self.assertTrue(view.clean)
        self.assertEqual(view.finding_count, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
