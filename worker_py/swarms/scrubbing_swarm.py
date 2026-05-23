"""Supervised scrubbing swarm — review a flagged claim before resubmission.

Given a :class:`ClaimProjection` (from the validation engine) and an optional
:class:`ScrubReport` (from the scrubbing engine) this swarm dispatches four
specialist agents that review different facets of the claim, then a
supervisor renders an APPROVE / FLAG / REJECT verdict with concrete actions
for the claim-edit workflow.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .framework import SwarmAgent, SwarmResult
from .runner import SwarmRunner

if TYPE_CHECKING:
    from scrubbing.model import ScrubReport
    from validation.model import ClaimProjection


def _claim_context(claim: "ClaimProjection", scrub: "ScrubReport | None") -> str:
    lines = [
        f"Claim ID: {claim.claim_id}",
        f"Total charge: {claim.total_charge}",
        f"Place of service: {claim.place_of_service or claim.type_of_bill or '-'}",
        f"Frequency: {claim.claim_frequency_code or '-'}",
        f"Filing indicator: {claim.filing_indicator_code or '-'}",
        f"Billing provider NPI: {claim.billing_provider_npi or '-'}",
        f"Rendering provider NPI: {claim.rendering_provider_npi or '-'}",
        f"Patient: {claim.patient_last_name or '-'}, {claim.patient_first_name or '-'} "
        f"(gender={claim.patient_gender or '-'}, dob={claim.patient_dob or '-'})",
        f"Diagnoses: {', '.join(claim.diagnosis_codes) or '-'}",
        "Service lines:",
    ]
    for line in claim.service_lines:
        lines.append(
            f"  - {line.line_no or '?'}: proc={line.procedure_code} "
            f"charge={line.charge_amount} units={line.units} "
            f"modifiers={line.modifiers or []} dos={line.service_date or '-'}"
        )
    if scrub is not None:
        findings = scrub.findings_for_claim(claim.claim_id or "")
        if findings:
            lines.append("Scrubbing findings:")
            for f in findings:
                lines.append(
                    f"  - [{f.severity.value.upper()}] {f.code}: {f.message}"
                )
    return "\n".join(lines)


_AGENTS = (
    (
        "coding_correctness",
        "Review the diagnosis and procedure codes for plausibility and "
        "internal consistency. Flag any codes that are mismatched, "
        "out-of-date, or implausible together.",
    ),
    (
        "medical_necessity",
        "Evaluate whether each procedure is supported by an appropriate "
        "diagnosis on the claim. Cite the diagnosis you believe supports "
        "(or fails to support) each procedure.",
    ),
    (
        "bundling_ncci",
        "Review NCCI procedure-to-procedure unbundling risk and modifier "
        "usage. For any bundled pair, recommend whether modifier 25, 59 or "
        "X{EPSU} is supportable, or whether the component line should be "
        "removed.",
    ),
    (
        "compliance",
        "Check age / gender / frequency consistency and identify any "
        "compliance or coverage concerns based on the claim context.",
    ),
)


class ScrubbingSwarm:
    """Supervised swarm that reviews a claim flagged by the scrubbing engine."""

    def __init__(self, runner: SwarmRunner) -> None:
        self.runner = runner

    def _build_agents(self, context: str) -> list[SwarmAgent]:
        return [
            SwarmAgent(
                name=name,
                prompt=(
                    f"You are the {name.replace('_', ' ')} agent on a healthcare "
                    f"claim review swarm. {instructions}\n\n"
                    f"Claim context:\n{context}\n\n"
                    "Respond with a concise analysis (under 200 words) ending "
                    "with a single line 'Severity: low | medium | high'."
                ),
                options={"temperature": 0.1, "num_predict": 400},
            )
            for name, instructions in _AGENTS
        ]

    def review(
        self,
        claim: "ClaimProjection",
        scrub: "ScrubReport | None" = None,
    ) -> SwarmResult:
        context = _claim_context(claim, scrub)
        agents = self._build_agents(context)
        return self.runner.run(
            workload="claim_scrubbing",
            agents=agents,
            supervisor_context=(
                "Render a verdict for resubmission readiness. APPROVE means "
                "the claim is safe to submit as-is; FLAG means submit only "
                "after a human reviews specific items; REJECT means hold and "
                "fix before submission."
            ),
        )
