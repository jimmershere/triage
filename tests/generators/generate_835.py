"""Generate X12 835 (Remittance Advice) — 005010X221A1.

Produces remittance files with BPR, TRN, CLP, CAS segments and a
mix of paid (status 1), denied (status 4), and pending (status 20) claims.
"""
from __future__ import annotations

import random

from .edi_common import (
    SEG, EL, SUB,
    LAST_NAMES, FIRST_NAMES, CPT_CODES, PAYER_IDS,
    rand_npi, rand_member_id, rand_date, rand_amount,
    build_isa, build_trailers, generate_to_size, TODAY,
)

IMPLEMENTATION_VERSION = "005010X221A1"

# CLP02 claim status codes: 1=Processed as Primary, 4=Denied, 20=Pending
CLAIM_STATUSES = ["1", "1", "1", "1", "4", "20"]  # weighted toward paid

# CAS adjustment reason codes
CO_REASON_CODES = ["45", "253", "97", "B5", "16"]  # Contractual obligation reasons
PR_REASON_CODES = ["1", "2", "3"]  # Patient responsibility: deductible, coinsurance, copay


def _build_835_claims(claim_count: int) -> str:
    """Build a complete 835 interchange with the given number of claims."""
    sender_id = "PAYER835"
    receiver_id = "PAYEE835"

    isa, gs, ctrl_num, grp_ctrl = build_isa(
        sender_id, receiver_id,
        functional_id_code="HP",
        implementation_version=IMPLEMENTATION_VERSION,
    )

    st_ctrl = "0001"
    tx_segs: list[str] = []

    # ST
    tx_segs.append(EL.join(["ST", "835", st_ctrl]) + SEG)

    # BPR — payment info
    total_payment = claim_count * 200.0
    tx_segs.append(EL.join([
        "BPR", "I", f"{total_payment:.2f}", "C", "ACH", "CTX",
        "01", "123456789", "DA", "123456789012",
        "1512345678", "", "01", "987654321", "DA",
        "987654321098", TODAY.strftime("%Y%m%d"),
    ]) + SEG)

    # TRN — trace number
    tx_segs.append(EL.join([
        "TRN", "1", "1234567890", "1512345678",
    ]) + SEG)

    # DTM — production date
    tx_segs.append(EL.join([
        "DTM", "405", TODAY.strftime("%Y%m%d"),
    ]) + SEG)

    # N1 loop — Payer
    tx_segs.append(EL.join([
        "N1", "PR", "PRIMARY HEALTH PLAN", "XV", "842610001",
    ]) + SEG)
    tx_segs.append(EL.join(["N3", "123 HEALTH STREET"]) + SEG)
    tx_segs.append(EL.join(["N4", "METROPOLIS", "NY", "10101"]) + SEG)
    tx_segs.append(EL.join([
        "PER", "BL", "EDI SUPPORT", "TE", "8005551212",
        "EM", "support@primaryhealth.com",
    ]) + SEG)

    # N1 loop — Payee
    payee_npi = rand_npi()
    tx_segs.append(EL.join([
        "N1", "PE", "PAYEE CLINIC", "XX", payee_npi,
    ]) + SEG)
    tx_segs.append(EL.join(["N3", "456 CLINIC AVENUE"]) + SEG)
    tx_segs.append(EL.join(["N4", "GOTHAM", "NY", "10001"]) + SEG)
    tx_segs.append(EL.join(["REF", "TJ", "987654321"]) + SEG)

    # Claims
    for idx in range(1, claim_count + 1):
        claim_id = f"CLM{idx:06d}"
        patient_id = f"PM{idx:06d}"
        status = random.choice(CLAIM_STATUSES)

        charged = random.uniform(150.0, 800.0)

        if status == "1":  # paid
            paid = charged * random.uniform(0.6, 0.9)
            co_adj = charged * random.uniform(0.05, 0.15)
            pr_adj = charged - paid - co_adj
        elif status == "4":  # denied
            paid = 0.0
            co_adj = charged
            pr_adj = 0.0
        else:  # pending
            paid = 0.0
            co_adj = 0.0
            pr_adj = 0.0

        # LX
        tx_segs.append(EL.join(["LX", str(idx)]) + SEG)

        # CLP — claim payment info
        tx_segs.append(EL.join([
            "CLP", f"{idx:06d}", status,
            f"{charged:.2f}", f"{paid:.2f}", f"{max(pr_adj, 0):.2f}",
            "MC", claim_id, "11", "1",
        ]) + SEG)

        # CAS — adjustments
        if co_adj > 0:
            tx_segs.append(EL.join([
                "CAS", "CO", random.choice(CO_REASON_CODES),
                f"{co_adj:.2f}",
            ]) + SEG)
        if pr_adj > 0:
            tx_segs.append(EL.join([
                "CAS", "PR", random.choice(PR_REASON_CODES),
                f"{pr_adj:.2f}",
            ]) + SEG)

        # NM1 — patient
        last = random.choice(LAST_NAMES)
        first = random.choice(FIRST_NAMES)
        tx_segs.append(EL.join([
            "NM1", "QC", "1", last, first,
            "", "", "", "MI", patient_id,
        ]) + SEG)

        # DTM — service dates
        svc_date = rand_date(90)
        tx_segs.append(EL.join(["DTM", "232", svc_date]) + SEG)
        tx_segs.append(EL.join(["DTM", "233", svc_date]) + SEG)

        # SVC — service line
        cpt = random.choice(CPT_CODES)
        tx_segs.append(EL.join([
            "SVC", f"HC{SUB}{cpt}",
            f"{charged:.2f}", f"{paid:.2f}", "1",
        ]) + SEG)

        # REF — line item reference
        tx_segs.append(EL.join(["REF", "6R", f"{idx:06d}"]) + SEG)

        # AMT — supplemental amount
        tx_segs.append(EL.join(["AMT", "B6", f"{paid:.2f}"]) + SEG)

    # SE
    seg_count = len(tx_segs) + 1
    tx_segs.append(EL.join(["SE", str(seg_count), st_ctrl]) + SEG)

    ge, iea = build_trailers(grp_ctrl, ctrl_num)
    return isa + gs + "".join(tx_segs) + ge + iea


def generate_835(
    claim_count: int | None = None,
    target_bytes: int | None = None,
) -> str:
    """Generate an 835 Remittance Advice file.

    Args:
        claim_count: Number of claims to generate.
        target_bytes: Target file size in bytes (±5%). Overrides claim_count.

    Returns:
        Complete X12 835 interchange as a string.
    """
    random.seed(42)

    if target_bytes is not None:
        return generate_to_size(
            _build_835_claims,
            target_bytes,
            count_kwarg="claim_count",
        )

    return _build_835_claims(claim_count or 10)
