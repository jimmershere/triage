"""835 implementation-guide validation — ASC X12N 005010X221A1
(Health Care Claim Payment/Advice — remittance).

Validates the BPR/TRN financial header, payer/payee identification, the CLP
claim-payment and SVC service-payment loops, and the two balancing equations
that make an 835 conformant:

- **Claim balance:** CLP03 (charge) = CLP04 (paid) + sum of CAS adjustments.
- **Transaction balance:** BPR02 (payment) = sum of CLP04 - sum of PLB adjustments.

Reference: ASC X12N 005010X221A1 Health Care Claim Payment/Advice (835) TR3.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from ..codesets import get_codeset
from ..model import ClaimProjection, Severity, SnipType, ValidationIssue, ValidationReport
from ..parser import Segment, Transaction

_TR3 = "ASC X12N 005010X221A1 (835) TR3"
_BALANCE_TOLERANCE = Decimal("0.01")

_PAYMENT_METHODS = {"ACH", "BOP", "CHK", "FWT", "NON"}
_CAS_AMOUNT_ELEMENTS = (3, 6, 9, 12, 15, 18)
_PLB_AMOUNT_ELEMENTS = (4, 6, 8, 10, 12, 14)


def _dec(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


class _RemitWalker:
    def __init__(self, txn: Transaction, report: ValidationReport) -> None:
        self.txn = txn
        self.report = report
        self._claim_id: str | None = None
        self._clp_charge: Decimal | None = None
        self._clp_paid: Decimal | None = None
        self._clp_seg: Segment | None = None
        self._claim_cas_total = Decimal("0")
        self._claim_svc_cas_sum = Decimal("0")
        self._svc_charge: Decimal | None = None
        self._svc_paid: Decimal | None = None
        self._svc_seg: Segment | None = None
        self._svc_cas_total = Decimal("0")
        self._total_paid = Decimal("0")
        self._total_plb = Decimal("0")
        self._clp_count = 0

    def _issue(
        self,
        snip: SnipType,
        severity: Severity,
        code: str,
        message: str,
        *,
        seg: Segment | None = None,
        segment_id: str | None = None,
        element: int | None = None,
        expected: str | None = None,
        actual: str | None = None,
    ) -> None:
        self.report.add(
            ValidationIssue(
                snip_type=snip,
                severity=severity,
                code=code,
                message=message,
                segment_id=segment_id or (seg.seg_id if seg else None),
                segment_position=seg.position if seg else None,
                element_position=element,
                transaction_set="835",
                transaction_control=self.txn.control_number,
                claim_id=self._claim_id,
                expected=expected,
                actual=actual,
                spec_ref=_TR3,
            )
        )

    def run(self) -> list[ClaimProjection]:
        self._validate_header()
        for seg in self.txn.segments:
            handler = getattr(self, f"_seg_{seg.seg_id}", None)
            if handler is not None:
                handler(seg)
        self._close_service()
        self._close_claim()
        self._validate_presence()
        self._validate_transaction_balance()
        return []

    # -- header ------------------------------------------------------------

    def _validate_header(self) -> None:
        bpr = self.txn.first("BPR")
        if bpr is None:
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.BPR.MISSING",
                "BPR financial information segment is required in an 835.",
                segment_id="BPR",
            )
        else:
            if bpr.elem(3) and bpr.elem(3) not in ("C", "D"):
                self._issue(
                    SnipType.CODE_SET, Severity.ERROR, "CODE.BPR03.CREDIT_DEBIT",
                    f"BPR03 credit/debit flag must be C or D; found "
                    f"'{bpr.elem(3)}'.",
                    seg=bpr, element=3, expected="C or D", actual=bpr.elem(3),
                )
            if bpr.elem(4) and bpr.elem(4) not in _PAYMENT_METHODS:
                self._issue(
                    SnipType.CODE_SET, Severity.ERROR, "CODE.BPR04.PAYMENT_METHOD",
                    f"BPR04 payment method '{bpr.elem(4)}' is not a valid code "
                    f"({', '.join(sorted(_PAYMENT_METHODS))}).",
                    seg=bpr, element=4, actual=bpr.elem(4),
                )

        trn = self.txn.first("TRN")
        if trn is None:
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.TRN.MISSING",
                "TRN reassociation trace segment is required in an 835.",
                segment_id="TRN",
            )
        elif not trn.has_elem(2):
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.TRN02.TRACE",
                "TRN02 reassociation trace number is required.",
                seg=trn, element=2,
            )

    # -- segment handlers --------------------------------------------------

    def _seg_CLP(self, seg: Segment) -> None:
        self._close_service()
        self._close_claim()
        self._clp_seg = seg
        self._claim_id = seg.elem(1) or None
        self._clp_charge = _dec(seg.elem(3))
        self._clp_paid = _dec(seg.elem(4))
        self._claim_cas_total = Decimal("0")
        self._claim_svc_cas_sum = Decimal("0")
        self._clp_count += 1

        if not seg.has_elem(1):
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.CLP01.ID",
                "CLP01 claim submitter identifier is required.",
                seg=seg, element=1,
            )
        if self._clp_charge is None:
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.CLP03.CHARGE",
                f"CLP03 total claim charge must be numeric; found "
                f"'{seg.elem(3)}'.",
                seg=seg, element=3,
            )
        if self._clp_paid is None:
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.CLP04.PAID",
                f"CLP04 claim payment amount must be numeric; found "
                f"'{seg.elem(4)}'.",
                seg=seg, element=4,
            )
        else:
            self._total_paid += self._clp_paid

    def _seg_SVC(self, seg: Segment) -> None:
        self._close_service()
        self._svc_seg = seg
        self._svc_charge = _dec(seg.elem(2))
        self._svc_paid = _dec(seg.elem(3))
        self._svc_cas_total = Decimal("0")

    def _seg_CAS(self, seg: Segment) -> None:
        group = seg.elem(1)
        if group and not get_codeset("adjustment_group").is_valid(group):
            self._issue(
                SnipType.CODE_SET, Severity.ERROR, "CODE.CAS01.GROUP",
                f"CAS01 adjustment group code '{group}' is invalid "
                "(expected CO, CR, OA, PI or PR).",
                seg=seg, element=1, actual=group,
            )
        carc = get_codeset("carc")
        total = Decimal("0")
        for amt_idx in _CAS_AMOUNT_ELEMENTS:
            reason = seg.elem(amt_idx - 1)
            amount = _dec(seg.elem(amt_idx))
            if reason and not carc.is_valid(reason):
                self._issue(
                    SnipType.CODE_SET, Severity.WARNING, "CODE.CAS.REASON",
                    f"CAS adjustment reason code '{reason}' was not found in the "
                    "bundled CARC subset; verify against the full code set.",
                    seg=seg, element=amt_idx - 1, actual=reason,
                )
            if amount is not None:
                total += amount
        if self._svc_seg is not None:
            self._svc_cas_total += total
        else:
            self._claim_cas_total += total

    def _seg_PLB(self, seg: Segment) -> None:
        for amt_idx in _PLB_AMOUNT_ELEMENTS:
            amount = _dec(seg.elem(amt_idx))
            if amount is not None:
                self._total_plb += amount

    # -- balancing ---------------------------------------------------------

    def _close_service(self) -> None:
        if self._svc_seg is None:
            return
        if self._svc_charge is not None and self._svc_paid is not None:
            expected = self._svc_paid + self._svc_cas_total
            if abs(self._svc_charge - expected) > _BALANCE_TOLERANCE:
                self._issue(
                    SnipType.LINE_BALANCING, Severity.ERROR, "BAL.SVC.SERVICE",
                    f"Service line does not balance: SVC02 charge "
                    f"{self._svc_charge} != SVC03 paid {self._svc_paid} + "
                    f"adjustments {self._svc_cas_total}.",
                    seg=self._svc_seg, segment_id="SVC",
                    expected=str(self._svc_charge), actual=str(expected),
                )
        self._claim_svc_cas_sum += self._svc_cas_total
        self._svc_seg = None
        self._svc_charge = None
        self._svc_paid = None
        self._svc_cas_total = Decimal("0")

    def _close_claim(self) -> None:
        if self._clp_seg is None:
            return
        if self._clp_charge is not None and self._clp_paid is not None:
            expected = (
                self._clp_paid + self._claim_cas_total + self._claim_svc_cas_sum
            )
            if abs(self._clp_charge - expected) > _BALANCE_TOLERANCE:
                self._issue(
                    SnipType.BALANCING, Severity.ERROR, "BAL.CLP.CLAIM",
                    f"Claim does not balance: CLP03 charge {self._clp_charge} "
                    f"!= CLP04 paid {self._clp_paid} + adjustments "
                    f"{self._claim_cas_total + self._claim_svc_cas_sum}.",
                    seg=self._clp_seg, segment_id="CLP",
                    expected=str(self._clp_charge), actual=str(expected),
                )
        self._clp_seg = None
        self._clp_charge = None
        self._clp_paid = None
        self._claim_cas_total = Decimal("0")
        self._claim_id = None

    def _validate_transaction_balance(self) -> None:
        bpr = self.txn.first("BPR")
        if bpr is None:
            return
        bpr02 = _dec(bpr.elem(2))
        if bpr02 is None:
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.BPR02.AMOUNT",
                f"BPR02 total payment amount must be numeric; found "
                f"'{bpr.elem(2)}'.",
                seg=bpr, element=2,
            )
            return
        expected = self._total_paid - self._total_plb
        if abs(bpr02 - expected) > _BALANCE_TOLERANCE:
            self._issue(
                SnipType.BALANCING, Severity.ERROR, "BAL.BPR02.TRANSACTION",
                f"Transaction does not balance: BPR02 payment {bpr02} != sum of "
                f"claim payments {self._total_paid} - provider adjustments "
                f"{self._total_plb}.",
                seg=bpr, segment_id="BPR", element=2,
                expected=str(bpr02), actual=str(expected),
            )

    def _validate_presence(self) -> None:
        segs = self.txn.segments
        if not any(s.seg_id == "N1" and s.elem(1) == "PR" for s in segs):
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.N1PR.MISSING",
                "Loop 1000A payer identification (N1*PR) is required.",
                segment_id="N1", element=1,
            )
        if not any(s.seg_id == "N1" and s.elem(1) == "PE" for s in segs):
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.N1PE.MISSING",
                "Loop 1000B payee identification (N1*PE) is required.",
                segment_id="N1", element=1,
            )
        if self._clp_count == 0:
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.CLP.MISSING",
                "Remittance contains no claim payment (loop 2100 CLP required).",
                segment_id="CLP",
            )


def validate_835(txn: Transaction, report: ValidationReport) -> list[ClaimProjection]:
    """Validate one 835 remittance transaction."""
    return _RemitWalker(txn, report).run()
