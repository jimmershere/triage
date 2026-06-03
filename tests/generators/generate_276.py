"""Generate X12 276 (Claim Status Request) — 005010X212.

Produces claim status request transactions with HL hierarchy
(payer -> provider -> subscriber) and various status category codes.
"""
from __future__ import annotations

import random

from .edi_common import (
    SEG, EL, SUB,
    LAST_NAMES, FIRST_NAMES, PAYER_IDS,
    rand_npi, rand_member_id, rand_date,
    build_isa, build_trailers, generate_to_size,
)

IMPLEMENTATION_VERSION = "005010X212"

# Status category codes for STC
STATUS_CATEGORY_CODES = [
    ("A0", "20", "PR"),  # Acknowledgement/Receipt
    ("A1", "20", "PR"),  # Acknowledgement/Acceptance into adjudication
    ("A2", "20", "PR"),  # Acknowledgement/Acceptance into adjudication - pending
    ("A3", "15", "PR"),  # Acknowledgement/Returned as unprocessable
    ("A4", "0",  "PR"),  # Acknowledgement/Not found
    ("F1", "0",  "PR"),  # Finalized/Payment
    ("F2", "0",  "PR"),  # Finalized/Denial
    ("P0", "20", "PR"),  # Pending/Under review
    ("P1", "20", "PR"),  # Pending/Waiting for information
]


def _build_276_inquiries(inquiry_count: int) -> str:
    """Build a complete 276 interchange with the given number of inquiries."""
    sender_id = "STATUSREQ"
    receiver_id = "STATUSRESP"

    isa, gs, ctrl_num, grp_ctrl = build_isa(
        sender_id, receiver_id,
        functional_id_code="HN",
        implementation_version=IMPLEMENTATION_VERSION,
    )

    st_ctrl = "0001"
    tx_segs: list[str] = []

    # ST
    tx_segs.append(EL.join(["ST", "276", st_ctrl, IMPLEMENTATION_VERSION]) + SEG)

    # BHT
    tx_segs.append(EL.join([
        "BHT", "0010", "13", "STS0001",
        "20260515", "1000",
    ]) + SEG)

    # HL 1 — Payer
    hl_id = 1
    payer_hl = hl_id
    tx_segs.append(EL.join(["HL", str(hl_id), "", "20", "1"]) + SEG)
    payer_id = random.choice(PAYER_IDS)
    tx_segs.append(EL.join([
        "NM1", "PR", "2", "RESPONSIBLE PAYER",
        "", "", "", "", "PI", payer_id,
    ]) + SEG)

    # HL 2 — Provider (billing)
    hl_id += 1
    biller_hl = hl_id
    tx_segs.append(EL.join([
        "HL", str(hl_id), str(payer_hl), "19", "1",
    ]) + SEG)
    tx_segs.append(EL.join([
        "NM1", "41", "2", "REQUESTING BILLER",
        "", "", "", "", "46", "BIL123",
    ]) + SEG)

    # HL 3 — Provider (rendering)
    hl_id += 1
    provider_hl = hl_id
    provider_npi = rand_npi()
    tx_segs.append(EL.join([
        "HL", str(hl_id), str(biller_hl), "19", "1",
    ]) + SEG)
    tx_segs.append(EL.join([
        "NM1", "1P", "2", "RENDERING PROVIDER",
        "", "", "", "", "XX", provider_npi,
    ]) + SEG)

    # Inquiries
    for idx in range(1, inquiry_count + 1):
        hl_id += 1

        # HL — subscriber
        tx_segs.append(EL.join([
            "HL", str(hl_id), str(provider_hl), "22", "0",
        ]) + SEG)

        # NM1 — patient
        last = random.choice(LAST_NAMES)
        first = random.choice(FIRST_NAMES)
        member_id = rand_member_id()
        tx_segs.append(EL.join([
            "NM1", "IL", "1", last, first,
            "", "", "", "MI", member_id,
        ]) + SEG)

        # TRN — trace
        tx_segs.append(EL.join([
            "TRN", "1", f"TRACE{idx:06d}",
        ]) + SEG)

        # STC — status category
        stc = random.choice(STATUS_CATEGORY_CODES)
        status_date = rand_date(60)
        tx_segs.append(EL.join([
            "STC", f"{stc[0]}{SUB}{stc[1]}{SUB}{stc[2]}",
            status_date, "WQ", "0",
        ]) + SEG)

        # REF — claim reference
        tx_segs.append(EL.join([
            "REF", "1K", f"CLM{idx:06d}",
        ]) + SEG)

        # DTP — service date
        tx_segs.append(EL.join([
            "DTP", "472", "D8", rand_date(90),
        ]) + SEG)

    # SE
    seg_count = len(tx_segs) + 1
    tx_segs.append(EL.join(["SE", str(seg_count), st_ctrl]) + SEG)

    ge, iea = build_trailers(grp_ctrl, ctrl_num)
    return isa + gs + "".join(tx_segs) + ge + iea


def generate_276(
    inquiry_count: int | None = None,
    target_bytes: int | None = None,
) -> str:
    """Generate a 276 Claim Status Request file.

    Args:
        inquiry_count: Number of status inquiries to generate.
        target_bytes: Target file size in bytes (±5%). Overrides inquiry_count.

    Returns:
        Complete X12 276 interchange as a string.
    """
    random.seed(42)

    if target_bytes is not None:
        return generate_to_size(
            _build_276_inquiries,
            target_bytes,
            count_kwarg="inquiry_count",
        )

    return _build_276_inquiries(inquiry_count or 10)
