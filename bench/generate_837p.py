"""Generate realistic X12 837P (Professional) test files for benchmarking.

Produces clean, structurally valid 005010X222A1 files that should score
Tier 0 (deterministic fast path) in TurboHEDI's routing engine.

Usage:
    python generate_837p.py <claim_count> <output_path>
"""
from __future__ import annotations

import random
import sys
from datetime import date, timedelta

SEG = "~"
EL = "*"
SUB = ":"

# Realistic data pools
DIAG_CODES = [
    "J0190", "K5789", "E119", "I10", "M5416", "G4700", "J069",
    "R0600", "N390", "Z0000", "F329", "J449", "E785", "Z23",
    "R1013", "M7910", "L309", "H1010", "B349", "R05",
]
CPT_CODES = [
    "99213", "99214", "99215", "99203", "99204", "99205",
    "99211", "99212", "99232", "99233", "99281", "99282",
    "99283", "99284", "99285", "99291", "90837", "90834",
    "90847", "96372",
]
LAST_NAMES = [
    "SMITH", "JOHNSON", "WILLIAMS", "BROWN", "JONES", "GARCIA",
    "MILLER", "DAVIS", "RODRIGUEZ", "MARTINEZ", "HERNANDEZ",
    "LOPEZ", "GONZALEZ", "WILSON", "ANDERSON", "THOMAS",
    "TAYLOR", "MOORE", "JACKSON", "MARTIN",
]
FIRST_NAMES = [
    "JAMES", "MARY", "JOHN", "PATRICIA", "ROBERT", "JENNIFER",
    "MICHAEL", "LINDA", "WILLIAM", "ELIZABETH", "DAVID", "BARBARA",
    "RICHARD", "SUSAN", "JOSEPH", "JESSICA", "THOMAS", "SARAH",
    "CHARLES", "KAREN",
]
STATES = [
    "AL", "AK", "AZ", "CA", "CO", "CT", "FL", "GA", "IL", "IN",
    "KS", "KY", "LA", "MA", "MD", "MI", "MN", "MO", "NC", "NJ",
    "NM", "NY", "OH", "OK", "OR", "PA", "SC", "TN", "TX", "VA",
]
CITIES = [
    "SPRINGFIELD", "RIVERSIDE", "FRANKLIN", "GREENVILLE", "MADISON",
    "CLINTON", "SALEM", "FAIRVIEW", "BRISTOL", "OXFORD",
    "JACKSONVILLE", "ARLINGTON", "WILMINGTON", "LANCASTER", "DAYTON",
]

TODAY = date(2026, 5, 15)
random.seed(42)  # Reproducible


def rand_npi() -> str:
    """Generate a 10-digit NPI-like number starting with 1."""
    return "1" + "".join(str(random.randint(0, 9)) for _ in range(9))


def rand_ein() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(9))


def rand_member_id() -> str:
    letter = random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    return letter + "".join(str(random.randint(0, 9)) for _ in range(8))


def rand_zip() -> str:
    return str(random.randint(10000, 99999))


def rand_date(days_back: int = 365) -> str:
    d = TODAY - timedelta(days=random.randint(1, days_back))
    return d.strftime("%Y%m%d")


def rand_dob() -> str:
    d = TODAY - timedelta(days=random.randint(7300, 29200))  # 20-80 years
    return d.strftime("%Y%m%d")


def rand_amount(lo: float = 50.0, hi: float = 500.0) -> str:
    return f"{random.uniform(lo, hi):.2f}"


def build_837p(claim_count: int) -> str:
    segs: list[str] = []

    ctrl_num = "000000001"
    grp_ctrl = "1"
    st_ctrl = "0001"

    billing_npi = rand_npi()
    billing_ein = rand_ein()
    sender_id = "SENDER123"
    receiver_id = "RECEIVER456"

    # ISA
    segs.append(
        EL.join([
            "ISA", "00", " " * 10, "00", " " * 10,
            "ZZ", f"{sender_id:<15}",
            "ZZ", f"{receiver_id:<15}",
            TODAY.strftime("%y%m%d"), TODAY.strftime("%H%M"),
            "^", "00501", f"{ctrl_num:>9}", "0", "T", ":"
        ]) + SEG
    )

    # GS — functional group for 837 healthcare claims
    segs.append(
        EL.join([
            "GS", "HC", sender_id, receiver_id,
            TODAY.strftime("%Y%m%d"), TODAY.strftime("%H%M"),
            grp_ctrl, "X", "005010X222A1"
        ]) + SEG
    )

    # ST — transaction set header (version in element 3 is key for routing)
    tx_segs: list[str] = []
    tx_segs.append(EL.join(["ST", "837", st_ctrl, "005010X222A1"]) + SEG)

    # BHT — beginning of hierarchical transaction
    tx_segs.append(
        EL.join(["BHT", "0019", "00", "BENCH0001",
                 TODAY.strftime("%Y%m%d"), "1200", "CH"]) + SEG
    )

    # 1000A — Submitter
    tx_segs.append(
        EL.join(["NM1", "41", "2", "BENCHMARK MEDICAL GROUP",
                 "", "", "", "", "46", sender_id]) + SEG
    )
    tx_segs.append(
        EL.join(["PER", "IC", "EDI DEPARTMENT", "TE", "5551234567"]) + SEG
    )

    # 1000B — Receiver
    tx_segs.append(
        EL.join(["NM1", "40", "2", "BENCHMARK INSURANCE CO",
                 "", "", "", "", "46", receiver_id]) + SEG
    )

    # HL counter
    hl_id = 0

    # 2000A — Billing Provider Hierarchical Level
    hl_id += 1
    billing_hl = hl_id
    tx_segs.append(EL.join(["HL", str(hl_id), "", "20", "1"]) + SEG)

    # 2010AA — Billing Provider Name
    tx_segs.append(
        EL.join(["NM1", "85", "2", "BENCHMARK MEDICAL GROUP",
                 "", "", "", "", "XX", billing_npi]) + SEG
    )
    tx_segs.append(EL.join(["N3", "100 MAIN STREET"]) + SEG)
    tx_segs.append(EL.join(["N4", "SPRINGFIELD", "IL", "62701"]) + SEG)
    tx_segs.append(EL.join(["REF", "EI", billing_ein]) + SEG)

    # Generate claims — each subscriber gets one claim (simplest valid structure)
    for claim_idx in range(1, claim_count + 1):
        # 2000B — Subscriber Hierarchical Level
        hl_id += 1
        tx_segs.append(
            EL.join(["HL", str(hl_id), str(billing_hl), "22", "0"]) + SEG
        )

        # SBR — Subscriber Information
        tx_segs.append(
            EL.join(["SBR", "P", "18", "", "", "", "", "", "", "MC"]) + SEG
        )

        # 2010BA — Subscriber Name
        last = random.choice(LAST_NAMES)
        first = random.choice(FIRST_NAMES)
        member_id = rand_member_id()
        tx_segs.append(
            EL.join(["NM1", "IL", "1", last, first,
                     "", "", "", "MI", member_id]) + SEG
        )
        tx_segs.append(
            EL.join(["N3", f"{random.randint(1, 9999)} {random.choice(['OAK', 'ELM', 'PINE', 'MAPLE', 'CEDAR'])} ST"]) + SEG
        )
        st = random.choice(STATES)
        tx_segs.append(
            EL.join(["N4", random.choice(CITIES), st, rand_zip()]) + SEG
        )
        tx_segs.append(
            EL.join(["DMG", "D8", rand_dob(), random.choice(["M", "F"])]) + SEG
        )

        # 2010BB — Payer Name
        tx_segs.append(
            EL.join(["NM1", "PR", "2", "BENCHMARK INSURANCE CO",
                     "", "", "", "", "PI", "BENCHPAYER01"]) + SEG
        )

        # 2300 — Claim Information
        charge = random.uniform(75.0, 750.0)
        claim_id = f"CLM{claim_idx:07d}"
        tx_segs.append(
            EL.join(["CLM", claim_id, f"{charge:.2f}", "", "",
                     f"11{SUB}B{SUB}1", "Y", "A", "Y", "I"]) + SEG
        )

        # DTP — service dates
        svc_date = rand_date(90)
        tx_segs.append(EL.join(["DTP", "431", "D8", svc_date]) + SEG)

        # HI — diagnosis codes (1-3 per claim)
        num_diag = random.randint(1, 3)
        diags = random.sample(DIAG_CODES, num_diag)
        hi_elements = ["HI"]
        for i, dx in enumerate(diags):
            qualifier = "ABK" if i == 0 else "ABF"
            hi_elements.append(f"{qualifier}{SUB}{dx}")
        tx_segs.append(EL.join(hi_elements) + SEG)

        # 2400 — Service Lines (1-3 per claim)
        num_lines = random.randint(1, 3)
        for line_idx in range(1, num_lines + 1):
            tx_segs.append(EL.join(["LX", str(line_idx)]) + SEG)

            cpt = random.choice(CPT_CODES)
            line_charge = charge / num_lines
            tx_segs.append(
                EL.join(["SV1", f"HC{SUB}{cpt}",
                         f"{line_charge:.2f}", "UN", "1", "", "",
                         f"1{SUB}2{SUB}3"]) + SEG
            )
            tx_segs.append(
                EL.join(["DTP", "472", "D8", svc_date]) + SEG
            )

    # SE — transaction set trailer
    seg_count = len(tx_segs) + 1  # +1 for SE itself
    tx_segs.append(EL.join(["SE", str(seg_count), st_ctrl]) + SEG)

    segs.extend(tx_segs)

    # GE / IEA
    segs.append(EL.join(["GE", "1", grp_ctrl]) + SEG)
    segs.append(EL.join(["IEA", "1", ctrl_num]) + SEG)

    return "".join(segs)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <claim_count> <output_path>")
        sys.exit(1)

    count = int(sys.argv[1])
    outpath = sys.argv[2]

    content = build_837p(count)
    with open(outpath, "w") as f:
        f.write(content)

    size_kb = len(content.encode()) / 1024
    seg_count = content.count("~")
    print(f"Generated {outpath}: {count} claims, {seg_count} segments, {size_kb:.1f} KB")
