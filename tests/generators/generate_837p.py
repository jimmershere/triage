"""Generate X12 837P (Professional) claims — 005010X222A1.

Refactored from bench/generate_837p.py to use shared edi_common infrastructure
and support both claim_count and target_bytes modes.
"""
from __future__ import annotations

import random

from .edi_common import (
    SEG, EL, SUB,
    LAST_NAMES, FIRST_NAMES, STATES, CITIES,
    DIAG_CODES, CPT_CODES, PAYER_IDS,
    rand_npi, rand_ein, rand_member_id, rand_zip, rand_date, rand_dob,
    rand_amount, build_isa, build_trailers, generate_to_size,
)

IMPLEMENTATION_VERSION = "005010X222A1"
STREETS = ["OAK", "ELM", "PINE", "MAPLE", "CEDAR", "BIRCH", "WALNUT", "ASH"]


def _build_837p_claims(claim_count: int) -> str:
    """Build a complete 837P interchange with the given number of claims."""
    sender_id = "SENDER837P"
    receiver_id = "RECEIVER837P"
    billing_npi = rand_npi()
    billing_ein = rand_ein()

    isa, gs, ctrl_num, grp_ctrl = build_isa(
        sender_id, receiver_id,
        functional_id_code="HC",
        implementation_version=IMPLEMENTATION_VERSION,
    )

    st_ctrl = "0001"
    tx_segs: list[str] = []

    # ST
    tx_segs.append(EL.join(["ST", "837", st_ctrl, IMPLEMENTATION_VERSION]) + SEG)

    # BHT
    tx_segs.append(EL.join([
        "BHT", "0019", "00", "BENCH0001",
        "20260515", "1200", "CH",
    ]) + SEG)

    # 1000A — Submitter
    tx_segs.append(EL.join([
        "NM1", "41", "2", "BENCHMARK MEDICAL GROUP",
        "", "", "", "", "46", sender_id,
    ]) + SEG)
    tx_segs.append(EL.join([
        "PER", "IC", "EDI DEPARTMENT", "TE", "5551234567",
    ]) + SEG)

    # 1000B — Receiver
    tx_segs.append(EL.join([
        "NM1", "40", "2", "BENCHMARK INSURANCE CO",
        "", "", "", "", "46", receiver_id,
    ]) + SEG)

    # 2000A — Billing Provider HL
    hl_id = 1
    billing_hl = hl_id
    tx_segs.append(EL.join(["HL", str(hl_id), "", "20", "1"]) + SEG)

    # 2010AA — Billing Provider Name
    tx_segs.append(EL.join([
        "NM1", "85", "2", "BENCHMARK MEDICAL GROUP",
        "", "", "", "", "XX", billing_npi,
    ]) + SEG)
    tx_segs.append(EL.join(["N3", "100 MAIN STREET"]) + SEG)
    tx_segs.append(EL.join(["N4", "SPRINGFIELD", "IL", "62701"]) + SEG)
    tx_segs.append(EL.join(["REF", "EI", billing_ein]) + SEG)

    # Generate claims
    for claim_idx in range(1, claim_count + 1):
        hl_id += 1

        # 2000B — Subscriber HL
        tx_segs.append(EL.join([
            "HL", str(hl_id), str(billing_hl), "22", "0",
        ]) + SEG)

        # SBR
        tx_segs.append(EL.join([
            "SBR", "P", "18", "", "", "", "", "", "", "MC",
        ]) + SEG)

        # 2010BA — Subscriber Name
        last = random.choice(LAST_NAMES)
        first = random.choice(FIRST_NAMES)
        member_id = rand_member_id()
        tx_segs.append(EL.join([
            "NM1", "IL", "1", last, first, "", "", "", "MI", member_id,
        ]) + SEG)
        tx_segs.append(EL.join([
            "N3", f"{random.randint(1, 9999)} {random.choice(STREETS)} ST",
        ]) + SEG)
        st = random.choice(STATES)
        tx_segs.append(EL.join([
            "N4", random.choice(CITIES), st, rand_zip(),
        ]) + SEG)
        tx_segs.append(EL.join([
            "DMG", "D8", rand_dob(), random.choice(["M", "F"]),
        ]) + SEG)

        # 2010BB — Payer Name
        payer_id = random.choice(PAYER_IDS)
        tx_segs.append(EL.join([
            "NM1", "PR", "2", "BENCHMARK INSURANCE CO",
            "", "", "", "", "PI", payer_id,
        ]) + SEG)

        # 2300 — Claim Information
        charge = random.uniform(75.0, 750.0)
        claim_id = f"CLM{claim_idx:07d}"
        tx_segs.append(EL.join([
            "CLM", claim_id, f"{charge:.2f}", "", "",
            f"11{SUB}B{SUB}1", "Y", "A", "Y", "I",
        ]) + SEG)

        # DTP — service date
        svc_date = rand_date(90)
        tx_segs.append(EL.join(["DTP", "431", "D8", svc_date]) + SEG)

        # HI — diagnosis codes (1-4 per claim)
        num_diag = random.randint(1, 4)
        diags = random.sample(DIAG_CODES, min(num_diag, len(DIAG_CODES)))
        hi_elements = ["HI"]
        for i, dx in enumerate(diags):
            qualifier = "ABK" if i == 0 else "ABF"
            hi_elements.append(f"{qualifier}{SUB}{dx}")
        tx_segs.append(EL.join(hi_elements) + SEG)

        # 2400 — Service Lines (1-4 per claim)
        num_lines = random.randint(1, 4)
        for line_idx in range(1, num_lines + 1):
            tx_segs.append(EL.join(["LX", str(line_idx)]) + SEG)
            cpt = random.choice(CPT_CODES)
            line_charge = charge / num_lines
            tx_segs.append(EL.join([
                "SV1", f"HC{SUB}{cpt}",
                f"{line_charge:.2f}", "UN", "1", "", "",
                f"1{SUB}2{SUB}3",
            ]) + SEG)
            tx_segs.append(EL.join(["DTP", "472", "D8", svc_date]) + SEG)

    # SE
    seg_count = len(tx_segs) + 1  # +1 for SE itself
    tx_segs.append(EL.join(["SE", str(seg_count), st_ctrl]) + SEG)

    # Assemble
    ge, iea = build_trailers(grp_ctrl, ctrl_num)
    return isa + gs + "".join(tx_segs) + ge + iea


def generate_837p(
    claim_count: int | None = None,
    target_bytes: int | None = None,
) -> str:
    """Generate an 837P Professional claims file.

    Args:
        claim_count: Number of claims to generate.
        target_bytes: Target file size in bytes (±5%). Overrides claim_count.

    Returns:
        Complete X12 837P interchange as a string.
    """
    random.seed(42)

    if target_bytes is not None:
        return generate_to_size(
            _build_837p_claims,
            target_bytes,
            count_kwarg="claim_count",
        )

    return _build_837p_claims(claim_count or 10)
