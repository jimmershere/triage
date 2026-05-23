"""Tests for the supervised swarm framework and workloads."""
import json
import unittest

from validation.model import ClaimProjection, ServiceLineProjection

from swarms import (
    DenialResolutionSwarm,
    FhirMappingSwarm,
    LlmClient,
    MockLlmClient,
    ScrubbingSwarm,
    SwarmAgent,
    SwarmRunner,
    SupervisorVerdict,
    parse_supervisor_response,
)


def _supervisor_json(
    verdict: str = "APPROVE",
    confidence: float = 0.9,
    summary: str = "ok",
    issues=None,
    actions=None,
) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "confidence": confidence,
            "summary": summary,
            "issues": issues or [],
            "actions": actions or [],
        }
    )


def _agent_router(per_agent: dict[str, str], supervisor: str):
    """Build a callable for MockLlmClient that routes by agent name in the prompt.

    The supervisor prompt is identified first (its distinctive marker) so
    that agent-name substrings inside the supervisor's quoted recap do not
    short-circuit the routing to an agent response.
    """

    def respond(prompt: str) -> str:
        if "Reply with ONLY a JSON object" in prompt:
            return supervisor
        for name, response in per_agent.items():
            if name in prompt:
                return response
        return ""

    return respond


# ---------------------------------------------------------------------------
# Supervisor parser
# ---------------------------------------------------------------------------

class SupervisorParseTests(unittest.TestCase):
    def test_clean_json(self) -> None:
        v = parse_supervisor_response(_supervisor_json("APPROVE", 0.92, "ok"))
        self.assertEqual(v.verdict, "APPROVE")
        self.assertAlmostEqual(v.confidence, 0.92, places=4)

    def test_markdown_fences_tolerated(self) -> None:
        wrapped = f"```json\n{_supervisor_json('FLAG', 0.6)}\n```"
        v = parse_supervisor_response(wrapped)
        self.assertEqual(v.verdict, "FLAG")

    def test_leading_text_then_json(self) -> None:
        v = parse_supervisor_response(
            f"Here is my decision:\n{_supervisor_json('REJECT', 0.4)}"
        )
        self.assertEqual(v.verdict, "REJECT")

    def test_unparseable_falls_back_to_flag(self) -> None:
        v = parse_supervisor_response("totally not json")
        self.assertEqual(v.verdict, "FLAG")
        self.assertIn("supervisor_parse_failure", v.issues)

    def test_empty_falls_back_with_empty_issue(self) -> None:
        v = parse_supervisor_response("")
        self.assertEqual(v.verdict, "FLAG")
        self.assertIn("supervisor_empty", v.issues)

    def test_bad_verdict_coerces_to_flag(self) -> None:
        v = parse_supervisor_response(_supervisor_json("MAYBE"))
        self.assertEqual(v.verdict, "FLAG")

    def test_confidence_clamped(self) -> None:
        v = parse_supervisor_response(_supervisor_json("APPROVE", 5.0))
        self.assertEqual(v.confidence, 1.0)


# ---------------------------------------------------------------------------
# MockLlmClient
# ---------------------------------------------------------------------------

class MockLlmClientTests(unittest.TestCase):
    def test_satisfies_protocol(self) -> None:
        self.assertIsInstance(MockLlmClient(), LlmClient)

    def test_dict_routing(self) -> None:
        client = MockLlmClient({"foo": "bar"}, default="default")
        self.assertEqual(client.generate("contains foo"), "bar")
        self.assertEqual(client.generate("nothing"), "default")

    def test_callable_routing(self) -> None:
        client = MockLlmClient(lambda p: f"echo:{len(p)}")
        self.assertEqual(client.generate("hi"), "echo:2")

    def test_records_calls(self) -> None:
        client = MockLlmClient({}, default="x")
        client.generate("first", temperature=0.1)
        client.generate("second")
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0]["prompt"], "first")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class SwarmRunnerTests(unittest.TestCase):
    def test_runs_all_agents_in_parallel(self) -> None:
        client = MockLlmClient(
            _agent_router(
                {"alpha": "alpha-response", "beta": "beta-response"},
                _supervisor_json("APPROVE", 0.9),
            )
        )
        runner = SwarmRunner(client, max_workers=4)
        result = runner.run(
            workload="test",
            agents=[
                SwarmAgent(name="alpha", prompt="alpha analyses claim"),
                SwarmAgent(name="beta", prompt="beta analyses claim"),
            ],
        )
        self.assertEqual(len(result.agents), 2)
        responses = {a.name: a.response for a in result.agents}
        self.assertEqual(responses["alpha"], "alpha-response")
        self.assertEqual(responses["beta"], "beta-response")
        self.assertEqual(result.supervisor.verdict, "APPROVE")

    def test_audit_trail_includes_every_call(self) -> None:
        client = MockLlmClient({}, default=_supervisor_json("FLAG"))
        runner = SwarmRunner(client)
        result = runner.run(
            workload="t",
            agents=[SwarmAgent(name="x", prompt="p")],
        )
        # 1 agent + 1 supervisor entry.
        self.assertEqual(len(result.audit), 2)
        names = {entry["agent"] for entry in result.audit}
        self.assertEqual(names, {"x", "supervisor"})

    def test_retry_recovers_from_transient_failure(self) -> None:
        attempts: list[str] = []

        def flaky(prompt: str) -> str:
            attempts.append(prompt)
            if prompt.startswith("agent") and len(attempts) <= 1:
                raise RuntimeError("transient")
            if "Reply with ONLY a JSON object" in prompt:
                return _supervisor_json("APPROVE")
            return "recovered"

        client = MockLlmClient(flaky)
        runner = SwarmRunner(client, max_retries=2, retry_backoff=0.0)
        result = runner.run(
            workload="t",
            agents=[SwarmAgent(name="primary", prompt="agent primary")],
        )
        primary = next(a for a in result.agents if a.name == "primary")
        self.assertEqual(primary.attempts, 2)
        self.assertEqual(primary.response, "recovered")

    def test_persistent_failure_marks_agent_errored(self) -> None:
        def always_fail(prompt: str) -> str:
            if "Reply with ONLY a JSON object" in prompt:
                return _supervisor_json("FLAG")
            raise RuntimeError("nope")

        client = MockLlmClient(always_fail)
        runner = SwarmRunner(client, max_retries=0)
        result = runner.run(
            workload="t",
            agents=[SwarmAgent(name="a", prompt="agent")],
        )
        self.assertEqual(result.agents[0].error, "nope")
        self.assertFalse(result.agents[0].succeeded)


# ---------------------------------------------------------------------------
# Workload swarms
# ---------------------------------------------------------------------------

def _sample_claim() -> ClaimProjection:
    return ClaimProjection(
        claim_id="C-1",
        total_charge="500",
        place_of_service="11",
        diagnosis_codes=["E119"],
        patient_last_name="DOE",
        patient_first_name="JANE",
        patient_gender="F",
        patient_dob="19800101",
        billing_provider_npi="1234567893",
        service_lines=[
            ServiceLineProjection(
                line_no="1", procedure_code="99214", charge_amount="175",
                units="1", service_date="20260510",
            ),
            ServiceLineProjection(
                line_no="2", procedure_code="27447", charge_amount="325",
                units="1", service_date="20260510",
            ),
        ],
    )


class WorkloadSwarmTests(unittest.TestCase):
    def test_scrubbing_swarm_approves(self) -> None:
        client = MockLlmClient(
            _agent_router(
                {
                    "coding_correctness": "Codes look consistent. Severity: low",
                    "medical_necessity": "Diagnosis supports procedures. Severity: low",
                    "bundling_ncci": "27447+99214 — recommend modifier 25. Severity: medium",
                    "compliance": "Demographics fine. Severity: low",
                },
                _supervisor_json("FLAG", 0.7, "PTP risk needs modifier 25",
                                 issues=["bundling_risk"],
                                 actions=["append modifier 25 to E/M"]),
            )
        )
        swarm = ScrubbingSwarm(SwarmRunner(client))
        result = swarm.review(_sample_claim())
        self.assertEqual(result.workload, "claim_scrubbing")
        self.assertEqual(result.verdict, "FLAG")
        self.assertEqual(len(result.agents), 4)
        self.assertIn("bundling_risk", result.supervisor.issues)

    def test_denial_swarm_produces_actions(self) -> None:
        client = MockLlmClient(
            _agent_router(
                {
                    "reason_interpretation": "CARC 16 = missing information.",
                    "fix_proposal": "Add modifier 25 to 99214.",
                    "documentation": "Office visit note documenting E/M.",
                    "appeal_narrative": "We respectfully appeal...",
                },
                _supervisor_json(
                    "APPROVE", 0.85, "Resubmit with modifier 25",
                    actions=["resubmit_with_modifier_25", "include_office_note"],
                ),
            )
        )
        swarm = DenialResolutionSwarm(SwarmRunner(client))
        result = swarm.resolve(
            _sample_claim(),
            denial_codes=["16"],
            denial_messages=["Claim/service lacks information."],
        )
        self.assertEqual(result.verdict, "APPROVE")
        self.assertIn("resubmit_with_modifier_25", result.supervisor.actions)

    def test_fhir_mapping_swarm(self) -> None:
        client = MockLlmClient(
            _agent_router(
                {
                    "identifier_mapping": "NPI maps correctly. No issues.",
                    "code_system": "CPT system URI present.",
                    "fidelity_loss": "Claim frequency not represented.",
                    "conformance": "Bundle is R4-conformant.",
                },
                _supervisor_json(
                    "FLAG", 0.7, "Minor fidelity loss",
                    issues=["claim_frequency_missing"],
                ),
            )
        )
        swarm = FhirMappingSwarm(SwarmRunner(client))
        result = swarm.review(
            "ISA*00*...ST*837*...SE*1*0001~",
            {"resourceType": "Bundle", "type": "transaction", "entry": []},
        )
        self.assertEqual(result.verdict, "FLAG")
        self.assertIn("claim_frequency_missing", result.supervisor.issues)


if __name__ == "__main__":
    unittest.main()
