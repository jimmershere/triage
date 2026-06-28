"""Tests for the per-partner SNIP severity policy engine (Workstream 7)."""
import unittest
from datetime import date

from validation import validate_document
from validation._fixtures import VALID_837P, with_unbalanced_claim
from validation.model import SnipType
from validation.policy import (
    EDIG_PARITY_V1,
    PolicyMode,
    SnipPolicy,
    apply_policy,
    available_policies,
    named_policy,
    policy_from_rows,
)


class NamedPolicyTests(unittest.TestCase):
    def test_edig_parity_enforces_1_2_warns_middle_off_7(self) -> None:
        p = named_policy("edig-parity-v1")
        self.assertEqual(p.mode_for(SnipType.INTEGRITY), PolicyMode.ENFORCE_REJECT)
        self.assertEqual(p.mode_for(SnipType.REQUIREMENT), PolicyMode.ENFORCE_REJECT)
        self.assertEqual(p.mode_for(SnipType.BALANCING), PolicyMode.WARN)
        self.assertEqual(p.mode_for(SnipType.GUIDE_SPECIFIC), PolicyMode.OFF)

    def test_unknown_name_falls_back_to_edig_parity(self) -> None:
        self.assertIs(named_policy("does-not-exist"), EDIG_PARITY_V1)

    def test_available_policies_lists_builtins(self) -> None:
        names = available_policies()
        self.assertIn("edig-parity-v1", names)
        self.assertIn("strict-all", names)


class ApplyPolicyTests(unittest.TestCase):
    def test_strict_validation_rejects_unbalanced_claim(self) -> None:
        report = validate_document(with_unbalanced_claim())
        self.assertFalse(report.is_valid)

    def test_edig_parity_downgrades_balancing_to_warning(self) -> None:
        report = validate_document(with_unbalanced_claim())
        application = apply_policy(report, "edig-parity-v1")
        self.assertTrue(report.is_valid)
        self.assertGreaterEqual(application.adjusted_count, 1)
        # The finding is downgraded, never dropped.
        self.assertTrue(any(i.code == "BAL.CLM02.LINE_SUM" for i in report.issues))

    def test_clean_claim_has_no_adjustments(self) -> None:
        report = validate_document(VALID_837P)
        application = apply_policy(report, "edig-parity-v1")
        self.assertEqual(application.adjusted_count, 0)
        self.assertTrue(report.is_valid)

    def test_none_policy_is_noop(self) -> None:
        report = validate_document(with_unbalanced_claim())
        application = apply_policy(report, None)
        self.assertEqual(application.policy_name, "none")
        self.assertFalse(report.is_valid)


class PolicyFromRowsTests(unittest.TestCase):
    def test_rows_override_base_defaults(self) -> None:
        rows = [
            {"snip_type": 3, "severity": "enforce-reject", "transaction_type": "*"},
            {"snip_type": 4, "severity": "off", "transaction_type": "837"},
        ]
        p = policy_from_rows("partner-x", rows)
        self.assertEqual(p.mode_for(3), PolicyMode.ENFORCE_REJECT)
        self.assertEqual(p.mode_for(4, "837"), PolicyMode.OFF)
        # A transaction without an override falls back to the base default.
        self.assertEqual(p.mode_for(4, "835"), EDIG_PARITY_V1.mode_for(4))

    def test_effective_dating_filters_rows(self) -> None:
        rows = [
            {
                "snip_type": 2,
                "severity": "off",
                "effective_from": "2030-01-01",
                "effective_to": None,
            }
        ]
        # Before the row takes effect, the base default (enforce) is kept.
        p = policy_from_rows("partner-x", rows, as_of=date(2026, 1, 1))
        self.assertEqual(p.mode_for(2), PolicyMode.ENFORCE_REJECT)
        # After it takes effect, the override applies.
        p2 = policy_from_rows("partner-x", rows, as_of=date(2030, 6, 1))
        self.assertEqual(p2.mode_for(2), PolicyMode.OFF)

    def test_apply_resolved_policy_object(self) -> None:
        p = SnipPolicy(
            name="warn-balancing",
            default_modes={int(SnipType.LINE_BALANCING): PolicyMode.WARN},
        )
        report = validate_document(with_unbalanced_claim())
        apply_policy(report, p)
        self.assertTrue(report.is_valid)


if __name__ == "__main__":
    unittest.main()
