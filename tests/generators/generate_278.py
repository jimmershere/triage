"""Generate X12 278 (Prior Authorization) — 005010X217.

Produces prior authorization request/response transactions with UM
(health care services review), HCR, and SV1/SV2 service detail segments.
"""
from __future__ import annotations

import random

from .edi_common import (
    SEG, EL, SUB,
    LAST_NAMES, FIRST_NAMES, STATES, CITIES, PAYER_IDS,
    DIAG_CODES, CPT_CODES, REVENUE_CODES,
    rand_npi, rand_member_id, rand_zip, rand_date, rand_dob,
    rand_amount, build_isa, build_trailers, generate_to_size,
)

IMPLEMENTATION_VERSION = "005010X217"

# UM01 — Request category codes
REQUEST_CATEGORIES = ["AR", "HS", "SC"]  # AR=Admission Review, HS=Health Services, SC=Specialty Care

# UM02 — Certification type codes
CERT_TYPES = ["I", "R", "E", "S"]  # Initial, Renewal, Extension, Second opinion

# UM04 — Service type codes for auth
AUTH_SERVICE_TYPES = ["2", "3", "4", "8", "14", "42", "47", "73"]

# HCR01 — Action codes
ACTION_CODES = ["A1", "A2", "A3", "A4", "A6", "CT"]  # Certified, Not certified, etc.

# Place of service
POS_CODES = ["11", "21", "22", "23", "31", "32"]


def _build_278_auths(auth_count: int) -> str:
    """Build a complete 278 interchange with the given number of authorizations."""
    sender_id = "AUTHREQ278"
    receiver_id = "AUTHRESP278"

    isa, gs, ctrl_num, grp_ctrl = build_isa(
        sender_id, receiver_id,
        functional_id_code="HI",
        implementation_version=IMPLEMENTATION_VERSION,
    )

    st_ctrl = "0001"
    tx_segs: list[str] = []

    # ST
    tx_segs.append(EL.join(["ST", "278", st_ctrl, IMPLEMENTATION_VERSION]) + SEG)

    # BHT
    tx_segs.append(EL.join([
        "BHT", "0007", "11", "AUTH0001",
        "20260515", "1100",
    ]) + SEG)

    # HL 1 — Utilization Management Organization (Payer)
    hl_id = 1
    payer_hl = hl_id
    tx_segs.append(EL.join(["HL", str(hl_id), "", "20", "1"]) + SEG)
    payer_id = random.choice(PAYER_IDS)
    tx_segs.append(EL.join([
        "NM1", "PR", "2", "AUTHORIZATION PAYER",
        "", "", "", "", "PI", payer_id,
    ]) + SEG)

    # HL 2 — Requester (Provider)
    hl_id += 1
    provider_hl = hl_id
    provider_npi = rand_npi()
    tx_segs.append(EL.join([
        "HL", str(hl_id), str(payer_hl), "21", "1",
    ]) + SEG)
    tx_segs.append(EL.join([
        "NM1", "1P", "2", "REQUESTING PROVIDER",
        "", "", "", "", "XX", provider_npi,
    ]) + SEG)

    for idx in range(1, auth_count + 1):
        hl_id += 1

        # HL — Subscriber
        tx_segs.append(EL.join([
            "HL", str(hl_id), str(provider_hl), "22", "1",
        ]) + SEG)

        # TRN — trace
        tx_segs.append(EL.join([
            "TRN", "1", f"AUTH{idx:06d}", rand_npi(),
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
            "N3", f"{random.randint(1, 9999)} {random.choice(['OAK', 'ELM', 'PINE'])} ST",
        ]) + SEG)
        tx_segs.append(EL.join([
            "N4", random.choice(CITIES), random.choice(STATES), rand_zip(),
        ]) + SEG)

        # DMG
        tx_segs.append(EL.join([
            "DMG", "D8", rand_dob(), random.choice(["M", "F"]),
        ]) + SEG)

        # UM — Health Care Services Review
        req_cat = random.choice(REQUEST_CATEGORIES)
        cert_type = random.choice(CERT_TYPES)
        svc_type = random.choice(AUTH_SERVICE_TYPES)
        pos = random.choice(POS_CODES)
        tx_segs.append(EL.join([
            "UM", req_cat, cert_type, "", pos,
        ]) + SEG)

        # HCR — Health Care Services Review Decision
        action = random.choice(ACTION_CODES)
        tx_segs.append(EL.join([
            "HCR", action, f"CERT{idx:06d}",
        ]) + SEG)

        # HI — diagnosis
        diag = random.choice(DIAG_CODES)
        tx_segs.append(EL.join([
            "HI", f"ABK{SUB}{diag}",
        ]) + SEG)

        # DTP — requested service dates
        svc_date = rand_date(30)
        tx_segs.append(EL.join([
            "DTP", "472", "D8", svc_date,
        ]) + SEG)

        # Service details (1-3 per auth)
        num_services = random.randint(1, 3)
        is_institutional = random.random() < 0.4

        for svc_idx in range(1, num_services + 1):
            if is_institutional:
                rev_code = random.choice(REVENUE_CODES)
                cpt = random.choice(CPT_CODES)
                charge = random.uniform(500.0, 5000.0)
                units = random.randint(1, 10)
                tx_segs.append(EL.join([
                    "SV2", rev_code, f"HC{SUB}{cpt}",
                    f"{charge:.2f}", "UN", str(units),
                ]) + SEG)
            else:
                cpt = random.choice(CPT_CODES)
                charge = random.uniform(100.0, 2000.0)
                tx_segs.append(EL.join([
                    "SV1", f"HC{SUB}{cpt}",
                    f"{charge:.2f}", "UN", "1", "", "",
                    f"1{SUB}2",
                ]) + SEG)

    # SE
    seg_count = len(tx_segs) + 1
    tx_segs.append(EL.join(["SE", str(seg_count), st_ctrl]) + SEG)

    ge, iea = build_trailers(grp_ctrl, ctrl_num)
    return isa + gs + "".join(tx_segs) + ge + iea


def generate_278(
    auth_count: int | None = None,
    target_bytes: int | None = None,
) -> str:
    """Generate a 278 Prior Authorization file.

    Args:
        auth_count: Number of authorization requests to generate.
        target_bytes: Target file size in bytes (±5%). Overrides auth_count.

    Returns:
        Complete X12 278 interchange as a string.
    """
    random.seed(42)

    if target_bytes is not None:
        return generate_to_size(
            _build_278_auths,
            target_bytes,
            count_kwarg="auth_count",
        )

    return _build_278_auths(auth_count or 10)
