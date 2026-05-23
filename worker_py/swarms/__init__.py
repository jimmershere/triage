"""TurboHEDI supervised swarms.

A *swarm* runs several specialist LLM agents in parallel over the same
workload, then a *supervisor* synthesizes their outputs into a single verdict
(APPROVE / FLAG / REJECT) with a confidence score and a list of issues. Every
call is logged to a structured audit trail.

This package ships:

- A reusable framework (:mod:`swarms.framework`, :mod:`swarms.runner`,
  :mod:`swarms.supervisor`, :mod:`swarms.llm`).
- Workload-specific swarms built on top:

  - :class:`swarms.scrubbing_swarm.ScrubbingSwarm` — review high-stakes claims
    after the deterministic scrubbing engine has flagged them.
  - :class:`swarms.denial_swarm.DenialResolutionSwarm` — propose resubmission
    fixes and an appeal narrative for a denied claim.
  - :class:`swarms.fhir_mapping_swarm.FhirMappingSwarm` — audit a generated
    FHIR bundle for fidelity to its source X12.
"""
from __future__ import annotations

from .denial_swarm import DenialResolutionSwarm
from .fhir_mapping_swarm import FhirMappingSwarm
from .framework import AgentResult, SwarmAgent, SwarmResult
from .llm import LlmClient, MockLlmClient, OllamaClient
from .runner import SwarmRunner
from .scrubbing_swarm import ScrubbingSwarm
from .supervisor import SupervisorVerdict, parse_supervisor_response

__all__ = [
    "AgentResult",
    "DenialResolutionSwarm",
    "FhirMappingSwarm",
    "LlmClient",
    "MockLlmClient",
    "OllamaClient",
    "ScrubbingSwarm",
    "SupervisorVerdict",
    "SwarmAgent",
    "SwarmResult",
    "SwarmRunner",
    "parse_supervisor_response",
]
