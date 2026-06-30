"""Swarm coordinator: shard → fan-out → aggregate → complete claim.

The coordinator is the new agentic entry point that beats the legacy
single-thread ``turbo_pipeline.run_pipeline`` flow for large interchanges
and multi-file bundles. It:

1. Splits the input into independent shards using :mod:`sharding`.
2. Fans validation, scrubbing and FHIR mapping out across an
   :class:`~swarms.engine_pool.EnginePool` so the per-shard work runs
   concurrently.
3. Within each shard, when one transaction carries many claims, the
   coordinator also partitions the claim list and fans scrubbing out
   again. This second axis of parallelism is what beats single-thread
   Python on a 200-claim 837.
4. Merges per-shard reports via :mod:`swarms.aggregator` into a single
   composite view.
5. Generates the canonical TA1 / 999 / 277CA acks against the *original*
   parsed document so the ack envelope counts match the source
   interchange.
6. Optionally runs the existing LLM tier-2 swarm via
   :class:`~swarms.runner.SwarmRunner` (parallel — see also the
   :mod:`tier2_swarm` fix) and folds the supervisor verdict into the
   composite result.

The coordinator does not currently publish to RabbitMQ — that is the
worker role split phase (P4 in the plan). For now it runs entirely
in-process and provides the API that the worker will call.
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from fhir import remittance_to_fhir, submission_to_fhir
from scrubbing import scrub_claims
from sharding import Shard, ShardKind, partition, split_x12
from validation import validate_document
from validation.acks import generate_277ca, generate_999, generate_ta1
from validation.engine import validate_parsed
from validation.model import ClaimProjection, ValidationReport
from validation.parser import parse

from .aggregator import (
    CompositeFhirBundle,
    CompositeScrubView,
    CompositeValidationView,
    merge_fhir_bundles,
    merge_scrub_reports,
    merge_validation_reports,
    min_confidence,
    worst_of_verdict,
)
from .engine_pool import EnginePool, PoolStats
from .framework import SwarmAgent, SwarmResult
from .llm import LlmClient
from .runner import SwarmRunner

logger = logging.getLogger("swarms.coordinator")


# Default fan-out knobs. The coordinator constructor accepts overrides; the
# defaults here are tuned for the typical worker box (4 vCPU) and for the
# ``MockLlmClient``-backed test suite.
_DEFAULT_CLAIMS_PER_BATCH = 25
_DEFAULT_THREAD_WORKERS = 4
_DEFAULT_PROCESS_WORKERS = None  # None -> CPU count


@dataclass
class ShardOutcome:
    """One shard's processed result, ready to be aggregated."""

    shard_id: str
    position: int
    kind: str
    transaction_set: str | None
    duration_ms: float
    validation: dict[str, Any]
    scrubbing: dict[str, Any] | None = None
    fhir: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class ShardedPipelineResult:
    """End-to-end outcome of a sharded coordinator run.

    Mirrors :class:`turbo_pipeline.PipelineResult` where possible so the
    worker can consume either result identically; adds shard-level audit
    fields the unsharded path doesn't have.
    """

    transaction_set: str | None = None
    implementation_version: str | None = None
    shard_count: int = 0
    parallel_workers: int = 0
    sharding_overhead_ms: float = 0.0
    fan_out_ms: float = 0.0
    aggregation_overhead_ms: float = 0.0
    total_duration_ms: float = 0.0
    shards: list[ShardOutcome] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)
    scrubbing: dict[str, Any] | None = None
    fhir: dict[str, Any] | None = None
    acknowledgments: dict[str, str] | None = None
    supervisor: dict[str, Any] | None = None
    fallback_reason: str | None = None  # set when the coordinator falls back

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
        return {
            "transaction_set": self.transaction_set,
            "implementation_version": self.implementation_version,
            "shard_count": self.shard_count,
            "parallel_workers": self.parallel_workers,
            "sharding_overhead_ms": round(self.sharding_overhead_ms, 2),
            "fan_out_ms": round(self.fan_out_ms, 2),
            "aggregation_overhead_ms": round(self.aggregation_overhead_ms, 2),
            "total_duration_ms": round(self.total_duration_ms, 2),
            "shards": [asdict(s) for s in self.shards],
            "validation": self.validation,
            "scrubbing": self.scrubbing,
            "fhir": self.fhir,
            "acknowledgments": self.acknowledgments,
            "supervisor": self.supervisor,
            "valid": self.valid,
            "scrubbing_clean": self.scrubbing_clean,
            "ready_to_submit": self.ready_to_submit,
            "fallback_reason": self.fallback_reason,
        }


class SwarmCoordinator:
    """Coordinator that splits an EDI payload and aggregates shard results.

    Construction parameters configure the two engine pools used by the
    coordinator (one for I/O-bound / mixed work, one for CPU-bound
    scrubbing) and the optional LLM client used by the tier-2 supervisor
    pass.
    """

    def __init__(
        self,
        *,
        thread_pool: EnginePool | None = None,
        process_pool: EnginePool | None = None,
        claims_per_batch: int = _DEFAULT_CLAIMS_PER_BATCH,
        llm_client: LlmClient | None = None,
        swarm_runner: SwarmRunner | None = None,
    ) -> None:
        self.thread_pool = thread_pool or EnginePool(
            mode="thread", max_workers=_DEFAULT_THREAD_WORKERS
        )
        # Scrubbing is the most CPU-heavy stage (NCCI table lookups +
        # within-claim line iteration); default to a process pool so the
        # GIL doesn't cap throughput. Tests typically pass mode="serial"
        # to keep them deterministic.
        self.process_pool = process_pool or EnginePool(
            mode="process", max_workers=_DEFAULT_PROCESS_WORKERS
        )
        if claims_per_batch <= 0:
            raise ValueError("claims_per_batch must be positive")
        self.claims_per_batch = claims_per_batch
        self.llm_client = llm_client
        self.swarm_runner = swarm_runner

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        text: str,
        *,
        parent_id: str = "x12",
        scrub: bool = True,
        to_fhir: bool = False,
        generate_acks: bool = False,
        run_supervisor: bool = False,
    ) -> ShardedPipelineResult:
        """Shard ``text``, fan out engine work, aggregate, return a result."""
        overall_start = time.perf_counter()

        # ---- Stage 1: shard --------------------------------------------------
        shard_start = time.perf_counter()
        shards = split_x12(text, parent_id=parent_id)
        sharding_overhead_ms = (time.perf_counter() - shard_start) * 1000

        # Fall back to the legacy single-pass path when the splitter can't
        # produce real shards (e.g. EDIFACT, malformed input).
        if len(shards) == 1 and shards[0].kind != ShardKind.ST_TRANSACTION:
            return self._fallback_to_unsharded(
                text=text,
                shard=shards[0],
                scrub=scrub,
                to_fhir=to_fhir,
                generate_acks=generate_acks,
                overall_start=overall_start,
                sharding_overhead_ms=sharding_overhead_ms,
            )

        # ---- Stage 2: fan out per-shard validation + (claim-batched) scrub + FHIR --
        fan_start = time.perf_counter()
        process_shard = _ShardWorker(
            scrub=scrub,
            to_fhir=to_fhir,
            claims_per_batch=self.claims_per_batch,
            process_pool=self.process_pool,
        )
        outcomes, pool_stats = self.thread_pool.map_with_stats(
            process_shard, shards
        )
        fan_out_ms = (time.perf_counter() - fan_start) * 1000

        # ---- Stage 3: aggregate ---------------------------------------------
        agg_start = time.perf_counter()
        composite = _aggregate_outcomes(outcomes)
        # Acks must be generated against the ORIGINAL parsed document so
        # the envelope counts and control numbers match what the partner sent.
        acknowledgments = None
        if generate_acks:
            doc = parse(text)
            report = validate_parsed(doc)
            acknowledgments = _build_acks(doc, report)
        # The optional supervisor pass runs only when an LLM client + runner
        # are configured, otherwise we never touch GPU resources.
        supervisor_view = None
        if run_supervisor and self.llm_client and self.swarm_runner:
            supervisor_view = self._run_supervisor(
                text=text,
                composite=composite,
            )
        aggregation_overhead_ms = (time.perf_counter() - agg_start) * 1000

        first = outcomes[0] if outcomes else None
        total_duration_ms = (time.perf_counter() - overall_start) * 1000

        return ShardedPipelineResult(
            transaction_set=composite.validation.transaction_set
            or (first.transaction_set if first else None),
            implementation_version=composite.validation.implementation_version,
            shard_count=len(shards),
            parallel_workers=pool_stats.worker_count,
            sharding_overhead_ms=sharding_overhead_ms,
            fan_out_ms=fan_out_ms,
            aggregation_overhead_ms=aggregation_overhead_ms,
            total_duration_ms=total_duration_ms,
            shards=outcomes,
            validation=composite.validation.to_dict(),
            scrubbing=composite.scrubbing.to_dict() if composite.scrubbing else None,
            fhir=composite.fhir.to_dict() if composite.fhir else None,
            acknowledgments=acknowledgments,
            supervisor=supervisor_view,
        )

    # ------------------------------------------------------------------
    # Internal: fallback path
    # ------------------------------------------------------------------

    def _fallback_to_unsharded(
        self,
        *,
        text: str,
        shard: Shard,
        scrub: bool,
        to_fhir: bool,
        generate_acks: bool,
        overall_start: float,
        sharding_overhead_ms: float,
    ) -> ShardedPipelineResult:
        """When the splitter could not produce real shards, run the legacy
        single-pass pipeline so we never lose data."""
        from turbo_pipeline import run_pipeline as legacy_pipeline

        legacy = legacy_pipeline(
            text, scrub=scrub, to_fhir=to_fhir, generate_acks=generate_acks
        )
        outcome = ShardOutcome(
            shard_id=shard.shard_id,
            position=0,
            kind=shard.kind.value,
            transaction_set=legacy.transaction_set,
            duration_ms=0.0,
            validation=legacy.validation,
            scrubbing=legacy.scrubbing,
            fhir=legacy.fhir,
        )
        total = (time.perf_counter() - overall_start) * 1000
        return ShardedPipelineResult(
            transaction_set=legacy.transaction_set,
            implementation_version=legacy.implementation_version,
            shard_count=1,
            parallel_workers=1,
            sharding_overhead_ms=sharding_overhead_ms,
            fan_out_ms=0.0,
            aggregation_overhead_ms=0.0,
            total_duration_ms=total,
            shards=[outcome],
            validation=legacy.validation,
            scrubbing=legacy.scrubbing,
            fhir=legacy.fhir,
            acknowledgments=legacy.acknowledgments,
            supervisor=None,
            fallback_reason=shard.metadata.get("reason", "unsharded_input"),
        )

    # ------------------------------------------------------------------
    # Internal: supervisor pass
    # ------------------------------------------------------------------

    def _run_supervisor(
        self,
        *,
        text: str,
        composite: "_Composite",
    ) -> dict[str, Any] | None:
        """Run a small specialist swarm over the composite findings.

        The supervisor swarm is intentionally lightweight (three short
        prompts) because the heavy diagnostic prompts live in the
        existing tier-2 swarm; here we just want a roll-up verdict over
        the *aggregated* outputs of the coordinator run.
        """
        assert self.swarm_runner is not None
        summary = {
            "valid": composite.validation.valid,
            "errors": composite.validation.error_count,
            "warnings": composite.validation.warning_count,
            "claims": composite.validation.claim_count,
            "scrub_findings": composite.scrubbing.finding_count
            if composite.scrubbing
            else 0,
            "scrub_clean": composite.scrubbing.clean if composite.scrubbing else True,
        }
        context = (
            "Roll-up of the coordinator's sharded validation+scrubbing pass.\n"
            f"Summary: {summary}\n"
            "Composite findings:\n"
            f"- error_count={summary['errors']}, warning_count={summary['warnings']}\n"
            f"- scrub_findings={summary['scrub_findings']}, clean={summary['scrub_clean']}"
        )
        agents = [
            SwarmAgent(
                name="composite_quality",
                prompt=(
                    "Review this composite EDI processing summary and identify "
                    "any quality concerns that would block automated submission.\n\n"
                    f"{context}"
                ),
            ),
            SwarmAgent(
                name="composite_risk",
                prompt=(
                    "From the composite summary below, rate the submission "
                    "risk and call out any pattern that suggests a structural "
                    "issue across shards (rather than a single bad claim).\n\n"
                    f"{context}"
                ),
            ),
        ]
        try:
            swarm_result: SwarmResult = self.swarm_runner.run(
                workload="coordinator_supervisor",
                agents=agents,
                supervisor_context=context,
            )
        except Exception as exc:  # never let the supervisor break the run
            logger.warning("coordinator supervisor swarm failed: %s", exc)
            return None
        verdict = swarm_result.supervisor
        return {
            "verdict": worst_of_verdict([verdict.verdict]),
            "confidence": min_confidence([verdict.confidence]),
            "summary": verdict.summary,
            "issues": list(verdict.issues),
            "actions": list(verdict.actions),
            "agent_count": len(swarm_result.agents),
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@dataclass
class _Composite:
    """Internal bag for the three aggregated views passed around together."""

    validation: CompositeValidationView
    scrubbing: CompositeScrubView | None
    fhir: CompositeFhirBundle | None


def _aggregate_outcomes(outcomes: list[ShardOutcome]) -> _Composite:
    """Reconstruct ValidationReport/ScrubReport objects from per-shard dicts
    so the existing aggregator pure-functions can fold them. The dicts on
    the ShardOutcome were produced inside the shard worker which returned
    serialized snapshots — we keep them as dicts for transport and rebuild
    just enough structure here to merge them cleanly."""
    validation_reports = [
        _rebuild_validation_report(o.validation) for o in outcomes if o.validation
    ]
    composite_validation = merge_validation_reports(validation_reports)
    scrub_reports = [
        _rebuild_scrub_report(o.scrubbing) for o in outcomes if o.scrubbing
    ]
    composite_scrub = merge_scrub_reports(scrub_reports) if scrub_reports else None
    fhir_bundles = [o.fhir for o in outcomes]
    composite_fhir = merge_fhir_bundles(fhir_bundles)
    return _Composite(
        validation=composite_validation,
        scrubbing=composite_scrub,
        fhir=composite_fhir,
    )


def _rebuild_validation_report(d: dict[str, Any]) -> ValidationReport:
    """Reconstruct a minimal ValidationReport from a to_dict() snapshot.

    Only the fields the aggregator looks at are restored — issues, claims,
    counts, snip levels and transaction set.
    """
    from validation.model import (
        ClaimProjection,
        ServiceLineProjection,
        Severity,
        SnipType,
        ValidationIssue,
    )

    report = ValidationReport()
    report.transaction_set = d.get("transaction_set")
    report.implementation_version = d.get("implementation_version")
    report.transaction_count = int(d.get("transaction_count") or 0)
    report.segment_count = int(d.get("segment_count") or 0)
    report.claim_count = int(d.get("claim_count") or 0)
    report.snip_levels_run = list(d.get("snip_levels_run") or [])

    for raw in d.get("issues") or []:
        try:
            snip = SnipType(int(raw.get("snip_type", 1)))
        except (TypeError, ValueError):
            snip = SnipType.INTEGRITY
        try:
            severity = Severity(str(raw.get("severity", "info")))
        except ValueError:
            severity = Severity.INFO
        report.issues.append(
            ValidationIssue(
                snip_type=snip,
                severity=severity,
                code=str(raw.get("code") or ""),
                message=str(raw.get("message") or ""),
                segment_id=raw.get("segment_id"),
                segment_position=raw.get("segment_position"),
                element_position=raw.get("element_position"),
                component_position=raw.get("component_position"),
                loop_id=raw.get("loop_id"),
                transaction_set=raw.get("transaction_set"),
                transaction_control=raw.get("transaction_control"),
                claim_id=raw.get("claim_id"),
                line_no=raw.get("line_no"),
                expected=raw.get("expected"),
                actual=raw.get("actual"),
                spec_ref=raw.get("spec_ref"),
            )
        )

    for raw in d.get("claims") or []:
        lines = []
        for line in raw.get("service_lines") or []:
            lines.append(
                ServiceLineProjection(
                    line_no=line.get("line_no"),
                    procedure_code=line.get("procedure_code"),
                    procedure_qualifier=line.get("procedure_qualifier"),
                    modifiers=list(line.get("modifiers") or []),
                    charge_amount=line.get("charge_amount"),
                    units=line.get("units"),
                    unit_basis=line.get("unit_basis"),
                    diagnosis_pointers=list(line.get("diagnosis_pointers") or []),
                    place_of_service=line.get("place_of_service"),
                    service_date=line.get("service_date"),
                    revenue_code=line.get("revenue_code"),
                )
            )
        report.claims.append(
            ClaimProjection(
                claim_id=raw.get("claim_id"),
                patient_control_number=raw.get("patient_control_number"),
                total_charge=raw.get("total_charge"),
                place_of_service=raw.get("place_of_service"),
                facility_code=raw.get("facility_code"),
                type_of_bill=raw.get("type_of_bill"),
                claim_frequency_code=raw.get("claim_frequency_code"),
                filing_indicator_code=raw.get("filing_indicator_code"),
                provider_signature_on_file=raw.get("provider_signature_on_file"),
                billing_provider_name=raw.get("billing_provider_name"),
                billing_provider_npi=raw.get("billing_provider_npi"),
                billing_provider_tax_id=raw.get("billing_provider_tax_id"),
                rendering_provider_npi=raw.get("rendering_provider_npi"),
                subscriber_last_name=raw.get("subscriber_last_name"),
                subscriber_first_name=raw.get("subscriber_first_name"),
                subscriber_id=raw.get("subscriber_id"),
                patient_last_name=raw.get("patient_last_name"),
                patient_first_name=raw.get("patient_first_name"),
                patient_dob=raw.get("patient_dob"),
                patient_gender=raw.get("patient_gender"),
                payer_name=raw.get("payer_name"),
                payer_id=raw.get("payer_id"),
                statement_from_date=raw.get("statement_from_date"),
                statement_to_date=raw.get("statement_to_date"),
                diagnosis_codes=list(raw.get("diagnosis_codes") or []),
                service_lines=lines,
            )
        )

    return report


def _rebuild_scrub_report(d: dict[str, Any]) -> "object":
    """Rebuild a thin ScrubReport so the aggregator can fold it."""
    from scrubbing.model import EditCategory, ScrubFinding, ScrubReport, ScrubSeverity

    report = ScrubReport(claim_count=int(d.get("claim_count") or 0))
    report.edits_run = list(d.get("edits_run") or [])
    for raw in d.get("findings") or []:
        try:
            category = EditCategory(str(raw.get("category")))
        except ValueError:
            continue
        try:
            severity = ScrubSeverity(str(raw.get("severity")))
        except ValueError:
            continue
        report.findings.append(
            ScrubFinding(
                category=category,
                severity=severity,
                code=str(raw.get("code") or ""),
                message=str(raw.get("message") or ""),
                claim_id=raw.get("claim_id"),
                line_no=raw.get("line_no"),
                procedure_code=raw.get("procedure_code"),
                related_code=raw.get("related_code"),
                diagnosis_code=raw.get("diagnosis_code"),
                resolution=raw.get("resolution"),
                source=raw.get("source"),
            )
        )
    return report


def _build_acks(doc, report) -> dict[str, str]:
    """Generate TA1/999/277CA against the original parsed document."""
    txn = report.transaction_set or ""
    out = {
        "TA1": generate_ta1(doc, report),
        "999": generate_999(doc, report),
    }
    if txn == "837":
        out["277CA"] = generate_277ca(doc, report)
    return out


# ---------------------------------------------------------------------------
# Shard worker (callable, picklable for process pools by avoiding closures)
# ---------------------------------------------------------------------------


class _ShardWorker:
    """Callable that processes one :class:`Shard` end-to-end.

    Implemented as a class (not a closure) so it remains picklable when
    the coordinator is configured with a ``mode="process"`` thread pool.
    """

    def __init__(
        self,
        *,
        scrub: bool,
        to_fhir: bool,
        claims_per_batch: int,
        process_pool: EnginePool,
    ) -> None:
        self.scrub = scrub
        self.to_fhir = to_fhir
        self.claims_per_batch = claims_per_batch
        self.process_pool = process_pool

    def __call__(self, shard: Shard) -> ShardOutcome:
        start = time.perf_counter()
        try:
            report = validate_document(shard.payload, source=shard.shard_id)
        except Exception as exc:
            duration = (time.perf_counter() - start) * 1000
            return ShardOutcome(
                shard_id=shard.shard_id,
                position=shard.position,
                kind=shard.kind.value,
                transaction_set=shard.transaction_set,
                duration_ms=duration,
                validation={},
                error=f"validation_failed: {exc}",
            )

        scrubbing_dict: dict[str, Any] | None = None
        if self.scrub and report.claims:
            try:
                scrubbing_dict = self._scrub_in_batches(report.claims)
            except Exception as exc:
                scrubbing_dict = {
                    "claim_count": len(report.claims),
                    "finding_count": 0,
                    "deny_count": 0,
                    "review_count": 0,
                    "clean": False,
                    "edits_run": [],
                    "category_summary": {},
                    "findings": [],
                    "error": f"scrubbing_failed: {exc}",
                }

        fhir_dict: dict[str, Any] | None = None
        if self.to_fhir:
            try:
                if report.transaction_set == "837":
                    fhir_dict = submission_to_fhir(shard.payload)
                elif report.transaction_set == "835":
                    fhir_dict = remittance_to_fhir(shard.payload)
            except Exception as exc:
                fhir_dict = {"error": f"fhir_failed: {exc}"}

        duration = (time.perf_counter() - start) * 1000
        return ShardOutcome(
            shard_id=shard.shard_id,
            position=shard.position,
            kind=shard.kind.value,
            transaction_set=report.transaction_set or shard.transaction_set,
            duration_ms=duration,
            validation=report.to_dict(),
            scrubbing=scrubbing_dict,
            fhir=fhir_dict,
        )

    def _scrub_in_batches(self, claims: list[ClaimProjection]) -> dict[str, Any]:
        """Scrub claims in parallel batches via the process pool.

        For small claim counts we skip the fan-out entirely so we don't
        pay process-spawn cost for one batch.
        """
        if len(claims) <= self.claims_per_batch:
            report = scrub_claims(claims)
            return report.to_dict()

        batches = partition(claims, self.claims_per_batch)
        # Run the batches in parallel. We can't pass dataclasses across
        # process boundaries cheaply (they pickle fine, but the round-trip
        # is wasteful), so we go through the in-thread pool by default;
        # process_pool is exposed for callers who want the GIL-free path.
        batch_reports = self.process_pool.map(scrub_claims, batches)

        # Fold the batch reports into a single ScrubReport-compatible dict.
        all_findings = []
        edits_run: list[str] = []
        total = 0
        for r in batch_reports:
            total += r.claim_count
            all_findings.extend(r.findings)
            for name in r.edits_run:
                if name not in edits_run:
                    edits_run.append(name)
        deny_count = sum(1 for f in all_findings if f.severity.value == "deny")
        review_count = sum(1 for f in all_findings if f.severity.value == "review")
        category_summary: dict[str, int] = {}
        for f in all_findings:
            category_summary[f.category.value] = (
                category_summary.get(f.category.value, 0) + 1
            )
        return {
            "claim_count": total,
            "finding_count": len(all_findings),
            "deny_count": deny_count,
            "review_count": review_count,
            "clean": not any(f.severity.blocks_submission for f in all_findings),
            "edits_run": edits_run,
            "category_summary": category_summary,
            "findings": [f.to_dict() for f in all_findings],
        }
