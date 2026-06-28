"""TurboHEDI end-to-end pipeline.

Glue layer that runs the four engines added in Phases 1-4 (and optionally a
Phase 5 swarm) over a single X12 payload and returns a single
:class:`PipelineResult`. This is the canonical entry point used by both the
HTTP API (``api/turbo_routes.py``) and the worker (`worker_py/worker.py`).

Order of operations::

    raw X12 -> validate (SNIP 1-7)
              -> scrub  (CMS payment edits)        [if it produced claims]
              -> map to FHIR Bundle                [opt-in]
              -> generate ACKs (TA1 / 999 / 277CA) [opt-in]
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fhir import remittance_to_fhir, submission_to_fhir
from scrubbing import scrub_claims
from validation import validate_document
from validation.acks import (
    ACK_PROFILE_999_ONLY,
    generate_277ca,
    generate_999,
    generate_acks_for_profile,
    generate_ta1,
)
from validation.engine import validate_parsed
from validation.parser import parse
from validation.policy import apply_policy


@dataclass
class PipelineResult:
    """Single combined report for one X12 payload."""

    transaction_set: str | None = None
    implementation_version: str | None = None
    validation: dict[str, Any] = field(default_factory=dict)
    scrubbing: dict[str, Any] | None = None
    fhir: dict[str, Any] | None = None
    acknowledgments: dict[str, str] | None = None
    snip_policy: dict[str, Any] | None = None

    @property
    def valid(self) -> bool:
        return bool(self.validation.get("valid"))

    @property
    def scrubbing_clean(self) -> bool:
        if not self.scrubbing:
            return True
        return bool(self.scrubbing.get("clean"))

    @property
    def ready_to_submit(self) -> bool:
        return self.valid and self.scrubbing_clean

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_pipeline(
    text: str,
    *,
    scrub: bool = True,
    to_fhir: bool = False,
    generate_acks: bool = False,
    eligibility_roster: dict | None = None,
    policy: Any = None,
    ack_profile: str = ACK_PROFILE_999_ONLY,
) -> PipelineResult:
    """Run validation, optional scrubbing, optional FHIR mapping and acks.

    ``policy`` is an optional per-partner SNIP policy (a
    :class:`validation.policy.SnipPolicy` or a built-in policy name) applied to
    the validation report before acks are generated, so the 999/277CA reflect
    the partner's configured leniency. ``ack_profile`` selects which acks are
    emitted (``999_only`` by default; ``999_plus_277CA`` to opt in to 277CA).
    """
    doc = parse(text)
    report = validate_parsed(doc)

    result = PipelineResult(
        transaction_set=report.transaction_set,
        implementation_version=report.implementation_version,
        validation={},
    )

    if policy is not None:
        result.snip_policy = apply_policy(report, policy).to_dict()

    # Serialize the report after the policy has adjusted severities so the
    # surfaced accept/reject decision matches the generated acknowledgements.
    result.validation = report.to_dict()

    if scrub and report.claims:
        scrub_report = scrub_claims(report.claims, roster=eligibility_roster)
        result.scrubbing = scrub_report.to_dict()

    if to_fhir:
        if report.transaction_set == "837":
            result.fhir = submission_to_fhir(text)
        elif report.transaction_set == "835":
            result.fhir = remittance_to_fhir(text)

    if generate_acks:
        result.acknowledgments = generate_acks_for_profile(
            doc, report, ack_profile=ack_profile
        )

    return result


def run_pipeline_file(path: str | Path, **kwargs: Any) -> PipelineResult:
    """File-path variant of :func:`run_pipeline`."""
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    return run_pipeline(text, **kwargs)
