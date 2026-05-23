"""Tests for the TurboHEDI Complexity-Aware Routing MVP.

Covers:
- FileProfile extraction from real X12 samples
- Complexity scoring with expected tier assignments
- Hard gate escalation behavior
- End-to-end profile → score → route pipeline
"""
import sys
import unittest
from pathlib import Path

# Ensure worker_py is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from file_profiler import FileProfile, profile_file, profile_x12, enrich_with_harness
from complexity_scorer import ComplexityScore, score_file, DEFAULT_WEIGHTS
from routing_engine import RoutingDecision, route_file, GatePolicy


# Minimal valid 837P X12 content for testing
MINIMAL_837 = (
    "ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       "
    "*200101*1253*^*00501*000000001*0*P*:~"
    "GS*HC*SENDER*RECEIVER*20200101*1253*1*X*005010X222A1~"
    "ST*837*0001*005010X222A1~"
    "BHT*0019*00*12345*20200101*1253*31~"
    "NM1*41*2*SUBMITTER CO*****46*123456789~"
    "NM1*40*2*RECEIVER CO*****46*987654321~"
    "HL*1**20*1~"
    "NM1*85*2*BILLING PROVIDER*****XX*1234567890~"
    "REF*EI*123456789~"
    "NM1*IL*1*DOE*JOHN****MI*MEMBER001~"
    "CLM*CLAIM001*100***11:B:1~"
    "HI*ABK:J0690~"
    "LX*1~"
    "SV1*HC:99213*75*UN*1***1~"
    "SE*14*0001~"
    "GE*1*1~"
    "IEA*1*000000001~"
)


class TestFileProfiler(unittest.TestCase):
    """Test deterministic file profiling."""

    def test_profile_minimal_837(self):
        profile = profile_x12(MINIMAL_837, file_id="test-001")
        self.assertEqual(profile.file_id, "test-001")
        self.assertEqual(profile.document_standard, "X12")
        self.assertIn("837", profile.transaction_sets)
        self.assertEqual(profile.transaction_count, 1)
        self.assertFalse(profile.mixed_transaction_types)
        self.assertGreater(profile.segment_count, 10)
        self.assertGreater(profile.byte_size, 0)
        self.assertEqual(profile.schema_match_confidence, 1.0)
        self.assertEqual(profile.map_match_confidence, 1.0)

    def test_profile_auto_detect(self):
        profile = profile_file(MINIMAL_837)
        self.assertEqual(profile.document_standard, "X12")

    def test_profile_unknown_format(self):
        profile = profile_file("GARBAGE DATA HERE")
        self.assertEqual(profile.document_standard, "UNKNOWN")
        self.assertEqual(profile.schema_match_confidence, 0.0)

    def test_profile_edifact(self):
        edifact = "UNB+UNOC:3+SENDER+RECEIVER+200101:1253+1'UNH+1+ORDERS:D:96A:UN'UNT+2+1'UNZ+1+1'"
        profile = profile_file(edifact)
        self.assertEqual(profile.document_standard, "EDIFACT")
        self.assertIn("ORDERS", profile.transaction_sets)

    def test_custom_segment_detection(self):
        # Add a non-standard segment tag
        custom_x12 = MINIMAL_837.replace("SE*14*0001~", "ZZZ*CUSTOM*DATA~SE*14*0001~")
        profile = profile_x12(custom_x12)
        self.assertTrue(profile.custom_segment_present)
        self.assertTrue(any("custom_segments" in m for m in profile.loop_irregularity_markers))


class TestComplexityScorer(unittest.TestCase):
    """Test weighted scoring model."""

    def test_clean_file_scores_low(self):
        """A clean, standard 837 from a known partner should score Tier 0."""
        profile = FileProfile(
            file_id="clean-001",
            received_at="2026-05-15T06:00:00Z",
            document_standard="X12",
            document_version="005010X222A1",
            transaction_sets=["837"],
            segment_count=500,
            transaction_count=1,
            byte_size=15000,
            partner_reliability_tier="stable",
            schema_match_confidence=1.0,
            map_match_confidence=1.0,
        )
        score = score_file(profile)
        self.assertLessEqual(score.total_score, 24)
        self.assertEqual(score.tier_suggestion, 0)

    def test_messy_file_scores_higher(self):
        """A file with multiple anomaly types and low confidence should score higher."""
        profile = FileProfile(
            file_id="messy-001",
            received_at="2026-05-15T06:00:00Z",
            document_standard="X12",
            document_version="005010X222A1",
            transaction_sets=["837", "835"],
            segment_count=2000,
            transaction_count=15,
            byte_size=10000,
            mixed_transaction_types=True,
            loop_irregularity_markers=["unusual_hl_hierarchy:20,23", "custom_segments:ZZZ"],
            custom_segment_present=True,
            schema_match_confidence=0.6,
            map_match_confidence=0.5,
            validation_anomaly_count=8,
            validation_anomaly_types=["CONTROL", "ST01", "BILLING_PROVIDER"],
            partner_reliability_tier="new",
            historical_failure_rate=0.3,
        )
        score = score_file(profile)
        self.assertGreater(score.total_score, 24)
        self.assertGreaterEqual(score.tier_suggestion, 1)

    def test_all_factors_contribute(self):
        """Every factor should produce a non-negative weighted score."""
        profile = profile_x12(MINIMAL_837, file_id="factor-test")
        score = score_file(profile)
        self.assertEqual(len(score.factors), 15)  # 4 + 4 + 4 + 3
        for f in score.factors:
            self.assertGreaterEqual(f.weighted_score, 0.0)
            self.assertGreaterEqual(f.raw_severity, 0.0)
            self.assertLessEqual(f.raw_severity, 1.0)

    def test_confidence_reflects_data_availability(self):
        """Confidence should be higher when real data is available."""
        known_profile = FileProfile(
            file_id="known",
            received_at="2026-05-15T06:00:00Z",
            partner_reliability_tier="stable",
            historical_failure_rate=0.05,
            retry_count=1,
            business_criticality_class="high",
            sla_class="urgent",
        )
        unknown_profile = FileProfile(
            file_id="unknown",
            received_at="2026-05-15T06:00:00Z",
        )
        known_score = score_file(known_profile)
        unknown_score = score_file(unknown_profile)
        self.assertGreater(known_score.confidence, unknown_score.confidence)


class TestRoutingEngine(unittest.TestCase):
    """Test tier selection and hard gate escalation."""

    def test_clean_file_routes_tier0(self):
        profile = FileProfile(
            file_id="route-clean",
            received_at="2026-05-15T06:00:00Z",
            partner_reliability_tier="stable",
            schema_match_confidence=1.0,
            map_match_confidence=1.0,
        )
        score = score_file(profile)
        decision = route_file(profile, score)
        self.assertEqual(decision.tier, 0)
        self.assertEqual(decision.tier_label, "deterministic_fast_path")
        self.assertEqual(decision.gate_triggers, [])

    def test_low_confidence_gate_escalates(self):
        """Low map confidence should trigger hard gate to at least Tier 1."""
        profile = FileProfile(
            file_id="route-lowconf",
            received_at="2026-05-15T06:00:00Z",
            map_match_confidence=0.3,
            schema_match_confidence=0.4,
        )
        score = score_file(profile)
        decision = route_file(profile, score)
        self.assertGreaterEqual(decision.tier, 1)
        self.assertTrue(len(decision.gate_triggers) > 0)

    def test_compliance_with_anomalies_routes_tier3(self):
        """Compliance materiality + anomalies must route to Tier 3."""
        profile = FileProfile(
            file_id="route-compliance",
            received_at="2026-05-15T06:00:00Z",
            compliance_materiality_flag=True,
            validation_anomaly_count=3,
            validation_anomaly_types=["CONTROL", "ST01"],
        )
        score = score_file(profile)
        decision = route_file(profile, score)
        self.assertEqual(decision.tier, 3)
        self.assertIn("compliance_with_anomalies", decision.gate_triggers)

    def test_new_partner_gate(self):
        """New partner should trigger at least Tier 1."""
        profile = FileProfile(
            file_id="route-newpartner",
            received_at="2026-05-15T06:00:00Z",
            partner_reliability_tier="new",
        )
        score = score_file(profile)
        decision = route_file(profile, score)
        self.assertGreaterEqual(decision.tier, 1)
        self.assertIn("new_partner", decision.gate_triggers)

    def test_multi_anomaly_classes_gate(self):
        """More than 2 unique anomaly types should escalate to Tier 2+."""
        profile = FileProfile(
            file_id="route-multianom",
            received_at="2026-05-15T06:00:00Z",
            validation_anomaly_count=5,
            validation_anomaly_types=["CONTROL", "ST01", "BILLING_PROVIDER"],
        )
        score = score_file(profile)
        decision = route_file(profile, score)
        self.assertGreaterEqual(decision.tier, 2)

    def test_decision_has_traceability(self):
        """Every routing decision must have an ID, timestamp, and policy version."""
        profile = profile_x12(MINIMAL_837, file_id="trace-test")
        score = score_file(profile)
        decision = route_file(profile, score)
        self.assertTrue(decision.routing_decision_id)
        self.assertTrue(decision.decided_at)
        self.assertEqual(decision.policy_version, "1.0.0")
        self.assertGreaterEqual(decision.decision_duration_ms, 0)


class TestEndToEnd(unittest.TestCase):
    """End-to-end profile → score → route using real sample if available."""

    def test_minimal_837_pipeline(self):
        """Full pipeline with minimal 837."""
        profile = profile_file(MINIMAL_837, file_id="e2e-001")
        score = score_file(profile)
        decision = route_file(profile, score)

        # Clean minimal file should be Tier 0
        self.assertEqual(decision.tier, 0)
        self.assertLessEqual(score.total_score, 24)
        self.assertGreater(score.confidence, 0)

        # Serialization round-trip
        profile_dict = profile.to_dict()
        self.assertIn("segment_count", profile_dict)
        score_dict = score.to_dict()
        self.assertIn("total_score", score_dict)
        self.assertIn("factors", score_dict)
        decision_dict = decision.to_dict()
        self.assertIn("tier", decision_dict)

    def test_large_sample_if_available(self):
        """Profile the bundled 3,200-claim sample if it exists."""
        sample = Path(__file__).resolve().parent.parent / "samples" / "x12_837_large_valid.x12"
        if not sample.exists():
            self.skipTest(f"Large sample not found at {sample}")

        text = sample.read_text(encoding="utf-8", errors="ignore")
        profile = profile_file(text, file_id="e2e-large")
        self.assertGreater(profile.segment_count, 1000)
        self.assertGreater(profile.transaction_count, 0)

        score = score_file(profile)
        decision = route_file(profile, score)
        # Large valid file should still be routable — tier depends on content
        self.assertIn(decision.tier, [0, 1, 2, 3])
        self.assertGreater(len(score.factors), 0)


if __name__ == "__main__":
    unittest.main()
