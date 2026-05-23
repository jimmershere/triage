"""Supervised denial-resolution swarm.

Given a denied claim (typically reconstructed from an 835 CLP/CAS or a 277CA
STC) this swarm fields four specialists who together produce a remediation
plan: what the denial means, what to correct, what documentation is needed,
and a draft appeal narrative if the denial is contested.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .framework import SwarmAgent, SwarmResult
from .runner import SwarmRunner

if TYPE_CHECKING:
    from validation.model import ClaimProjection


def _denial_context(
    claim: "ClaimProjection",
    *,
    denial_codes: list[str],
    denial_messages: list[str] | None = None,
) -> str:
    msgs = denial_messages or []
    pairs = "\n".join(
        f"  - {code}: {msgs[i] if i < len(msgs) else '(no message)'}"
        for i, code in enumerate(denial_codes)
    ) or "  (none provided)"
    lines = [
        f"Claim ID: {claim.claim_id}",
        f"Total charge: {claim.total_charge}",
        f"Billing provider NPI: {claim.billing_provider_npi or '-'}",
        f"Patient: {claim.patient_last_name or '-'}, {claim.patient_first_name or '-'}",
        f"Diagnoses: {', '.join(claim.diagnosis_codes) or '-'}",
        "Service lines:",
    ]
    for line in claim.service_lines:
        lines.append(
            f"  - {line.line_no or '?'}: proc={line.procedure_code} "
            f"charge={line.charge_amount} units={line.units} "
            f"modifiers={line.modifiers or []}"
        )
    lines.append("Denial codes (CARC / RARC / STC):")
    lines.append(pairs)
    return "\n".join(lines)


_AGENTS = (
    (
        "reason_interpretation",
        "Explain in plain language what each denial code means and which "
        "claim element it points to.",
    ),
    (
        "fix_proposal",
        "Propose a concrete edit to the claim that would resolve each "
        "denial. Be specific: which segment, which field, which new value.",
    ),
    (
        "documentation",
        "Identify clinical or administrative documentation that would "
        "support the proposed fixes during a payer audit.",
    ),
    (
        "appeal_narrative",
        "Draft a short (5-8 sentence) appeal narrative that the provider "
        "could send to the payer if the denial is unjust.",
    ),
)


class DenialResolutionSwarm:
    """Supervised swarm that produces a denial-resolution plan."""

    def __init__(self, runner: SwarmRunner) -> None:
        self.runner = runner

    def _build_agents(self, context: str) -> list[SwarmAgent]:
        return [
            SwarmAgent(
                name=name,
                prompt=(
                    f"You are the {name.replace('_', ' ')} agent on a "
                    "healthcare-claim denial-resolution swarm. "
                    f"{instructions}\n\n"
                    f"Case context:\n{context}\n\n"
                    "Keep the response under 200 words and avoid PHI."
                ),
                options={"temperature": 0.15, "num_predict": 400},
            )
            for name, instructions in _AGENTS
        ]

    def resolve(
        self,
        claim: "ClaimProjection",
        denial_codes: list[str],
        denial_messages: list[str] | None = None,
    ) -> SwarmResult:
        context = _denial_context(
            claim, denial_codes=denial_codes, denial_messages=denial_messages,
        )
        agents = self._build_agents(context)
        return self.runner.run(
            workload="denial_resolution",
            agents=agents,
            supervisor_context=(
                "Render a verdict on the recommended path: APPROVE = "
                "resubmit with the proposed fixes; FLAG = resubmit only "
                "after a human reviews; REJECT = the denial is correct, do "
                "not resubmit."
            ),
        )
