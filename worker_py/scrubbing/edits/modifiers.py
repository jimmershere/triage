"""Procedure modifier validation edits.

Checks that every modifier on a service line is a recognized CPT/HCPCS Level I
or Level II modifier, and flags structurally invalid modifier values.
"""
from __future__ import annotations

import re

from validation.model import ClaimProjection

from ..model import EditCategory, ScrubFinding, ScrubReport, ScrubSeverity

# Recognized CPT/HCPCS modifiers (curated common subset). A modifier is two
# characters: CPT modifiers are numeric, HCPCS Level II modifiers alphanumeric.
_KNOWN_MODIFIERS = {
    # CPT modifiers
    "22", "23", "24", "25", "26", "27", "32", "33", "47", "50", "51", "52",
    "53", "54", "55", "56", "57", "58", "59", "62", "63", "66", "76", "77",
    "78", "79", "80", "81", "82", "90", "91", "92", "95", "96", "97", "99",
    # HCPCS Level II modifiers
    "AA", "AD", "AS", "CR", "GA", "GC", "GN", "GO", "GP", "GT", "GY", "GZ",
    "KX", "LT", "RT", "QW", "TC", "XE", "XS", "XP", "XU",
    # Anatomic finger / toe / eyelid / coronary modifiers
    "E1", "E2", "E3", "E4", "FA", "F1", "F2", "F3", "F4", "F5", "F6", "F7",
    "F8", "F9", "TA", "T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9",
    "LC", "LD", "RC", "LM", "RI",
}

_MODIFIER_RE = re.compile(r"^[0-9A-Z]{2}$")


def apply(claims: list[ClaimProjection], report: ScrubReport, **_ctx) -> None:
    for claim in claims:
        for line in claim.service_lines:
            for modifier in line.modifiers:
                mod = (modifier or "").strip().upper()
                if not mod:
                    continue
                if not _MODIFIER_RE.match(mod):
                    report.add(
                        ScrubFinding(
                            category=EditCategory.MODIFIER,
                            severity=ScrubSeverity.REVIEW,
                            code="MOD.MALFORMED",
                            message=(
                                f"Modifier '{modifier}' on procedure "
                                f"{line.procedure_code} is not a valid two-"
                                "character modifier."
                            ),
                            claim_id=claim.claim_id,
                            line_no=line.line_no,
                            procedure_code=line.procedure_code,
                            resolution="Correct or remove the modifier.",
                            source="CPT/HCPCS modifier set",
                        )
                    )
                elif mod not in _KNOWN_MODIFIERS:
                    report.add(
                        ScrubFinding(
                            category=EditCategory.MODIFIER,
                            severity=ScrubSeverity.ADVISORY,
                            code="MOD.UNRECOGNIZED",
                            message=(
                                f"Modifier '{mod}' on procedure "
                                f"{line.procedure_code} is not in the recognized "
                                "modifier set; verify it against the current "
                                "CPT/HCPCS modifier list."
                            ),
                            claim_id=claim.claim_id,
                            line_no=line.line_no,
                            procedure_code=line.procedure_code,
                            source="CPT/HCPCS modifier set",
                        )
                    )
