"""Tests for the advisory mapping-suggestion module (Workstream 1).

These pin the advisory contract: structural candidate generation, transparent
logistic ranking, 999-driven validation-rule inference, and the
approve/version governance flow. The module must never expose an "apply" path.
"""

import unittest

from mapping_advisor import (
    GOVERNANCE_NOTE,
    InMemoryMappingRepository,
    LogisticScorer,
    MappingAdvisor,
    MappingFeatures,
)
from mapping_advisor.advisor import MappingAdvisor as AdvisorClass
from mapping_advisor.validation_signal import infer_validation_rules


X12_837 = (
    "ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       "
    "*060628*1600*^*00501*000000001*0*T*:~"
    "GS*HC*SENDER*RECEIVER*20060628*1600*1*X*005010X222A1~"
    "ST*837*0001*005010X222A1~"
    "BHT*0019*00*REF123*20060628*1600*CH~"
    "HL*1**20*1~"
    "NM1*85*2*PROVIDER~"
    "HL*2*1*22*0~"
    "CLM*ACCT001*500***11:B:1~"
    "SV1*HC:99213*500*UN*1~"
    "SE*9*0001~GE*1*1~IEA*1*000000001~"
)

FLAT_FILE = "acct,charge,proc\nACCT001,500,99213\n"

ACK_999 = (
    "ISA*00*          *00*          *ZZ*RECEIVER       *ZZ*SENDER         "
    "*060628*1600*^*00501*000000009*0*T*:~"
    "GS*FA*RECEIVER*SENDER*20060628*1600*9*X*005010X231A1~"
    "ST*999*0001*005010X231A1~"
    "AK1*HC*1*005010X222A1~"
    "AK2*837*0001*005010X222A1~"
    "IK3*CLM*8*2300*8~"
    "CTX*CLM01:ACCT001~"
    "IK4*1*1028*1~"
    "IK5*R*5~AK9*R*1*1*0~"
    "SE*10*0001~GE*1*9~IEA*1*000000009~"
)


class AdvisorSuggestionTests(unittest.TestCase):
    def test_exact_value_match_ranks_first(self) -> None:
        advisor = MappingAdvisor()
        result = advisor.suggest(
            X12_837,
            flat_file_text=FLAT_FILE,
            delimiter=",",
            header=True,
            partner_id="ACME",
        )
        self.assertEqual(result.transaction_set, "837")
        self.assertTrue(result.advisory)
        self.assertTrue(result.candidates, "expected at least one candidate")
        top = result.candidates[0]
        # CLM01 == ACCT001 == the "acct" flat field value -> strongest signal.
        self.assertEqual(top.source.segment_id, "CLM")
        self.assertEqual(top.target.name, "acct")
        self.assertGreater(top.confidence, 0.5)

    def test_no_apply_method_exposed(self) -> None:
        # Governance: advisory only. There must be no execution/apply path.
        self.assertFalse(hasattr(AdvisorClass, "apply"))
        self.assertFalse(hasattr(AdvisorClass, "mutate"))
        self.assertIn("ADVISORY ONLY", GOVERNANCE_NOTE)

    def test_min_confidence_filters(self) -> None:
        advisor = MappingAdvisor()
        result = advisor.suggest(
            X12_837,
            flat_file_text=FLAT_FILE,
            delimiter=",",
            header=True,
            min_confidence=0.99,
        )
        self.assertTrue(all(c.confidence >= 0.99 for c in result.candidates))


class ValidationSignalTests(unittest.TestCase):
    def test_infers_mandatory_element_rule_from_999(self) -> None:
        rules = infer_validation_rules(ACK_999)
        self.assertTrue(rules)
        match = [
            r
            for r in rules
            if r.segment_id == "CLM" and r.element_position == 1
        ]
        self.assertTrue(match, "expected an inferred rule for CLM element 1")
        rule = match[0]
        self.assertEqual(rule.loop_path, "2300")
        self.assertIn("mandatory", rule.requirement.lower())
        self.assertIn("ACCT001", rule.claim_refs)

    def test_empty_input_returns_no_rules(self) -> None:
        self.assertEqual(infer_validation_rules(""), [])


class ScorerTests(unittest.TestCase):
    def test_training_separates_positive_from_negative(self) -> None:
        pos = MappingFeatures(value_exact_match=1.0, historical_support=1.0)
        neg = MappingFeatures(name_similarity=0.1)
        scorer = LogisticScorer()
        scorer.train([pos, neg], [1, 0], epochs=200, lr=0.5)
        self.assertGreater(scorer.predict_proba(pos), scorer.predict_proba(neg))


class GovernanceQueueTests(unittest.TestCase):
    def _suggestion_set(self):
        advisor = MappingAdvisor()
        return advisor.suggest(
            X12_837,
            flat_file_text=FLAT_FILE,
            delimiter=",",
            header=True,
            ack_999_text=ACK_999,
            partner_id="ACME",
        )

    def test_enqueue_and_approve_versions(self) -> None:
        repo = InMemoryMappingRepository()
        ids = repo.enqueue_suggestions(self._suggestion_set())
        self.assertTrue(ids)
        pending = repo.list_suggestions(status="pending")
        self.assertEqual(len(pending), len(ids))

        first = pending[0]
        rule_v1 = repo.approve(first.id, approver="reviewer@triage")
        self.assertEqual(rule_v1.version, 1)
        self.assertTrue(rule_v1.active)
        self.assertEqual(
            repo.get_suggestion(first.id).status, "approved"
        )

        # Re-enqueue the same advisory output: same rule_key -> version 2,
        # and the prior version is deactivated (config-style versioning).
        ids2 = repo.enqueue_suggestions(self._suggestion_set())
        same_key = next(
            s for s in repo.list_suggestions(status="pending")
            if s.rule_key == first.rule_key
        )
        rule_v2 = repo.approve(same_key.id, approver="reviewer@triage")
        self.assertEqual(rule_v2.version, 2)
        active = [r for r in repo.list_rules(active_only=True) if r.rule_key == first.rule_key]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].version, 2)

    def test_reject_does_not_create_rule(self) -> None:
        repo = InMemoryMappingRepository()
        ids = repo.enqueue_suggestions(self._suggestion_set())
        repo.reject(ids[0], approver="reviewer@triage", reason="false positive")
        self.assertEqual(repo.get_suggestion(ids[0]).status, "rejected")
        self.assertEqual(repo.list_rules(active_only=False), [])


if __name__ == "__main__":
    unittest.main()
