"""NCCI Procedure-to-Procedure (PTP) unbundling edits.

When two procedure codes on the same claim form a PTP pair, the column-2
(component) code is bundled into the column-1 (comprehensive) code and is not
separately payable — unless an NCCI-associated modifier is appended and the
pair's modifier indicator permits it.
"""
from __future__ import annotations

from validation.model import ClaimProjection

from ..model import EditCategory, ScrubFinding, ScrubReport, ScrubSeverity
from ..tables import load_table

# NCCI-associated modifiers that may bypass a modifier-indicator-1 PTP edit.
_OVERRIDE_MODIFIERS = {"25", "59", "XE", "XS", "XP", "XU"}


def apply(claims: list[ClaimProjection], report: ScrubReport, **_ctx) -> None:
    table = load_table("ncci_ptp")
    pairs = table.get("pairs", [])
    if not pairs:
        return

    # Index: (column1, column2) -> modifier_indicator.
    index: dict[tuple[str, str], str] = {
        (p["column1"], p["column2"]): str(p.get("modifier_indicator", "1"))
        for p in pairs
    }
    source = table.get("source", "CMS NCCI PTP")

    for claim in claims:
        lines = [ln for ln in claim.service_lines if ln.procedure_code]
        for i, comprehensive in enumerate(lines):
            for component in lines:
                if component is comprehensive:
                    continue
                key = (comprehensive.procedure_code, component.procedure_code)
                if key not in index:
                    continue
                indicator = index[key]
                has_override = bool(
                    _OVERRIDE_MODIFIERS.intersection(component.modifiers)
                )
                if indicator == "1" and has_override:
                    report.add(
                        ScrubFinding(
                            category=EditCategory.NCCI_PTP,
                            severity=ScrubSeverity.ADVISORY,
                            code="PTP.BYPASSED",
                            message=(
                                f"Procedure {component.procedure_code} is a PTP "
                                f"component of {comprehensive.procedure_code}; an "
                                "NCCI-associated modifier is present, so the edit "
                                "is bypassed."
                            ),
                            claim_id=claim.claim_id,
                            line_no=component.line_no,
                            procedure_code=component.procedure_code,
                            related_code=comprehensive.procedure_code,
                            source=source,
                        )
                    )
                elif indicator == "1":
                    report.add(
                        ScrubFinding(
                            category=EditCategory.NCCI_PTP,
                            severity=ScrubSeverity.REVIEW,
                            code="PTP.BUNDLED",
                            message=(
                                f"Procedure {component.procedure_code} is bundled "
                                f"into {comprehensive.procedure_code} by an NCCI "
                                "PTP edit and is not separately payable as billed."
                            ),
                            claim_id=claim.claim_id,
                            line_no=component.line_no,
                            procedure_code=component.procedure_code,
                            related_code=comprehensive.procedure_code,
                            resolution=(
                                "Append an NCCI-associated modifier (25 for a "
                                "separately identifiable E/M, 59 or XE/XS/XP/XU "
                                "for a distinct service) if clinically supported, "
                                "otherwise remove the component line."
                            ),
                            source=source,
                        )
                    )
                else:  # modifier indicator 0 — cannot be bypassed.
                    report.add(
                        ScrubFinding(
                            category=EditCategory.NCCI_PTP,
                            severity=ScrubSeverity.DENY,
                            code="PTP.NOT_PAYABLE",
                            message=(
                                f"Procedure {component.procedure_code} is bundled "
                                f"into {comprehensive.procedure_code} by an NCCI "
                                "PTP edit with modifier indicator 0; it is never "
                                "separately payable."
                            ),
                            claim_id=claim.claim_id,
                            line_no=component.line_no,
                            procedure_code=component.procedure_code,
                            related_code=comprehensive.procedure_code,
                            resolution="Remove the component line; it cannot be unbundled.",
                            source=source,
                        )
                    )
