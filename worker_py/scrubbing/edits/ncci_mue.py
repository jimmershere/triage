"""NCCI Medically Unlikely Edits (MUE).

An MUE is the maximum units of service a provider would report for a single
HCPCS/CPT code, for a single beneficiary, on a single date of service. Units
billed above the MUE are flagged for review.
"""
from __future__ import annotations

from validation.model import ClaimProjection

from ..model import EditCategory, ScrubFinding, ScrubReport, ScrubSeverity
from ..tables import load_table


def _units(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def apply(claims: list[ClaimProjection], report: ScrubReport, **_ctx) -> None:
    table = load_table("ncci_mue")
    limits = table.get("limits", {})
    if not limits:
        return
    source = table.get("source", "CMS MUE")

    for claim in claims:
        # Aggregate units per (procedure, service date) to catch split lines.
        bucket: dict[tuple[str, str], float] = {}
        first_line: dict[tuple[str, str], str | None] = {}
        for line in claim.service_lines:
            proc = line.procedure_code
            if not proc or proc not in limits:
                continue
            units = _units(line.units)
            if units is None:
                continue
            key = (proc, line.service_date or "")
            bucket[key] = bucket.get(key, 0.0) + units
            first_line.setdefault(key, line.line_no)

        for (proc, dos), total in bucket.items():
            limit = limits[proc]
            if total > limit:
                report.add(
                    ScrubFinding(
                        category=EditCategory.NCCI_MUE,
                        severity=ScrubSeverity.REVIEW,
                        code="MUE.EXCEEDED",
                        message=(
                            f"Procedure {proc} billed with {total:g} unit(s) on "
                            f"{dos or 'the service date'} exceeds the MUE limit "
                            f"of {limit}."
                        ),
                        claim_id=claim.claim_id,
                        line_no=first_line.get((proc, dos)),
                        procedure_code=proc,
                        resolution=(
                            "Reduce units to the MUE limit, or split across "
                            "dates of service with documentation supporting "
                            "medical necessity for the additional units."
                        ),
                        source=source,
                    )
                )
