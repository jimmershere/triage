"""Age and gender appropriateness edits.

Flags procedures and diagnoses that are inconsistent with the patient's
administrative gender or with their age on the date of service.
"""
from __future__ import annotations

from validation.model import ClaimProjection

from ..model import EditCategory, ScrubFinding, ScrubReport, ScrubSeverity, age_on
from ..tables import load_table

_GENDER_LABEL = {"M": "male", "F": "female"}


def apply(claims: list[ClaimProjection], report: ScrubReport, **_ctx) -> None:
    table = load_table("demographic_edits")
    gender_rules = table.get("gender", {})
    age_rules = table.get("age", {})
    proc_gender = gender_rules.get("procedures", {})
    dx_gender = gender_rules.get("diagnosis_prefixes", {})
    proc_age = age_rules.get("procedures", {})
    if not (proc_gender or dx_gender or proc_age):
        return
    source = table.get("source", "CMS age/gender edits")

    for claim in claims:
        gender = (claim.patient_gender or "").upper()

        # --- gender vs procedure ---
        for line in claim.service_lines:
            proc = line.procedure_code
            if proc and proc in proc_gender:
                required = proc_gender[proc]
                if gender and gender != required:
                    report.add(
                        ScrubFinding(
                            category=EditCategory.DEMOGRAPHIC,
                            severity=ScrubSeverity.DENY,
                            code="DEMO.GENDER_PROCEDURE",
                            message=(
                                f"Procedure {proc} is restricted to "
                                f"{_GENDER_LABEL.get(required, required)} "
                                f"patients but the patient gender is "
                                f"{_GENDER_LABEL.get(gender, gender)}."
                            ),
                            claim_id=claim.claim_id,
                            line_no=line.line_no,
                            procedure_code=proc,
                            resolution="Verify patient gender and procedure code.",
                            source=source,
                        )
                    )

        # --- gender vs diagnosis ---
        for dx in claim.diagnosis_codes:
            code = dx.replace(".", "").upper()
            for prefix, required in dx_gender.items():
                if code.startswith(prefix.replace(".", "").upper()):
                    if gender and gender != required:
                        report.add(
                            ScrubFinding(
                                category=EditCategory.DEMOGRAPHIC,
                                severity=ScrubSeverity.REVIEW,
                                code="DEMO.GENDER_DIAGNOSIS",
                                message=(
                                    f"Diagnosis {dx} is gender-specific "
                                    f"({_GENDER_LABEL.get(required, required)}) "
                                    f"but the patient gender is "
                                    f"{_GENDER_LABEL.get(gender, gender)}."
                                ),
                                claim_id=claim.claim_id,
                                procedure_code=None,
                                diagnosis_code=dx,
                                resolution="Verify patient gender and diagnosis.",
                                source=source,
                            )
                        )
                    break

        # --- age vs procedure ---
        for line in claim.service_lines:
            proc = line.procedure_code
            if not proc or proc not in proc_age:
                continue
            rule = proc_age[proc]
            age = age_on(claim.patient_dob, line.service_date)
            if age is None:
                continue
            lo = rule.get("min", 0)
            hi = rule.get("max", 130)
            if not (lo <= age <= hi):
                report.add(
                    ScrubFinding(
                        category=EditCategory.DEMOGRAPHIC,
                        severity=ScrubSeverity.REVIEW,
                        code="DEMO.AGE_PROCEDURE",
                        message=(
                            f"Procedure {proc} ({rule.get('label', 'age-specific')})"
                            f" expects patient age {lo}-{hi} but the patient was "
                            f"{age} on the date of service."
                        ),
                        claim_id=claim.claim_id,
                        line_no=line.line_no,
                        procedure_code=proc,
                        resolution="Verify the patient date of birth and the procedure code.",
                        source=source,
                    )
                )
