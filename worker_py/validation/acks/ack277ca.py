"""277CA Health Care Claim Acknowledgment generation — ASC X12 005010X214.

The 277CA reports, per claim, whether a submitted 837 claim was accepted into
the adjudication system or rejected, using STC claim-status segments organized
under the standard 2000A/2000B/2000C/2000D hierarchical loops.

Reference: ASC X12N 005010X214 Health Care Claim Acknowledgment (277CA).
"""
from __future__ import annotations

from datetime import datetime

from ..model import ClaimProjection, ValidationReport
from ..parser import X12Document
from .builder import X12Builder, now_stamp

_277_VERSION = "005010X214"


def _claim_status(
    report: ValidationReport, claim: ClaimProjection, comp: str
) -> tuple[str, str]:
    """Return (STC01 composite, free-form message) for one claim."""
    errors = [
        i for i in report.issues_for_claim(claim.claim_id or "") if i.severity.rejects
    ]
    if not errors:
        # A2 = acknowledgement/acceptance into adjudication system; 20 = accepted.
        return f"A2{comp}20", "Claim accepted for processing."

    missing = any("MISSING" in i.code for i in errors)
    if missing:
        # A6 = rejected for missing information; 21 = missing/invalid information.
        category = "A6"
    else:
        # A7 = rejected for invalid information.
        category = "A7"
    return f"{category}{comp}21", errors[0].message


def generate_277ca(
    doc: X12Document,
    report: ValidationReport,
    *,
    now: datetime | None = None,
    control: str = "000000001",
) -> str:
    """Generate a 277CA claim acknowledgment for the claims in ``report``."""
    isa_date, gs_date, time_, time6 = now_stamp(now)
    comp = doc.delimiters.component
    builder = X12Builder(doc.delimiters)

    first = doc.interchanges[0] if doc.interchanges else None
    source_id = (first.receiver_id if first else "") or "TURBOHEDI"
    receiver_id = (first.sender_id if first else "") or "UNKNOWN"
    usage = (first.usage_indicator if first else "") or "P"

    builder.interchange(
        sender=source_id,
        receiver=receiver_id,
        control=control,
        date=isa_date,
        time=time_,
        usage=usage,
    )
    builder.group(
        functional_id="HN",
        sender=source_id,
        receiver=receiver_id,
        date=gs_date,
        time=time_,
        control=control.lstrip("0") or "1",
        version=_277_VERSION,
    )
    builder.transaction(set_code="277", control="0001", version=_277_VERSION)
    builder.add("BHT", "0085", "08", control.lstrip("0") or "1", gs_date, time6, "TH")

    accepted = sum(
        1
        for c in report.claims
        if not any(
            i.severity.rejects for i in report.issues_for_claim(c.claim_id or "")
        )
    )
    rejected = len(report.claims) - accepted

    hl = 0

    # -- Loop 2000A Information Source -------------------------------------
    hl += 1
    source_hl = hl
    builder.add("HL", str(source_hl), "", "20", "1")
    builder.add("NM1", "AY", "2", source_id, "", "", "", "", "46", source_id)

    # -- Loop 2000B Information Receiver -----------------------------------
    hl += 1
    receiver_hl = hl
    builder.add("HL", str(receiver_hl), str(source_hl), "21", "1")
    builder.add("NM1", "41", "2", receiver_id, "", "", "", "", "46", receiver_id)
    builder.add("TRN", "1", control.lstrip("0") or "1")
    builder.add(
        "STC",
        f"A1{comp}19",  # acknowledgement / receipt
        gs_date,
        "WQ",
    )
    builder.add("QTY", "90", str(accepted))   # 90 = quantity accepted
    builder.add("QTY", "AA", str(rejected))   # AA = quantity rejected

    # -- Group claims by billing provider (Loop 2000C) --------------------
    by_provider: dict[tuple[str, str], list[ClaimProjection]] = {}
    order: list[tuple[str, str]] = []
    for claim in report.claims:
        key = (
            claim.billing_provider_npi or "",
            claim.billing_provider_name or "Billing Provider",
        )
        if key not in by_provider:
            by_provider[key] = []
            order.append(key)
        by_provider[key].append(claim)

    for npi, name in order:
        hl += 1
        provider_hl = hl
        builder.add("HL", str(provider_hl), str(receiver_hl), "19", "1")
        if npi:
            builder.add("NM1", "85", "2", name, "", "", "", "", "XX", npi)
        else:
            builder.add("NM1", "85", "2", name)

        for claim in by_provider[(npi, name)]:
            hl += 1
            builder.add("HL", str(hl), str(provider_hl), "PT", "0")
            builder.add(
                "NM1",
                "QC",
                "1",
                claim.patient_last_name or "",
                claim.patient_first_name or "",
            )
            stc01, message = _claim_status(report, claim, comp)
            builder.add("TRN", "2", claim.claim_id or "")
            builder.add(
                "STC",
                stc01,
                gs_date,
                "WQ",
                claim.total_charge or "",
            )
            if claim.claim_id:
                builder.add("REF", "1K", claim.claim_id)
            if claim.patient_control_number:
                builder.add("REF", "D9", claim.patient_control_number)
            # STC free-form note carrying the human-readable status reason.
            builder.add("STC", stc01, gs_date, "WQ", "", "", "", "", "", "", "", message[:255])

    builder.end_transaction()
    builder.end_group()
    builder.end_interchange()
    return builder.render()
