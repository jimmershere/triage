"""Framework data classes shared by every swarm and workload."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SwarmAgent:
    """One specialist agent in a swarm.

    Each agent has a stable ``name`` (used in audit logs and the supervisor
    prompt), a fully-rendered ``prompt`` string and an optional ``options``
    dict that is merged into the LLM call (temperature, num_predict, ...).
    """

    name: str
    prompt: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    """Outcome of one agent's LLM call."""

    name: str
    response: str | None = None
    duration_ms: float = 0.0
    error: str | None = None
    attempts: int = 1

    @property
    def succeeded(self) -> bool:
        return self.error is None and self.response is not None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SwarmResult:
    """Aggregate outcome of one swarm run.

    The result carries every agent response, the supervisor verdict and a
    structured audit trail of every LLM call (input prompt, output, timing,
    retries) — sufficient to reproduce the decision for compliance review.
    """

    workload: str
    agents: list[AgentResult] = field(default_factory=list)
    supervisor: "Any" = None  # supervisor.SupervisorVerdict
    duration_ms: float = 0.0
    audit: list[dict[str, Any]] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        return getattr(self.supervisor, "verdict", "FLAG")

    @property
    def confidence(self) -> float:
        return float(getattr(self.supervisor, "confidence", 0.0))

    @property
    def is_approved(self) -> bool:
        return self.verdict == "APPROVE"

    def to_dict(self) -> dict[str, Any]:
        sup = self.supervisor.to_dict() if self.supervisor is not None else None
        return {
            "workload": self.workload,
            "duration_ms": round(self.duration_ms, 2),
            "agents": [a.to_dict() for a in self.agents],
            "supervisor": sup,
            "audit": self.audit,
        }
