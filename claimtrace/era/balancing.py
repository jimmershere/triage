"""835 balancing relationships.

Three relationships make an 835 conformant (ASC X12N 005010X221A1):

* **Service line:** SVC02 (charge) - Σ line CAS = SVC03 (paid).
* **Claim:** CLP03 (charge) - Σ CAS (claim + service) = CLP04 (paid).
* **Transaction:** BPR02 (payment) = Σ CLP04 - Σ PLB provider adjustments.

Reconstruction must reproduce these exactly or the file is rejected downstream;
this module computes a structured report used by the API/worker to certify a
reconstructed or reversed 835 before it is stored or transmitted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .model import (
    CAS_AMOUNT_POSITIONS,
    PLB_AMOUNT_POSITIONS,
    Claim,
    Era835,
    Segment,
    Service,
    to_decimal,
)

TOLERANCE = Decimal("0.01")
_ZERO = Decimal("0")


def _cas_total(seg: Segment) -> Decimal:
    total = _ZERO
    for pos in CAS_AMOUNT_POSITIONS:
        amount = to_decimal(seg.elem(pos))
        if amount is not None:
            total += amount
    return total


def service_adjustment_total(service: Service) -> Decimal:
    return sum((_cas_total(s) for s in service.cas_segments()), _ZERO)


def claim_adjustment_total(claim: Claim) -> Decimal:
    """Σ of all CAS adjustment amounts in a claim (claim-level + service-level)."""
    total = sum((_cas_total(s) for s in claim.claim_cas_segments()), _ZERO)
    for service in claim.services:
        total += service_adjustment_total(service)
    return total


def plb_total(era: Era835) -> Decimal:
    total = _ZERO
    for seg in era.plbs:
        for pos in PLB_AMOUNT_POSITIONS:
            amount = to_decimal(seg.elem(pos))
            if amount is not None:
                total += amount
    return total


@dataclass
class BalanceReport:
    balanced: bool = True
    errors: list[dict[str, Any]] = field(default_factory=list)
    claim_paid_total: Decimal = _ZERO
    plb_total: Decimal = _ZERO
    bpr_amount: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "balanced": self.balanced,
            "errors": self.errors,
            "claim_paid_total": str(self.claim_paid_total),
            "plb_total": str(self.plb_total),
            "bpr_amount": None if self.bpr_amount is None else str(self.bpr_amount),
        }


def balance_report(era: Era835) -> BalanceReport:
    """Verify the three 835 balancing relationships for ``era``."""
    report = BalanceReport()
    claim_paid_total = _ZERO

    for claim in era.claims:
        # Service-line balance: SVC02 - Σ CAS = SVC03.
        for service in claim.services:
            charge = service.charge
            paid = service.paid
            if charge is None or paid is None:
                continue
            expected = paid + service_adjustment_total(service)
            if abs(charge - expected) > TOLERANCE:
                report.balanced = False
                report.errors.append(
                    {
                        "level": "service",
                        "claim_id": claim.claim_id,
                        "code": "BAL.SVC.SERVICE",
                        "message": (
                            f"Service line for claim {claim.claim_id} does not "
                            f"balance: SVC02 {charge} != SVC03 {paid} + "
                            f"adjustments {service_adjustment_total(service)}."
                        ),
                    }
                )

        # Claim balance: CLP03 - Σ CAS = CLP04.
        charge = claim.charge
        paid = claim.paid
        if charge is not None and paid is not None:
            expected = paid + claim_adjustment_total(claim)
            if abs(charge - expected) > TOLERANCE:
                report.balanced = False
                report.errors.append(
                    {
                        "level": "claim",
                        "claim_id": claim.claim_id,
                        "code": "BAL.CLP.CLAIM",
                        "message": (
                            f"Claim {claim.claim_id} does not balance: CLP03 "
                            f"{charge} != CLP04 {paid} + adjustments "
                            f"{claim_adjustment_total(claim)}."
                        ),
                    }
                )
        if paid is not None:
            claim_paid_total += paid

    report.claim_paid_total = claim_paid_total
    report.plb_total = plb_total(era)
    report.bpr_amount = era.bpr_amount

    # Transaction balance: BPR02 = Σ CLP04 - Σ PLB.
    if report.bpr_amount is not None:
        expected = claim_paid_total - report.plb_total
        if abs(report.bpr_amount - expected) > TOLERANCE:
            report.balanced = False
            report.errors.append(
                {
                    "level": "transaction",
                    "code": "BAL.BPR02.TRANSACTION",
                    "message": (
                        f"Transaction does not balance: BPR02 "
                        f"{report.bpr_amount} != Σ CLP04 {claim_paid_total} - "
                        f"Σ PLB {report.plb_total}."
                    ),
                }
            )
    return report
