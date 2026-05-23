"""NCD/LCD coverage edits.

Two checks:
- **Statutory exclusion** — a procedure that is never covered.
- **Medical necessity** — a procedure that is covered only when billed with a
  diagnosis matching one of the policy's covered ICD-10 prefixes.
"""
from __future__ import annotations

from validation.model import ClaimProjection

from ..model import EditCategory, ScrubFinding, ScrubReport, ScrubSeverity
from ..tables import load_table


def _matches_prefix(dx: str, prefixes: list[str]) -> bool:
    code = dx.replace(".", "").upper()
    for prefix in prefixes:
        if code.startswith(prefix.replace(".", "").upper()):
            return True
    return False


def apply(claims: list[ClaimProjection], report: ScrubReport, **_ctx) -> None:
    table = load_table("coverage")
    excluded = table.get("statutorily_excluded", {})
    medical_necessity = table.get("medical_necessity", {})
    if not excluded and not medical_necessity:
        return
    source = table.get("source", "CMS NCD/LCD")

    for claim in claims:
        diagnoses = [d for d in claim.diagnosis_codes if d]
        for line in claim.service_lines:
            proc = line.procedure_code
            if not proc:
                continue

            if proc in excluded:
                report.add(
                    ScrubFinding(
                        category=EditCategory.COVERAGE,
                        severity=ScrubSeverity.DENY,
                        code="COV.STATUTORY_EXCLUSION",
                        message=(
                            f"Procedure {proc} is statutorily excluded from "
                            f"coverage: {excluded[proc]}"
                        ),
                        claim_id=claim.claim_id,
                        line_no=line.line_no,
                        procedure_code=proc,
                        resolution="Do not bill this procedure to the payer.",
                        source=source,
                    )
                )
                continue

            policy = medical_necessity.get(proc)
            if not policy:
                continue
            prefixes = policy.get("covered_dx_prefixes", [])
            supported = any(_matches_prefix(d, prefixes) for d in diagnoses)
            if not supported:
                report.add(
                    ScrubFinding(
                        category=EditCategory.COVERAGE,
                        severity=ScrubSeverity.REVIEW,
                        code="COV.MEDICAL_NECESSITY",
                        message=(
                            f"Procedure {proc} is not supported by any diagnosis "
                            f"on the claim under coverage policy: "
                            f"{policy.get('policy', 'medical necessity LCD')}."
                        ),
                        claim_id=claim.claim_id,
                        line_no=line.line_no,
                        procedure_code=proc,
                        diagnosis_code=diagnoses[0] if diagnoses else None,
                        resolution=(
                            "Add or correct a diagnosis that establishes medical "
                            "necessity per the coverage policy."
                        ),
                        source=source,
                    )
                )
