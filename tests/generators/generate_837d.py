"""Generate X12 837D (Dental) claims — 005010X224A2.

Produces dental claims with SV3 service lines, TOO (tooth) segments,
and ADA procedure codes.
"""
from __future__ import annotations

import random

from .edi_common import (
    SEG, EL, SUB,
    LAST_NAMES, FIRST_NAMES, STATES, CITIES,
    ADA_CODES, PAYER_IDS,
    rand_npi, rand_ein, rand_member_id, rand_zip, rand_date, rand_dob,
    build_isa, build_trailers, generate_to_size,
)

IMPLEMENTATION_VERSION = "005010X224A2"

# Tooth numbers (universal numbering system: 1-32 adult, A-T primary)
TOOTH_NUMBERS = [str(i) for i in range(1, 33)]

# Tooth surfaces: M=Mesial, O=Occlusal, D=Distal, B=Buccal, L=Lingual, I=Incisal
SURFACES = ["M", "O", "D", "B", "L", "I", "MO", "DO", "MOD", "DL", "MI", "DI"]

# Place of service: 11=Office, 12=Home, 21=Inpatient Hospital, 22=Outpatient Hospital
DENTAL_POS_CODES = ["11", "12", "21", "22"]

# Dental diagnosis codes
DENTAL_DIAG_CODES = ["K0000", "K0210", "K0510", "K0530", "K0800", "K0889"]


def _build_837d_claims(claim_count: int) -> str:
    """Build a complete 837D interchange with the given number of claims."""
    sender_id = "SENDER837D"
    receiver_id = "RECEIVER837D"
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
        "BHT", "0019", "00", "DENTAL001",
        "20260515", "1300", "CH",
    ]) + SEG)

    # 1000A — Submitter
    tx_segs.append(EL.join([
        "NM1", "41", "2", "DENTAL CARE ASSOCIATES",
        "", "", "", "", "46", sender_id,
    ]) + SEG)
    tx_segs.append(EL.join([
        "PER", "IC", "DENTAL BILLING", "TE", "5551234567",
    ]) + SEG)

    # 1000B — Receiver
    tx_segs.append(EL.join([
        "NM1", "40", "2", "DENTAL INSURANCE CO",
        "", "", "", "", "46", receiver_id,
    ]) + SEG)

    # 2000A — Billing Provider HL
    hl_id = 1
    billing_hl = hl_id
    tx_segs.append(EL.join(["HL", str(hl_id), "", "20", "1"]) + SEG)

    # 2010AA — Billing Provider
    tx_segs.append(EL.join([
        "NM1", "85", "2", "DENTAL CARE ASSOCIATES",
        "", "", "", "", "XX", billing_npi,
    ]) + SEG)
    tx_segs.append(EL.join(["N3", "200 SMILE BOULEVARD"]) + SEG)
    tx_segs.append(EL.join(["N4", "SPRINGFIELD", "IL", "62701"]) + SEG)
    tx_segs.append(EL.join(["REF", "EI", billing_ein]) + SEG)

    for claim_idx in range(1, claim_count + 1):
        hl_id += 1

        # 2000B — Subscriber HL
        tx_segs.append(EL.join([
            "HL", str(hl_id), str(billing_hl), "22", "0",
        ]) + SEG)

        # SBR
        tx_segs.append(EL.join([
            "SBR", "P", "18", f"{claim_idx:06d}", "", "", "", "", "", "MC",
        ]) + SEG)

        # 2010BA — Subscriber
        last = random.choice(LAST_NAMES)
        first = random.choice(FIRST_NAMES)
        member_id = rand_member_id()
        tx_segs.append(EL.join([
            "NM1", "IL", "1", last, first, "", "", "", "MI", member_id,
        ]) + SEG)
        tx_segs.append(EL.join([
            "N3", f"{random.randint(1, 9999)} ORTHO LANE",
        ]) + SEG)
        city = random.choice(CITIES)
        state = random.choice(STATES)
        tx_segs.append(EL.join(["N4", city, state, rand_zip()]) + SEG)
        tx_segs.append(EL.join([
            "DMG", "D8", rand_dob(), random.choice(["M", "F"]),
        ]) + SEG)

        # 2300 — Claim
        total_charge = random.uniform(100.0, 2000.0)
        claim_id = f"D{claim_idx:06d}"
        pos = random.choice(DENTAL_POS_CODES)

        tx_segs.append(EL.join([
            "CLM", claim_id, f"{total_charge:.2f}", "", "",
            f"{pos}{SUB}B{SUB}1", "Y", "A", "Y", "I",
        ]) + SEG)

        # HI — dental diagnosis
        diag = random.choice(DENTAL_DIAG_CODES)
        tx_segs.append(EL.join(["HI", f"ABK{SUB}{diag}"]) + SEG)

        # Service lines (1-3 per claim)
        svc_date = rand_date(90)
        num_lines = random.randint(1, 3)
        for line_idx in range(1, num_lines + 1):
            tx_segs.append(EL.join(["LX", str(line_idx)]) + SEG)

            ada_code = random.choice(ADA_CODES)
            line_charge = total_charge / num_lines

            # SV3 — dental service
            tx_segs.append(EL.join([
                "SV3", f"AD{SUB}{ada_code}",
                f"{line_charge:.2f}", "", "JP",
            ]) + SEG)

            # TOO — tooth information
            tooth = random.choice(TOOTH_NUMBERS)
            surface = random.choice(SURFACES)
            tx_segs.append(EL.join(["TOO", "JP", tooth, surface]) + SEG)

            tx_segs.append(EL.join(["DTP", "472", "D8", svc_date]) + SEG)

    # SE
    seg_count = len(tx_segs) + 1
    tx_segs.append(EL.join(["SE", str(seg_count), st_ctrl]) + SEG)

    ge, iea = build_trailers(grp_ctrl, ctrl_num)
    return isa + gs + "".join(tx_segs) + ge + iea


def generate_837d(
    claim_count: int | None = None,
    target_bytes: int | None = None,
) -> str:
    """Generate an 837D Dental claims file.

    Args:
        claim_count: Number of claims to generate.
        target_bytes: Target file size in bytes (±5%). Overrides claim_count.

    Returns:
        Complete X12 837D interchange as a string.
    """
    random.seed(42)

    if target_bytes is not None:
        return generate_to_size(
            _build_837d_claims,
            target_bytes,
            count_kwarg="claim_count",
        )

    return _build_837d_claims(claim_count or 10)
