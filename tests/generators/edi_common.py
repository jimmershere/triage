"""Shared infrastructure for X12 EDI test file generators.

Provides realistic data pools, random value helpers, ISA/GS envelope
builders, trailer builders, size-targeting wrappers, and multi-part
envelope utilities.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Callable

# ---------------------------------------------------------------------------
# X12 delimiters
# ---------------------------------------------------------------------------
SEG = "~"
EL = "*"
SUB = ":"
REP = "^"

# ---------------------------------------------------------------------------
# Reference date — fixed for reproducibility
# ---------------------------------------------------------------------------
TODAY = date(2026, 5, 15)

# ---------------------------------------------------------------------------
# Realistic data pools
# ---------------------------------------------------------------------------
LAST_NAMES = [
    "SMITH", "JOHNSON", "WILLIAMS", "BROWN", "JONES", "GARCIA",
    "MILLER", "DAVIS", "RODRIGUEZ", "MARTINEZ", "HERNANDEZ",
    "LOPEZ", "GONZALEZ", "WILSON", "ANDERSON", "THOMAS",
    "TAYLOR", "MOORE", "JACKSON", "MARTIN", "LEE", "PEREZ",
    "THOMPSON", "WHITE", "HARRIS",
]
FIRST_NAMES = [
    "JAMES", "MARY", "JOHN", "PATRICIA", "ROBERT", "JENNIFER",
    "MICHAEL", "LINDA", "WILLIAM", "ELIZABETH", "DAVID", "BARBARA",
    "RICHARD", "SUSAN", "JOSEPH", "JESSICA", "THOMAS", "SARAH",
    "CHARLES", "KAREN", "DANIEL", "NANCY", "MATTHEW", "LISA",
]
STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "MA", "MD",
    "MI", "MN", "MO", "NC", "NJ", "NM", "NY", "OH", "OK", "OR",
    "PA", "SC", "TN", "TX", "VA",
]
CITIES = [
    "SPRINGFIELD", "RIVERSIDE", "FRANKLIN", "GREENVILLE", "MADISON",
    "CLINTON", "SALEM", "FAIRVIEW", "BRISTOL", "OXFORD",
    "JACKSONVILLE", "ARLINGTON", "WILMINGTON", "LANCASTER", "DAYTON",
    "BOULDER", "AURORA", "LAKEWOOD", "DENVER", "MESA",
]
DIAG_CODES = [
    "J0190", "K5789", "E119", "I10", "M5416", "G4700", "J069",
    "R0600", "N390", "Z0000", "F329", "J449", "E785", "Z23",
    "R1013", "M7910", "L309", "H1010", "B349", "R05",
    "S72001A", "T8130XA",
]
CPT_CODES = [
    "99213", "99214", "99215", "99203", "99204", "99205",
    "99211", "99212", "99232", "99233", "99281", "99282",
    "99283", "99284", "99285", "99291", "90837", "90834",
    "90847", "96372",
]
ADA_CODES = [
    "D0120", "D1110", "D2140", "D2150", "D2160", "D2330",
    "D2331", "D2391", "D2392", "D7140", "D0210", "D0274",
]
REVENUE_CODES = [
    "0120", "0250", "0301", "0510", "0636", "0450",
    "0260", "0320", "0370", "0710", "0730",
]
PAYER_IDS = [
    "AETNA001", "BCBS0002", "CIGNA003", "HUMANA04", "UNITED05",
    "ANTHEM06", "KAISER07", "MOLINA08", "CENTENE9", "TRICAR10",
    "MEDICD11", "MEDICAR1",
]
HOSPITAL_NAMES = [
    "MERCY GENERAL HOSPITAL", "ST JOSEPH MEDICAL CENTER",
    "CITY REGIONAL HOSPITAL", "MOUNTAIN VIEW MEDICAL CENTER",
    "VALLEY HEALTH SYSTEM", "COMMUNITY GENERAL HOSPITAL",
    "UNIVERSITY MEDICAL CENTER", "MEMORIAL HOSPITAL",
    "LAKESIDE MEDICAL CENTER", "RIVERSIDE COMMUNITY HOSPITAL",
]

# ---------------------------------------------------------------------------
# Random value helpers
# ---------------------------------------------------------------------------

def rand_npi() -> str:
    """Generate a 10-digit NPI-like number starting with 1."""
    return "1" + "".join(str(random.randint(0, 9)) for _ in range(9))


def rand_ein() -> str:
    """Generate a 9-digit EIN."""
    return "".join(str(random.randint(0, 9)) for _ in range(9))


def rand_member_id() -> str:
    """Generate a member ID like A12345678."""
    letter = random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    return letter + "".join(str(random.randint(0, 9)) for _ in range(8))


def rand_zip() -> str:
    """Generate a 5-digit ZIP code."""
    return str(random.randint(10000, 99999))


def rand_date(days_back: int = 365) -> str:
    """Generate a date string YYYYMMDD within days_back of TODAY."""
    d = TODAY - timedelta(days=random.randint(1, days_back))
    return d.strftime("%Y%m%d")


def rand_dob() -> str:
    """Generate a date of birth 20-80 years ago."""
    d = TODAY - timedelta(days=random.randint(7300, 29200))
    return d.strftime("%Y%m%d")


def rand_amount(lo: float = 50.0, hi: float = 500.0) -> str:
    """Generate a monetary amount string."""
    return f"{random.uniform(lo, hi):.2f}"


def rand_date_range(days_back: int = 90, span_min: int = 1, span_max: int = 14) -> tuple[str, str]:
    """Generate an admit/discharge date range as (start, end) in YYYYMMDD."""
    start = TODAY - timedelta(days=random.randint(1, days_back))
    end = start + timedelta(days=random.randint(span_min, span_max))
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# Envelope builders
# ---------------------------------------------------------------------------

def build_isa(
    sender_id: str,
    receiver_id: str,
    version: str = "00501",
    functional_id_code: str = "HC",
    implementation_version: str = "005010X222A1",
    ctrl_num: str = "000000001",
    grp_ctrl: str = "1",
) -> tuple[str, str, str, str]:
    """Build ISA and GS prefix segments.

    Returns (isa_segment, gs_segment, ctrl_num, grp_ctrl).
    """
    isa = EL.join([
        "ISA", "00", " " * 10, "00", " " * 10,
        "ZZ", f"{sender_id:<15}",
        "ZZ", f"{receiver_id:<15}",
        TODAY.strftime("%y%m%d"), TODAY.strftime("%H%M"),
        REP, version, f"{ctrl_num:>9}", "0", "T", SUB,
    ]) + SEG

    gs = EL.join([
        "GS", functional_id_code, sender_id, receiver_id,
        TODAY.strftime("%Y%m%d"), TODAY.strftime("%H%M"),
        grp_ctrl, "X", implementation_version,
    ]) + SEG

    return isa, gs, ctrl_num, grp_ctrl


def build_trailers(
    grp_ctrl: str,
    interchange_ctrl: str,
    tx_count: int = 1,
    seg_count: int | None = None,
) -> tuple[str, str]:
    """Build GE and IEA trailer segments.

    Returns (ge_segment, iea_segment).
    """
    ge = EL.join(["GE", str(tx_count), grp_ctrl]) + SEG
    iea = EL.join(["IEA", "1", interchange_ctrl]) + SEG
    return ge, iea


# ---------------------------------------------------------------------------
# Multi-part and batched envelope utilities
# ---------------------------------------------------------------------------

def wrap_multi_gs(
    transactions_list: list[str],
    sender_id: str = "SENDER123",
    receiver_id: str = "RECEIVER456",
    version: str = "00501",
    functional_id_code: str = "HC",
    implementation_version: str = "005010X222A1",
) -> str:
    """Wrap multiple ST..SE transaction sets in separate GS groups within one ISA.

    Each item in transactions_list should be a string containing ST..SE segments.
    """
    ctrl_num = "000000001"
    isa = EL.join([
        "ISA", "00", " " * 10, "00", " " * 10,
        "ZZ", f"{sender_id:<15}",
        "ZZ", f"{receiver_id:<15}",
        TODAY.strftime("%y%m%d"), TODAY.strftime("%H%M"),
        REP, version, f"{ctrl_num:>9}", "0", "T", SUB,
    ]) + SEG

    parts = [isa]
    for i, tx_content in enumerate(transactions_list, 1):
        grp_ctrl = str(i)
        gs = EL.join([
            "GS", functional_id_code, sender_id, receiver_id,
            TODAY.strftime("%Y%m%d"), TODAY.strftime("%H%M"),
            grp_ctrl, "X", implementation_version,
        ]) + SEG
        ge = EL.join(["GE", "1", grp_ctrl]) + SEG
        parts.append(gs)
        parts.append(tx_content)
        parts.append(ge)

    iea = EL.join(["IEA", str(len(transactions_list)), ctrl_num]) + SEG
    parts.append(iea)
    return "".join(parts)


def build_batched_isa(file_contents_list: list[str]) -> str:
    """Concatenate multiple complete ISA..IEA interchanges."""
    return "".join(file_contents_list)


# ---------------------------------------------------------------------------
# Size-targeting wrapper
# ---------------------------------------------------------------------------

def generate_to_size(
    generator_func: Callable[..., str],
    target_bytes: int,
    count_kwarg: str = "claim_count",
    **kwargs,
) -> str:
    """Iteratively call generator_func increasing count until output reaches target_bytes (±5%).

    Args:
        generator_func: A generator function that accepts a count keyword.
        target_bytes: Target output size in bytes.
        count_kwarg: The keyword argument name for the item count.
        **kwargs: Additional keyword arguments passed to generator_func.
    """
    lo_bound = target_bytes * 0.95
    hi_bound = target_bytes * 1.05

    # Estimate bytes per item with a small sample
    sample_kwargs = {count_kwarg: 5, **kwargs}
    sample = generator_func(**sample_kwargs)
    sample_bytes = len(sample.encode())

    # Estimate envelope overhead and per-item size
    single_kwargs = {count_kwarg: 1, **kwargs}
    single = generator_func(**single_kwargs)
    single_bytes = len(single.encode())
    overhead = single_bytes
    per_item = max((sample_bytes - overhead) / 4, 100)  # 5 items - 1 = 4 incremental

    # Initial estimate
    count = max(1, int((target_bytes - overhead) / per_item))

    for _ in range(20):  # max iterations to converge
        result_kwargs = {count_kwarg: count, **kwargs}
        result = generator_func(**result_kwargs)
        result_bytes = len(result.encode())

        if lo_bound <= result_bytes <= hi_bound:
            return result

        # Adjust count proportionally
        ratio = target_bytes / max(result_bytes, 1)
        new_count = max(1, int(count * ratio))
        if new_count == count:
            new_count = count + (1 if result_bytes < target_bytes else -1)
        if new_count < 1:
            break
        count = new_count

    # Return best effort
    return result
