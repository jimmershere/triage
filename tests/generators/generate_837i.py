"""Generate X12 837I (Institutional) claims — 005010X223A2.

Produces inpatient and outpatient institutional claims with CL1 segments,
SV2 service lines with revenue codes, and RD8 date ranges for stays.
"""
from __future__ import annotations

import random

from .edi_common import (
    SEG, EL, SUB,
    LAST_NAMES, FIRST_NAMES, STATES, CITIES,
    DIAG_CODES, REVENUE_CODES, HOSPITAL_NAMES, PAYER_IDS,
    rand_npi, rand_ein, rand_member_id, rand_zip, rand_dob,
    rand_date_range, rand_amount, build_isa, build_trailers,
    generate_to_size, CPT_CODES,
)

IMPLEMENTATION_VERSION = "005010X223A2"

# Admit type: 1=Emergency, 2=Urgent, 3=Elective, 4=Newborn
ADMIT_TYPES = ["1", "2", "3", "4"]
# Admit source: 1=Physician referral, 2=Clinic referral, 4=Transfer, 7=Emergency room
ADMIT_SOURCES = ["1", "2", "4", "7"]
# Discharge status: 01=Home, 02=Short-term hospital, 03=SNF, 06=Home health
DISCHARGE_STATUSES = ["01", "02", "03", "06"]

# Facility type codes for CLM05 — 11=Hospital inpatient, 21=Hospital outpatient ER
FACILITY_CODES = [
    ("11", "A", "1"),  # Hospital inpatient, admission
    ("21", "B", "1"),  # Hospital outpatient
]


def _build_837i_claims(claim_count: int) -> str:
    """Build a complete 837I interchange with the given number of claims."""
    sender_id = "SENDER837I"
    receiver_id = "RECEIVER837I"
    hospital_name = random.choice(HOSPITAL_NAMES)
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
        "BHT", "0019", "00", "INST0001",
        "20260515", "0830", "CH",
    ]) + SEG)

    # 1000A — Submitter
    tx_segs.append(EL.join([
        "NM1", "41", "2", hospital_name,
        "", "", "", "", "46", sender_id,
    ]) + SEG)
    tx_segs.append(EL.join([
        "PER", "IC", "PATIENT ACCOUNTS", "TE", "5554567890",
    ]) + SEG)

    # 1000B — Receiver
    tx_segs.append(EL.join([
        "NM1", "40", "2", "INSTITUTIONAL PAYER",
        "", "", "", "", "46", receiver_id,
    ]) + SEG)

    # 2000A — Billing Provider HL
    hl_id = 1
    billing_hl = hl_id
    tx_segs.append(EL.join(["HL", str(hl_id), "", "20", "1"]) + SEG)

    # 2010AA — Billing Provider
    tx_segs.append(EL.join([
        "NM1", "85", "2", hospital_name,
        "", "", "", "", "XX", billing_npi,
    ]) + SEG)
    tx_segs.append(EL.join(["N3", "8000 HOSPITAL PARKWAY"]) + SEG)
    tx_segs.append(EL.join(["N4", "DENVER", "CO", "80204"]) + SEG)
    tx_segs.append(EL.join(["REF", "EI", billing_ein]) + SEG)

    for claim_idx in range(1, claim_count + 1):
        hl_id += 1
        is_inpatient = random.random() < 0.6

        # 2000B — Subscriber HL
        tx_segs.append(EL.join([
            "HL", str(hl_id), str(billing_hl), "22", "1",
        ]) + SEG)

        # SBR
        tx_segs.append(EL.join([
            "SBR", "P", "18", "", "", "", "", "", "", "CI",
        ]) + SEG)

        # 2010BA — Subscriber
        last = random.choice(LAST_NAMES)
        first = random.choice(FIRST_NAMES)
        member_id = rand_member_id()
        tx_segs.append(EL.join([
            "NM1", "IL", "1", last, first, "", "", "", "MI", member_id,
        ]) + SEG)
        tx_segs.append(EL.join([
            "N3", f"{random.randint(100, 9999)} {random.choice(['HOSPITAL', 'BASIN', 'CANYON', 'ALPINE', 'HARBOR'])} {random.choice(['ST', 'AVE', 'DR', 'RD'])}",
        ]) + SEG)
        city = random.choice(CITIES)
        state = random.choice(STATES)
        tx_segs.append(EL.join(["N4", city, state, rand_zip()]) + SEG)
        tx_segs.append(EL.join([
            "DMG", "D8", rand_dob(), random.choice(["M", "F"]),
        ]) + SEG)

        # 2010BB — Payer
        payer_id = random.choice(PAYER_IDS)
        tx_segs.append(EL.join([
            "NM1", "PR", "2", "INSTITUTIONAL PAYER",
            "", "", "", "", "PI", payer_id,
        ]) + SEG)

        # 2300 — Claim
        admit_date, discharge_date = rand_date_range(90, 1, 14)
        total_charge = random.uniform(3000.0, 30000.0)
        claim_id = f"INST{claim_idx:06d}"

        if is_inpatient:
            fac = ("11", "A", "1")
        else:
            fac = ("21", "B", "1")

        tx_segs.append(EL.join([
            "CLM", claim_id, f"{total_charge:.2f}", "", "",
            f"{fac[0]}{SUB}{fac[1]}{SUB}{fac[2]}", "Y", "A", "Y", "Y",
        ]) + SEG)

        if is_inpatient:
            # Admission & discharge dates
            tx_segs.append(EL.join(["DTP", "435", "D8", admit_date]) + SEG)
            tx_segs.append(EL.join(["DTP", "096", "D8", discharge_date]) + SEG)

            # CL1 — Institutional claim code
            tx_segs.append(EL.join([
                "CL1",
                random.choice(ADMIT_TYPES),
                random.choice(ADMIT_SOURCES),
                random.choice(DISCHARGE_STATUSES),
            ]) + SEG)
        else:
            tx_segs.append(EL.join(["DTP", "472", "D8", admit_date]) + SEG)

        # Service lines (2-5 per claim)
        num_lines = random.randint(2, 5)
        for line_idx in range(1, num_lines + 1):
            tx_segs.append(EL.join(["LX", str(line_idx)]) + SEG)

            rev_code = random.choice(REVENUE_CODES)
            cpt = random.choice(CPT_CODES)
            line_charge = total_charge / num_lines
            units = random.randint(1, 20)

            tx_segs.append(EL.join([
                "SV2", rev_code, f"HC{SUB}{cpt}",
                f"{line_charge:.2f}", "UN", str(units),
            ]) + SEG)

            if is_inpatient:
                tx_segs.append(EL.join([
                    "DTP", "472", "RD8", f"{admit_date}-{discharge_date}",
                ]) + SEG)
            else:
                tx_segs.append(EL.join([
                    "DTP", "472", "D8", admit_date,
                ]) + SEG)

    # SE
    seg_count = len(tx_segs) + 1
    tx_segs.append(EL.join(["SE", str(seg_count), st_ctrl]) + SEG)

    ge, iea = build_trailers(grp_ctrl, ctrl_num)
    return isa + gs + "".join(tx_segs) + ge + iea


def generate_837i(
    claim_count: int | None = None,
    target_bytes: int | None = None,
) -> str:
    """Generate an 837I Institutional claims file.

    Args:
        claim_count: Number of claims to generate.
        target_bytes: Target file size in bytes (±5%). Overrides claim_count.

    Returns:
        Complete X12 837I interchange as a string.
    """
    random.seed(42)

    if target_bytes is not None:
        return generate_to_size(
            _build_837i_claims,
            target_bytes,
            count_kwarg="claim_count",
        )

    return _build_837i_claims(claim_count or 10)
