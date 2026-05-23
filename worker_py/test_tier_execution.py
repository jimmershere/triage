"""Tests for the TurboHEDI tier execution layer."""
import json
import os
import sys
import unittest

# Ensure worker_py modules are importable when run directly.
sys.path.insert(0, os.path.dirname(__file__))

from file_profiler import profile_file
from complexity_scorer import score_file
from routing_engine import route_file
from tier_executor import execute_tier, TierResult
from tier1_assist import run_assisted_review

# --- Sample X12 837 for testing ---
SIMPLE_837 = (
    "ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       "
    "*210101*1200*^*00501*000000001*0*P*:~"
    "GS*HC*SENDER*RECEIVER*20210101*1200*1*X*005010X222A1~"
    "ST*837*0001*005010X222A1~"
    "BHT*0019*00*12345*20210101*1200*CH~"
    "NM1*41*2*BILLING PROVIDER*****46*123456789~"
    "HL*1**20*1~"
    "NM1*85*2*BILLING PROVIDER*****XX*1234567890~"
    "HL*2*1*22*1~"
    "SBR*P*18*******CI~"
    "NM1*IL*1*DOE*JOHN****MI*MEMBERID123~"
    "HL*3*2*23*0~"
    "PAT*19~"
    "CLM*CLAIM001*500***11:B:1*Y*A*Y*Y~"
    "DTP*431*D8*20210101~"
    "HI*ABK:J0690~"
    "NM1*82*1*RENDERING*PROVIDER****XX*9876543210~"
    "SV1*HC:99213*100*UN*1***1~"
    "DTP*472*D8*20210101~"
    "SE*18*0001~"
    "GE*1*1~"
    "IEA*1*000000001~"
)

# Complex file: mixed types, many claims, anomalies
COMPLEX_837 = SIMPLE_837.replace(
    "CLM*CLAIM001*500",
    "CLM*CLAIM001*500"
) + (
    "ST*837*0002*005010X222A1~"
    "BHT*0019*00*67890*20210101*1200*CH~"
    "CLM*CLAIM001*99999***11:B:1*Y*A*Y*Y~"  # duplicate ID, outlier amount
    "CLM*CLAIM002*50***11:B:1*Y*A*Y*Y~"
    "CLM*CLAIM003*75***11:B:1*Y*A*Y*Y~"
    "CLM*CLAIM004*60***11:B:1*Y*A*Y*Y~"
    "SE*6*0002~"
    "GE*2*1~"
)


class TierExecutionTests(unittest.TestCase):
    def test_tier0_passthrough(self) -> None:
        """Tier 0 returns completed with no extra processing."""
        profile = profile_file(SIMPLE_837)
        score = score_file(profile)
        decision = route_file(profile, score)

        result = execute_tier(0, profile, score, decision, SIMPLE_837)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.tier, 0)
        self.assertEqual(len(result.ai_analyses), 0)

    def test_tier1_detects_duplicates(self) -> None:
        """Tier 1 detects duplicate claim IDs."""
        profile = profile_file(COMPLEX_837)
        score = score_file(profile)
        decision = route_file(profile, score)

        result = execute_tier(1, profile, score, decision, COMPLEX_837)
        self.assertEqual(result.tier, 1)
        self.assertTrue(
            any("DUPLICATE_CLAIM_IDS" in f for f in result.flags),
            f"Expected duplicate flag, got: {result.flags}",
        )

    def test_tier1_produces_flags(self) -> None:
        """Tier 1 assisted review produces flags for the complex file."""
        profile = profile_file(COMPLEX_837)
        score = score_file(profile)
        decision = route_file(profile, score)

        result = execute_tier(1, profile, score, decision, COMPLEX_837)
        self.assertGreater(len(result.flags), 0)

    def test_tier1_control_structure(self) -> None:
        """Tier 1 assisted review completes for files with control issues."""
        profile = profile_file(COMPLEX_837)
        score = score_file(profile)
        decision = route_file(profile, score)

        result = run_assisted_review(profile, score, decision, COMPLEX_837)
        self.assertEqual(result.tier, 1)
        self.assertIn(result.status, ("completed", "needs_review"))

    def test_tier3_parks_file(self) -> None:
        """Tier 3 parks the file and blocks auto-processing."""
        profile = profile_file(SIMPLE_837)
        profile.compliance_materiality_flag = True
        profile.validation_anomaly_count = 5
        profile.validation_anomaly_types = ["ERR1", "ERR2", "ERR3"]

        score = score_file(profile)
        decision = route_file(profile, score)

        result = execute_tier(3, profile, score, decision, SIMPLE_837)
        self.assertEqual(result.status, "parked")
        self.assertEqual(result.tier, 3)
        self.assertIn("HUMAN_REVIEW_REQUIRED", result.flags)
        self.assertIn("AUTO_PROCESSING_BLOCKED", result.flags)

    def test_tier2_without_ollama(self) -> None:
        """Tier 2 gracefully handles Ollama being unavailable."""
        profile = profile_file(SIMPLE_837)
        score = score_file(profile)
        decision = route_file(profile, score)

        result = execute_tier(
            2, profile, score, decision, SIMPLE_837,
            ollama_url="http://127.0.0.1:9",
            ollama_model="nonexistent",
        )
        self.assertEqual(result.tier, 2)
        self.assertEqual(result.swarm_task_count, 4)
        self.assertIn(result.supervisor_verdict, ("FLAG", None))

    def test_tier_result_serialization(self) -> None:
        """TierResult.to_dict produces valid JSON."""
        result = TierResult(
            tier=2,
            status="needs_review",
            processing_ms=1234.56,
            ai_analyses=[{"task": "test", "response": "ok"}],
            flags=["FLAG1", "FLAG2"],
            recommendations=["Do this"],
            swarm_task_count=4,
            supervisor_verdict="FLAG",
        )
        d = result.to_dict()
        serialized = json.dumps(d)
        self.assertIn("FLAG1", serialized)
        self.assertEqual(d["swarm_task_count"], 4)

    def test_full_pipeline_simple(self) -> None:
        """Full profile -> score -> route -> execute pipeline for a simple file."""
        profile = profile_file(SIMPLE_837)
        score = score_file(profile)
        decision = route_file(profile, score)

        self.assertEqual(decision.tier, 0, f"Expected Tier 0, got Tier {decision.tier}")

        result = execute_tier(decision.tier, profile, score, decision, SIMPLE_837)
        self.assertEqual(result.status, "completed")


if __name__ == "__main__":
    unittest.main()
