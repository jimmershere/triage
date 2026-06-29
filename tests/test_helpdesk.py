"""Unit tests for the Workstream 4 (operational helpdesk) pure-Python core:
the case status machine and the human-readable rejection-report packager.
"""
import unittest

from claimtrace.helpdesk import (
    STATUS_AWAITING,
    STATUS_NOTIFIED,
    STATUS_OPEN,
    STATUS_RESOLVED,
    CaseStatusError,
    build_rejection_report,
    next_statuses,
    validate_transition,
)
from claimtrace.helpdesk.rejection_report import RejectionContext, RejectionItem


class StatusMachineTests(unittest.TestCase):
    def test_happy_path_progression(self):
        validate_transition(STATUS_OPEN, STATUS_NOTIFIED)
        validate_transition(STATUS_NOTIFIED, STATUS_AWAITING)
        validate_transition(STATUS_AWAITING, STATUS_RESOLVED)

    def test_resubmission_failure_steps_back(self):
        validate_transition(STATUS_AWAITING, STATUS_NOTIFIED)

    def test_any_open_case_can_resolve(self):
        validate_transition(STATUS_OPEN, STATUS_RESOLVED)
        validate_transition(STATUS_NOTIFIED, STATUS_RESOLVED)

    def test_illegal_transitions_rejected(self):
        with self.assertRaises(CaseStatusError):
            validate_transition(STATUS_OPEN, STATUS_AWAITING)  # cannot skip notify
        with self.assertRaises(CaseStatusError):
            validate_transition(STATUS_RESOLVED, STATUS_OPEN)  # closed is terminal
        with self.assertRaises(CaseStatusError):
            validate_transition(STATUS_OPEN, STATUS_OPEN)  # no-op
        with self.assertRaises(CaseStatusError):
            validate_transition("bogus", STATUS_OPEN)

    def test_next_statuses(self):
        self.assertEqual(next_statuses(STATUS_RESOLVED), set())
        self.assertIn(STATUS_NOTIFIED, next_statuses(STATUS_OPEN))


class RejectionReportTests(unittest.TestCase):
    def _ctx(self, ack_type="999"):
        return RejectionContext(
            claim_id="claim-abc",
            claim_hash_id="hash-123",
            trading_partner_id="TP100",
            submitter_id="SUB42",
            transaction_set="837",
            ack_type=ack_type,
            interchange_control="000000001",
            transaction_control="0001",
            file_reference="batch.x12",
            correlation_id="corr-9",
            items=[
                RejectionItem(
                    code="8",
                    message="Segment has data element errors.",
                    segment_id="CLM",
                    element_position="05",
                    loop_id="2300",
                ),
                RejectionItem(
                    code="I12",
                    message="Implementation pattern match failure.",
                    segment_id="NM1",
                    element_position="09",
                    loop_id="2010BA",
                ),
            ],
        )

    def test_999_report_targets_software_team(self):
        report = build_rejection_report(self._ctx("999"))
        self.assertIn("software team", report["text"])
        self.assertIn("claim-abc", report["text"])
        self.assertIn("segment CLM05", report["text"])
        self.assertEqual(report["item_count"], 2)
        self.assertEqual(report["ack_type"], "999")
        self.assertIn("does not modify your data", report["text"])

    def test_277ca_report_targets_billing_team(self):
        report = build_rejection_report(self._ctx("277CA"))
        self.assertIn("billing team", report["text"])
        self.assertEqual(report["ack_type"], "277CA")

    def test_report_is_phi_safe_by_construction(self):
        # Only reference ids/codes/locations are present — no member/clinical fields.
        report = build_rejection_report(self._ctx())
        for forbidden in ("dob", "ssn", "diagnosis", "member name"):
            self.assertNotIn(forbidden, report["text"].lower())

    def test_from_dict_contract_accepts_ws7_event_shape(self):
        ctx = RejectionContext.from_dict(
            {
                "claim_id": "c1",
                "ack_type": "999",
                "items": [
                    {"code": "8", "segment": "CLM", "element": "05", "loop": "2300",
                     "message": "bad"}
                ],
            }
        )
        report = build_rejection_report(ctx)
        self.assertEqual(report["item_count"], 1)
        self.assertEqual(report["items"][0]["segment_id"], "CLM")


if __name__ == "__main__":
    unittest.main()
