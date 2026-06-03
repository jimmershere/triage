"""Generate X12 271 (Eligibility Response) — 005010X279A1.

Produces eligibility response transactions with EB (eligibility/benefit)
segments, mix of active and inactive coverage, copay/deductible/coinsurance.
"""
from __future__ import annotations

import random

from .edi_common import (
    SEG, EL, SUB,
    LAST_NAMES, FIRST_NAMES, STATES, CITIES, PAYER_IDS,
    rand_npi, rand_member_id, rand_zip, rand_date, rand_dob,
    rand_amount, build_isa, build_trailers, generate_to_size,
)

IMPLEMENTATION_VERSION = "005010X279A1"

# EB01: 1=Active Coverage, 6=Inactive
EB_STATUS_CODES = ["1", "1", "1", "1", "6"]  # weighted toward active

# Service type codes
SERVICE_TYPE_CODES = ["30", "47", "88", "33", "42", "86"]

# Insurance type codes
INSURANCE_TYPES = ["HM", "PR", "PS", "QM", "12", "13", "14"]


def _build_271_members(member_count: int) -> str:
    """Build a complete 271 interchange with the given number of members."""
    sender_id = "RESPONDER271"
    receiver_id = "REQUESTER271"

    isa, gs, ctrl_num, grp_ctrl = build_isa(
        sender_id, receiver_id,
        functional_id_code="HB",
        implementation_version=IMPLEMENTATION_VERSION,
    )

    st_ctrl = "0001"
    tx_segs: list[str] = []

    # ST
    tx_segs.append(EL.join(["ST", "271", st_ctrl, IMPLEMENTATION_VERSION]) + SEG)

    # BHT
    tx_segs.append(EL.join([
        "BHT", "0022", "11", "RESP0001",
        "20260515", "0915",
    ]) + SEG)

    # HL 1 — Payer
    hl_id = 1
    payer_hl = hl_id
    tx_segs.append(EL.join(["HL", str(hl_id), "", "20", "1"]) + SEG)
    payer_id = random.choice(PAYER_IDS)
    tx_segs.append(EL.join([
        "NM1", "PR", "2", "PRIMARY PAYER",
        "", "", "", "", "PI", payer_id,
    ]) + SEG)

    # HL 2 — Provider
    hl_id += 1
    provider_hl = hl_id
    provider_npi = rand_npi()
    tx_segs.append(EL.join([
        "HL", str(hl_id), str(payer_hl), "21", "1",
    ]) + SEG)
    tx_segs.append(EL.join([
        "NM1", "1P", "2", "RESPONDING PROVIDER",
        "", "", "", "", "XX", provider_npi,
    ]) + SEG)

    # Subscribers
    for idx in range(1, member_count + 1):
        hl_id += 1
        tx_segs.append(EL.join([
            "HL", str(hl_id), str(provider_hl), "22", "0",
        ]) + SEG)

        # TRN
        tx_segs.append(EL.join([
            "TRN", "1", f"RESP{idx:06d}",
        ]) + SEG)

        # NM1 — subscriber
        last = random.choice(LAST_NAMES)
        first = random.choice(FIRST_NAMES)
        member_id = rand_member_id()
        tx_segs.append(EL.join([
            "NM1", "IL", "1", last, first,
            "", "", "", "MI", member_id,
        ]) + SEG)

        # N3/N4
        tx_segs.append(EL.join([
            "N3", f"{random.randint(1, 9999)} RESPONSE AVE",
        ]) + SEG)
        tx_segs.append(EL.join([
            "N4", random.choice(CITIES), random.choice(STATES), rand_zip(),
        ]) + SEG)

        # DMG
        tx_segs.append(EL.join([
            "DMG", "D8", rand_dob(), random.choice(["M", "F"]),
        ]) + SEG)

        # EB segments — coverage details
        status = random.choice(EB_STATUS_CODES)
        stc = random.choice(SERVICE_TYPE_CODES)
        ins_type = random.choice(INSURANCE_TYPES)

        # Primary EB — active/inactive
        tx_segs.append(EL.join([
            "EB", status, "IND", stc, "", ins_type, "", "", "Y",
        ]) + SEG)

        if status == "1":  # active — add benefit details
            # Copay
            copay = random.uniform(15.0, 75.0)
            tx_segs.append(EL.join([
                "EB", "B", "IND", stc, "LA", "", "", "",
                "", "", "", f"{copay:.2f}",
            ]) + SEG)

            # Deductible
            deductible = random.uniform(250.0, 5000.0)
            tx_segs.append(EL.join([
                "EB", "C", "IND", stc, "", "", "", "",
                "", "", "", f"{deductible:.2f}",
            ]) + SEG)

            # Coinsurance
            coinsurance_pct = random.choice(["20", "25", "30", "40"])
            tx_segs.append(EL.join([
                "EB", "A", "IND", stc, "", "", "", "",
                "", "", "", "", coinsurance_pct,
            ]) + SEG)

        # DTP — plan dates
        tx_segs.append(EL.join([
            "DTP", "291", "D8", rand_date(30),
        ]) + SEG)

    # SE
    seg_count = len(tx_segs) + 1
    tx_segs.append(EL.join(["SE", str(seg_count), st_ctrl]) + SEG)

    ge, iea = build_trailers(grp_ctrl, ctrl_num)
    return isa + gs + "".join(tx_segs) + ge + iea


def generate_271(
    member_count: int | None = None,
    target_bytes: int | None = None,
) -> str:
    """Generate a 271 Eligibility Response file.

    Args:
        member_count: Number of member responses to generate.
        target_bytes: Target file size in bytes (±5%). Overrides member_count.

    Returns:
        Complete X12 271 interchange as a string.
    """
    random.seed(42)

    if target_bytes is not None:
        return generate_to_size(
            _build_271_members,
            target_bytes,
            count_kwarg="member_count",
        )

    return _build_271_members(member_count or 10)
