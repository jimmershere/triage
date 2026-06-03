import datetime as dt
import tempfile
import unittest
from pathlib import Path

from claimtrace.common.canonical import canonical_json, line_items_signature
from claimtrace.common.hashing import hash_payload
from claimtrace.correlation.middleware import CorrelationError, require_outbound_context, with_correlation
from claimtrace.correlation.segments import extract_trace, stamp_trace
from claimtrace.correlation.transport import TraceContext, from_headers, read_envelope, to_headers, write_envelope
from claimtrace.identity.ids import ClaimKey, claim_hash, derive_bundle_id, derive_claim_id
from claimtrace.journal.store import ClaimEvent, InMemoryJournalStore
from claimtrace.journal.trace_api import get_trace, reconstruct
from claimtrace.lineage.projector import InMemoryGraphSink, LineageProjector
from claimtrace.lineage.queries import claims_for_payment, lineage_835_to_837
from claimtrace.merkle.tree import build_batch_mapping, build_merkle_tree, get_proof_path, merkle_root, verify_proof


def sample_claim_key(amount=12500):
    return ClaimKey(
        submitter_id="sub1",
        subscriber_id="member1",
        patient_dob=dt.date(1980, 1, 1),
        dos_start=dt.date(2026, 1, 15),
        charge_amount_cents=amount,
        line_items_signature=line_items_signature(
            [{"proc_code": "99213", "dos": dt.date(2026, 1, 15), "units": 1, "charge_amount_cents": amount}]
        ),
        payer_id="payer1",
    )


class IdentityTests(unittest.TestCase):
    def test_claim_id_is_deterministic_and_sensitive(self):
        key = sample_claim_key()
        self.assertEqual(derive_claim_id(key), derive_claim_id(sample_claim_key()))
        self.assertNotEqual(claim_hash(canonical_json(key.model_dump())), claim_hash(canonical_json(sample_claim_key(12501).model_dump())))

    def test_bundle_id_is_order_independent(self):
        self.assertEqual(derive_bundle_id(["a", "b"]), derive_bundle_id(["b", "a"]))

    def test_collision_sanity(self):
        ids = {
            derive_claim_id(
                ClaimKey(
                    submitter_id=f"sub{i}",
                    subscriber_id=f"member{i}",
                    patient_dob=dt.date(1980, 1, 1),
                    dos_start=dt.date(2026, 1, 15),
                    charge_amount_cents=10000 + i,
                    line_items_signature=f"sig{i}",
                    payer_id="payer",
                )
            )
            for i in range(1000)
        }
        self.assertEqual(len(ids), 1000)


class CorrelationTests(unittest.TestCase):
    def context(self):
        return TraceContext(
            claim_id="claim-a",
            claim_root_id="claim-root",
            bundle_id="bundle-a",
            trace_id="trace-a",
            prior_state_hash=None,
            new_state_hash="state-a",
            correlation_ids={"bundle_claim_ids": ["claim-a", "claim-b"]},
        )

    def test_stamp_extract_round_trip(self):
        stamped = stamp_trace("ST*837*0001~CLM*ABC*100~SE*3*0001~", self.context())
        extracted = extract_trace(stamped)
        self.assertEqual(extracted.claim_id, "claim-a")
        self.assertEqual(extracted.bundle_id, "bundle-a")
        self.assertEqual(extracted.correlation_ids["bundle_claim_ids"], ["claim-a", "claim-b"])

    def test_headers_and_envelope_round_trip(self):
        ctx = self.context()
        self.assertEqual(from_headers(to_headers(ctx)), ctx)
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/claim.x12"
            write_envelope(path, ctx)
            self.assertEqual(read_envelope(path), ctx)

    def test_fail_closed_outbound(self):
        with self.assertRaises(CorrelationError):
            require_outbound_context({})
        with with_correlation(ctx=self.context()):
            with self.assertRaises(CorrelationError):
                require_outbound_context({})
            require_outbound_context(to_headers(self.context()))


class JournalMerkleLineageTests(unittest.TestCase):
    def build_store(self):
        store = InMemoryJournalStore()
        root = "claim-root"
        child_a = "claim-a"
        child_b = "claim-b"
        bundle = derive_bundle_id([child_a, child_b])
        for event in [
            ClaimEvent(claim_id=root, new_state_hash="h1", payload_location="s3://ingest", operation_type="INGEST", service_name="ingest"),
            ClaimEvent(claim_id=child_a, new_state_hash="h2", payload_location="s3://split-a", operation_type="SPLIT", service_name="split", correlation_ids={"parent": root}),
            ClaimEvent(claim_id=child_b, new_state_hash="h3", payload_location="s3://split-b", operation_type="SPLIT", service_name="split", correlation_ids={"parent": root}),
            ClaimEvent(claim_id=child_a, bundle_id=bundle, new_state_hash="h4", payload_location="s3://bundle", operation_type="BUNDLE", service_name="bundle", correlation_ids={"children": [child_a, child_b]}),
            ClaimEvent(claim_id=child_a, bundle_id=bundle, new_state_hash="h5", payload_location="s3://835", operation_type="ADJUDICATE", service_name="payer", correlation_ids={"children": [child_a, child_b], "payment_id": "pay-1", "trn": "trn-1"}),
        ]:
            store.append_event(event)
        return store, bundle

    def test_journal_replay_and_late_reaggregation(self):
        store, bundle = self.build_store()
        trace = get_trace(store, claim_id="claim-a")
        self.assertEqual([event.operation_type for event in trace], ["INGEST", "SPLIT", "SPLIT", "BUNDLE", "ADJUDICATE"])
        self.assertEqual(len(get_trace(store, bundle_id=bundle)), 2)
        second_bundle = derive_bundle_id(["claim-a", "claim-c"])
        store.append_event(
            ClaimEvent(
                claim_id="claim-a",
                bundle_id=second_bundle,
                new_state_hash="h6",
                payload_location="s3://rebundle",
                operation_type="BUNDLE",
                service_name="bundle",
                correlation_ids={"children": ["claim-a", "claim-c"]},
            )
        )
        self.assertNotEqual(bundle, second_bundle)
        self.assertEqual(len(get_trace(store, bundle_id=bundle)), 2)
        self.assertTrue(reconstruct(store, "claim-a"))

    def test_merkle_proofs_and_tamper_detection(self):
        hashes = {"claim-a": "ha", "claim-b": "hb", "claim-c": "hc"}
        mapping = build_batch_mapping(hashes)
        ordered = sorted(hashes)
        tree = build_merkle_tree([hashes[key] for key in ordered])
        root = merkle_root(tree)
        self.assertEqual(mapping["batch_root_hash"], root)
        for index, claim_id in enumerate(ordered):
            proof = get_proof_path(tree, index)
            self.assertTrue(verify_proof(hashes[claim_id], proof, root))
            self.assertFalse(verify_proof(hashes[claim_id] + "-tampered", proof, root))

    def test_lineage_projection_is_idempotent(self):
        store, _ = self.build_store()
        sink = InMemoryGraphSink()
        projector = LineageProjector(sink)
        projector.project(store.events())
        nodes = set(sink.nodes)
        edges = set(sink.edges)
        projector.project(store.events())
        self.assertEqual(nodes, sink.nodes)
        self.assertEqual(edges, sink.edges)
        self.assertEqual(claims_for_payment("pay-1", sink), {"claim-a", "claim-b"})
        self.assertEqual(lineage_835_to_837("trn-1", sink), {"claim-a", "claim-b"})


class E2ETests(unittest.TestCase):
    def test_golden_pure_python_flow(self):
        key1 = sample_claim_key(10000)
        key2 = sample_claim_key(20000)
        claim1 = derive_claim_id(key1)
        claim2 = derive_claim_id(key2)
        child1 = claim1 + "-1"
        child2 = claim1 + "-2"
        bundle = derive_bundle_id([child1, child2, claim2])
        ctx = TraceContext(
            claim_id=claim1,
            claim_root_id=claim1,
            bundle_id=bundle,
            trace_id="trace-golden",
            prior_state_hash=None,
            new_state_hash=hash_payload(b"canonical"),
            correlation_ids={"bundle_claim_ids": [child1, child2, claim2]},
        )
        x12 = stamp_trace("ST*837*0001~CLM*A*100~SE*3*0001~", ctx)
        self.assertEqual(extract_trace(x12).bundle_id, bundle)

        store = InMemoryJournalStore()
        for event in [
            ClaimEvent(claim_id=claim1, new_state_hash="h1", payload_location="s3://837/a", operation_type="INGEST", service_name="ingest"),
            ClaimEvent(claim_id=claim2, new_state_hash="h2", payload_location="s3://837/b", operation_type="INGEST", service_name="ingest"),
            ClaimEvent(claim_id=child1, new_state_hash="h3", payload_location="s3://split/1", operation_type="SPLIT", service_name="split", correlation_ids={"parent": claim1}),
            ClaimEvent(claim_id=child2, new_state_hash="h4", payload_location="s3://split/2", operation_type="SPLIT", service_name="split", correlation_ids={"parent": claim1}),
            ClaimEvent(claim_id=child1, bundle_id=bundle, new_state_hash="h5", payload_location="s3://bundle", operation_type="BUNDLE", service_name="bundle", correlation_ids={"children": [child1, child2, claim2]}),
            ClaimEvent(claim_id=child1, bundle_id=bundle, new_state_hash="h6", payload_location="s3://835", operation_type="ADJUDICATE", service_name="payer", correlation_ids={"children": [child1, child2, claim2], "payment_id": "pay-golden", "trn": "trn-golden"}),
        ]:
            store.append_event(event)
        hashes = {child1: "h3", child2: "h4", claim2: "h2"}
        tree = build_merkle_tree([hashes[key] for key in sorted(hashes)])
        self.assertTrue(all(verify_proof(hashes[key], get_proof_path(tree, index), merkle_root(tree)) for index, key in enumerate(sorted(hashes))))
        sink = InMemoryGraphSink()
        LineageProjector(sink).project(store.events())
        self.assertEqual(claims_for_payment("pay-golden", sink), {child1, child2, claim2})

REPO_ROOT = Path(__file__).resolve().parents[1]


class ClaimtraceWebsiteTests(unittest.TestCase):
    def test_sidebar_exposes_claimtrace_menu(self):
        pages = [
            REPO_ROOT / "frontend_go" / "public" / "index.html",
            REPO_ROOT / "frontend_go" / "public" / "processed.html",
            REPO_ROOT / "frontend_go" / "public" / "admin.html",
            REPO_ROOT / "frontend_go" / "public" / "claimtrace.html",
        ]
        for page in pages:
            self.assertIn("/claimtrace.html", page.read_text(encoding="utf-8"))

    def test_api_route_helpers(self):
        try:
            from api import claimtrace_routes
        except Exception as exc:  # pragma: no cover - dependency guard for minimal envs
            self.skipTest(f"FastAPI route dependencies unavailable: {exc}")

        identity = claimtrace_routes.claim_identity(
            claimtrace_routes.IdentityRequest(
                submitter_id="sub",
                subscriber_id="member",
                patient_dob=dt.date(1980, 1, 1),
                dos_start=dt.date(2026, 1, 15),
                charge_amount_cents=10000,
                payer_id="payer",
                line_items=[
                    claimtrace_routes.LineItemRequest(
                        proc_code="99213",
                        dos=dt.date(2026, 1, 15),
                        units=1,
                        charge_amount_cents=10000,
                    )
                ],
            )
        )
        self.assertIn("claim_id", identity)
        stamped = claimtrace_routes.correlation_stamp(
            claimtrace_routes.StampRequest(
                x12_text="ST*837*0001~CLM*A*100~SE*3*0001~",
                trace_context=TraceContext(
                    claim_id=identity["claim_id"],
                    claim_root_id=identity["claim_id"],
                    trace_id="trace-ui",
                    new_state_hash="state-ui",
                    correlation_ids={},
                ),
            )
        )
        self.assertIn("REF*ZZ*", stamped["x12_text"])
        merkle = claimtrace_routes.merkle_batch(
            claimtrace_routes.MerkleRequest(claim_hashes={"a": "ha", "b": "hb"})
        )
        self.assertIn("batch_root_hash", merkle)


if __name__ == "__main__":
    unittest.main()
