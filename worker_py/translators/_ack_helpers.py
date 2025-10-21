"""Shared helpers for constructing synthetic X12 acknowledgements."""
from __future__ import annotations

import re
import time
import uuid

__all__ = [
    "generate_simple_999",
    "generate_simple_277ca",
]


def _safe_component(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", value.strip())
    return cleaned or fallback


def _control_from_uuid(job_uuid: uuid.UUID, offset: int = 0) -> str:
    base = job_uuid.int % (10 ** 9)
    value = (base + offset) % (10 ** 9)
    return f"{value:09d}"


def generate_simple_999(
    *,
    job_uuid: uuid.UUID,
    trading_partner_id: str | None,
    total_claims: int,
    isa_control: str | None,
    gs_functional_code: str | None,
    st_code: str | None,
) -> str:
    """Return a minimal but well-formed 999 acknowledgement."""

    ctrl = (isa_control or _control_from_uuid(job_uuid))[:9].rjust(9, "0")
    gs_ctrl = _control_from_uuid(job_uuid, 1)
    partner_raw = _safe_component(trading_partner_id, "HEDI-RECV").upper()
    partner_padded = partner_raw[:15].rjust(15)
    app_receiver = partner_raw[:12] or "RECEIVER"
    gs_code = (gs_functional_code or "HC").strip() or "HC"
    st_value = (st_code or "837").strip() or "837"
    count = max(int(total_claims or 0), 1)
    date_short = time.strftime("%y%m%d")
    time_short = time.strftime("%H%M")
    segments = [
        f"ISA*00*          *00*          *ZZ*HEDI999       *ZZ*{partner_padded}*{date_short}*{time_short}*^*00501*{ctrl}*0*T*:~",
        f"GS*FA*HEDI*{app_receiver}*20{date_short}*{time_short}*{gs_ctrl}*X*005010X231A1~",
        "ST*999*0001*005010X231A1~",
        f"AK1*{gs_code}*0001~",
        f"AK2*{st_value}*0001~",
        "AK5*A~",
        f"AK9*A*{count}*{count}*{count}~",
        f"GE*1*{gs_ctrl}~",
        f"IEA*1*{ctrl}~",
    ]
    return "\n".join(segments)


def generate_simple_277ca(
    *,
    job_uuid: uuid.UUID,
    trading_partner_id: str | None,
    total_claims: int,
) -> str:
    """Return a synthetic 277CA acknowledgement."""

    ctrl = _control_from_uuid(job_uuid, 2)
    gs_ctrl = _control_from_uuid(job_uuid, 3)
    partner_raw = _safe_component(trading_partner_id, "HEDI-RECV").upper()
    partner_padded = partner_raw[:15].rjust(15)
    partner_short = partner_raw[:12] or "RECEIVER"
    date_full = time.strftime("%Y%m%d")
    time_short = time.strftime("%H%M")
    count = max(int(total_claims or 0), 1)
    segments = [
        f"ISA*00*          *00*          *ZZ*HEDI277       *ZZ*{partner_padded}*{date_full[2:]}*{time_short}*^*00501*{ctrl}*0*T*:~",
        f"GS*HN*HEDI*{partner_short}*{date_full}*{time_short}*{gs_ctrl}*X*005010X214~",
        "ST*277*0001*005010X214~",
        f"BHT*0085*08*{ctrl}*{date_full}*{time_short}~",
        "HL*1**20*1~",
        "NM1*PR*2*HEDI HEALTH*****PI*HEDI277~",
        "HL*2*1*21*0~",
        f"NM1*41*2*{partner_short or 'RECEIVER'}*****46*{partner_short or 'RECEIVER'}~",
        f"TRN*1*{ctrl}*{partner_short or 'RECEIVER'}~",
        f"STC*A1:19*{date_full}*U*{count}*CLM~",
        "SE*9*0001~",
        f"GE*1*{gs_ctrl}~",
        f"IEA*1*{ctrl}~",
    ]
    return "\n".join(segments)
