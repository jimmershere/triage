"""Supervised FHIR-mapping swarm.

Audits a FHIR R4 bundle generated from an X12 transaction for fidelity to the
source. Four specialists check identifier mapping, code-system selection,
data loss, and conformance to applicable implementation guides (FHIR R4
Claim/EOB, CARIN Blue Button, Da Vinci PAS).
"""
from __future__ import annotations

import json

from .framework import SwarmAgent, SwarmResult
from .runner import SwarmRunner


def _truncate(text: str, limit: int = 1500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... ({len(text) - limit} chars truncated)"


_AGENTS = (
    (
        "identifier_mapping",
        "Verify that identifiers in the FHIR bundle (NPI, Tax ID, "
        "subscriber/member ID, payer ID) line up correctly with the source "
        "X12 fields. Flag any swap, omission or truncation.",
    ),
    (
        "code_system",
        "Verify that procedure, diagnosis and place-of-service codes use "
        "the correct FHIR code systems (CPT/HCPCS, ICD-10-CM, CMS POS).",
    ),
    (
        "fidelity_loss",
        "Identify X12 data that was not preserved in the FHIR bundle. "
        "Examples to look for: claim frequency, filing indicator, control "
        "numbers, modifiers, diagnosis pointers.",
    ),
    (
        "conformance",
        "Check that the bundle conforms to FHIR R4 cardinality and to the "
        "appropriate profile (CARIN BB ExplanationOfBenefit, Da Vinci PAS "
        "Claim, or vanilla R4 Claim / CoverageEligibility*).",
    ),
)


class FhirMappingSwarm:
    """Supervised swarm that audits an X12 -> FHIR mapping."""

    def __init__(self, runner: SwarmRunner) -> None:
        self.runner = runner

    def _build_agents(self, context: str) -> list[SwarmAgent]:
        return [
            SwarmAgent(
                name=name,
                prompt=(
                    f"You are the {name.replace('_', ' ')} agent on a FHIR "
                    "mapping audit swarm. "
                    f"{instructions}\n\n"
                    f"Inputs:\n{context}\n\n"
                    "Respond with a concise critique, citing specific X12 "
                    "segments and FHIR JSON paths where possible. Under 250 "
                    "words."
                ),
                options={"temperature": 0.1, "num_predict": 500},
            )
            for name, instructions in _AGENTS
        ]

    def review(self, x12_text: str, fhir_bundle: dict) -> SwarmResult:
        context = (
            f"Source X12 (truncated):\n{_truncate(x12_text)}\n\n"
            f"Generated FHIR bundle (truncated):\n"
            f"{_truncate(json.dumps(fhir_bundle, indent=2))}"
        )
        agents = self._build_agents(context)
        return self.runner.run(
            workload="fhir_mapping_audit",
            agents=agents,
            supervisor_context=(
                "Render a verdict on the mapping: APPROVE = the FHIR bundle "
                "faithfully represents the X12; FLAG = there are minor "
                "fidelity gaps that should be reviewed; REJECT = there are "
                "material errors that would corrupt downstream FHIR "
                "consumers."
            ),
        )
