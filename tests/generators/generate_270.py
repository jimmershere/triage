"""Generate X12 270 (Eligibility Inquiry) — 005010X279A1.

Produces eligibility inquiry transactions with HL hierarchy
(payer -> provider -> subscriber) and multiple service type codes.
"""
from __future__ import annotations

import random

from .edi_common import (
    SEG, EL, SUB,
    LAST_NAMES, FIRST_NAMES, STATES, CITIES, PAYER_IDS,
    rand_npi, rand_member_id, rand_zip, rand_date, rand_dob,
    build_isa, build_trailers, generate_to_size,
)

IMPLEMENTATION_VERSION = "005010X279A1"

# Service type codes: 30=Health Benefit Plan Coverage, 47=Hospital, 88=Pharmacy
SERVICE_TYPE_CODES = ["30", "47", "88", "33", "42", "86", "98"]


def _build_270_members(member_count: int) -> str:
    """Build a complete 270 interchange with the given number of members."""
    sender_id = "SENDER270"
    receiver_id = "RECEIVER270"

    isa, gs, ctrl_num, grp_ctrl = build_isa(
        sender_id, receiver_id,
        functional_id_code="HS",
        implementation_version=IMPLEMENTATION_VERSION,
    )

    st_ctrl = "0001"
    tx_segs: list[str] = []

    # ST
    tx_segs.append(EL.join(["ST", "270", st_ctrl, IMPLEMENTATION_VERSION]) + SEG)

    # BHT
    tx_segs.append(EL.join([
        "BHT", "0022", "13", "ELG0001",
        "20260515", "0900",
    ]) + SEG)

    # HL 1 — Payer (Information Source)
    hl_id = 1
    payer_hl = hl_id
    tx_segs.append(EL.join(["HL", str(hl_id), "", "20", "1"]) + SEG)
    payer_id = random.choice(PAYER_IDS)
    tx_segs.append(EL.join([
        "NM1", "PR", "2", "PRIMARY PAYER",
        "", "", "", "", "PI", payer_id,
    ]) + SEG)

    # HL 2 — Provider (Information Receiver)
    hl_id += 1
    provider_hl = hl_id
    provider_npi = rand_npi()
    tx_segs.append(EL.join(["HL", str(hl_id), str(payer_hl), "21", "1"]) + SEG)
    tx_segs.append(EL.join([
        "NM1", "1P", "2", "REQUESTING PROVIDER",
        "", "", "", "", "XX", provider_npi,
    ]) + SEG)

    # Subscribers
    for idx in range(1, member_count + 1):
        hl_id += 1
        tx_segs.append(EL.join([
            "HL", str(hl_id), str(provider_hl), "22", "0",
        ]) + SEG)

        # TRN — trace
        tx_segs.append(EL.join([
            "TRN", "1", f"REQ{idx:06d}",
        ]) + SEG)

        # NM1 — subscriber
        last = random.choice(LAST_NAMES)
        first = random.choice(FIRST_NAMES)
        member_id = rand_member_id()
        tx_segs.append(EL.join([
            "NM1", "IL", "1", last, first,
            "", "", "", "MI", member_id,
        ]) + SEG)

        # N3/N4 — address
        tx_segs.append(EL.join([
            "N3", f"{random.randint(1, 9999)} MAIN STREET",
        ]) + SEG)
        tx_segs.append(EL.join([
            "N4", random.choice(CITIES), random.choice(STATES), rand_zip(),
        ]) + SEG)

        # DMG — demographics
        tx_segs.append(EL.join([
            "DMG", "D8", rand_dob(), random.choice(["M", "F"]),
        ]) + SEG)

        # EQ — eligibility inquiry (1-3 service types per member)
        num_service_types = random.randint(1, 3)
        for stc in random.sample(SERVICE_TYPE_CODES, num_service_types):
            tx_segs.append(EL.join(["EQ", stc]) + SEG)

        # DTP — date of service
        tx_segs.append(EL.join([
            "DTP", "291", "D8", rand_date(30),
        ]) + SEG)

    # SE
    seg_count = len(tx_segs) + 1
    tx_segs.append(EL.join(["SE", str(seg_count), st_ctrl]) + SEG)

    ge, iea = build_trailers(grp_ctrl, ctrl_num)
    return isa + gs + "".join(tx_segs) + ge + iea


def generate_270(
    member_count: int | None = None,
    target_bytes: int | None = None,
) -> str:
    """Generate a 270 Eligibility Inquiry file.

    Args:
        member_count: Number of member inquiries to generate.
        target_bytes: Target file size in bytes (±5%). Overrides member_count.

    Returns:
        Complete X12 270 interchange as a string.
    """
    random.seed(42)

    if target_bytes is not None:
        return generate_to_size(
            _build_270_members,
            target_bytes,
            count_kwarg="member_count",
        )

    return _build_270_members(member_count or 10)
