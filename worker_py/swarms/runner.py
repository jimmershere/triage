"""Swarm orchestrator.

The :class:`SwarmRunner` dispatches a list of :class:`SwarmAgent` calls in
parallel against an :class:`LlmClient`, retries transient failures, then runs
a supervisor call that synthesizes the responses into a single verdict.

Every LLM call (agents + supervisor + retries) is captured in the result's
audit trail so a deployment can replay the decision for compliance review.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .framework import AgentResult, SwarmAgent, SwarmResult
from .llm import LlmClient
from .supervisor import SupervisorVerdict, parse_supervisor_response

logger = logging.getLogger("swarms.runner")


class SwarmRunner:
    """Run a parallel swarm of LLM agents and have a supervisor synthesize."""

    def __init__(
        self,
        client: LlmClient,
        *,
        max_workers: int = 4,
        per_call_timeout: float = 90.0,
        max_retries: int = 1,
        retry_backoff: float = 1.0,
    ) -> None:
        self.client = client
        self.max_workers = max(1, int(max_workers))
        self.per_call_timeout = per_call_timeout
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff = retry_backoff

    # ------------------------------------------------------------------

    def _call_agent(self, agent: SwarmAgent) -> AgentResult:
        start = time.perf_counter()
        last_error: Exception | None = None
        attempts = 0
        for attempt in range(self.max_retries + 1):
            attempts = attempt + 1
            try:
                response = self.client.generate(agent.prompt, **agent.options)
                duration = (time.perf_counter() - start) * 1000
                return AgentResult(
                    name=agent.name,
                    response=response,
                    duration_ms=duration,
                    attempts=attempts,
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "swarm agent %s attempt %d failed: %s",
                    agent.name, attempts, exc,
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff * (attempt + 1))
        duration = (time.perf_counter() - start) * 1000
        return AgentResult(
            name=agent.name,
            duration_ms=duration,
            error=str(last_error) if last_error else "unknown error",
            attempts=attempts,
        )

    def _audit_entry(self, agent: SwarmAgent, result: AgentResult) -> dict:
        return {
            "agent": agent.name,
            "prompt": agent.prompt,
            "response": result.response,
            "duration_ms": round(result.duration_ms, 2),
            "attempts": result.attempts,
            "error": result.error,
            "options": agent.options,
        }

    def _supervisor_prompt(
        self,
        workload: str,
        agents: list[SwarmAgent],
        results: list[AgentResult],
        supervisor_context: str,
    ) -> str:
        summaries: list[str] = []
        for agent, result in zip(agents, results):
            body = result.response or f"FAILED: {result.error}"
            summaries.append(f"### {agent.name}\n{body.strip()[:1200]}")
        return (
            f"You are the {workload} swarm supervisor. {len(agents)} specialist "
            "agents have reviewed the case.\n\n"
            f"{supervisor_context}\n\n"
            "Agent analyses:\n"
            + "\n\n".join(summaries)
            + "\n\nReply with ONLY a JSON object with these fields: "
            '{"verdict": "APPROVE" | "FLAG" | "REJECT", '
            '"confidence": 0.0-1.0, '
            '"summary": "1-2 sentence rationale", '
            '"issues": ["short issue strings"], '
            '"actions": ["concrete next steps"]} '
            'and nothing else.'
        )

    # ------------------------------------------------------------------

    def run(
        self,
        workload: str,
        agents: list[SwarmAgent],
        *,
        supervisor_context: str = "",
        supervisor_prompt_builder: Callable[[list[SwarmAgent], list[AgentResult]], str] | None = None,
        supervisor_options: dict | None = None,
    ) -> SwarmResult:
        """Run ``agents`` in parallel and have the supervisor synthesize."""
        start = time.perf_counter()
        audit: list[dict] = []

        results: list[AgentResult] = [
            AgentResult(name=agent.name) for agent in agents
        ]

        if agents:
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                future_to_index = {
                    pool.submit(self._call_agent, agent): i
                    for i, agent in enumerate(agents)
                }
                for future in as_completed(future_to_index):
                    idx = future_to_index[future]
                    results[idx] = future.result()
                    audit.append(self._audit_entry(agents[idx], results[idx]))

        # Build & run supervisor.
        if supervisor_prompt_builder is None:
            sup_prompt = self._supervisor_prompt(workload, agents, results, supervisor_context)
        else:
            sup_prompt = supervisor_prompt_builder(agents, results)

        sup_agent = SwarmAgent(
            name="supervisor",
            prompt=sup_prompt,
            options=(supervisor_options or {"temperature": 0.05, "num_predict": 512}),
        )
        sup_result = self._call_agent(sup_agent)
        audit.append(self._audit_entry(sup_agent, sup_result))

        verdict: SupervisorVerdict = parse_supervisor_response(sup_result.response or "")
        if sup_result.error:
            verdict.issues.append(f"supervisor_error:{sup_result.error}")

        duration = (time.perf_counter() - start) * 1000
        return SwarmResult(
            workload=workload,
            agents=results,
            supervisor=verdict,
            duration_ms=duration,
            audit=audit,
        )
