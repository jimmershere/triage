"""Aggregate per-shard outcomes back into one composite result.

The coordinator fans validation, scrubbing and FHIR mapping out across
shards; this module merges those per-shard products back into a single
view that downstream code (worker, API, audit trail) can consume.

The aggregator never re-runs business logic — it just folds existing
report objects into one. The only re-run is the canonical ack generation
which the coordinator performs against the *original* parsed document
(once, at the parent level), because acknowledgment counts (999 SE,
277CA STC totals) must reference the original interchange envelope.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scrubbing.model import ScrubFinding, ScrubReport
from validation.model import ValidationReport


@dataclass
class CompositeValidationView:
    """Read-only roll-up of N shard :class:`ValidationReport` objects."""

    transaction_set: str | None
    implementation_version: str | None
    transaction_count: int
    segment_count: int
    claim_count: int
    error_count: int
    warning_count: int
    valid: bool
    snip_summary: dict[str, dict[str, int]]
    issues: list[dict[str, Any]]
    claims: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_set": self.transaction_set,
            "implementation_version": self.implementation_version,
            "transaction_count": self.transaction_count,
            "segment_count": self.segment_count,
            "claim_count": self.claim_count,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "valid": self.valid,
            "snip_summary": self.snip_summary,
            "issues": self.issues,
            "claims": self.claims,
        }


@dataclass
class CompositeScrubView:
    """Read-only roll-up of N shard :class:`ScrubReport` objects."""

    claim_count: int
    finding_count: int
    deny_count: int
    review_count: int
    clean: bool
    edits_run: list[str]
    category_summary: dict[str, int]
    findings: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_count": self.claim_count,
            "finding_count": self.finding_count,
            "deny_count": self.deny_count,
            "review_count": self.review_count,
            "clean": self.clean,
            "edits_run": self.edits_run,
            "category_summary": self.category_summary,
            "findings": self.findings,
        }


@dataclass
class CompositeFhirBundle:
    """One FHIR ``Bundle`` of type ``collection`` over per-shard bundles."""

    resource_type: str = "Bundle"
    bundle_type: str = "collection"
    entry: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resourceType": self.resource_type,
            "type": self.bundle_type,
            "entry": list(self.entry),
        }


# ---------------------------------------------------------------------------
# Aggregators
# ---------------------------------------------------------------------------


def merge_validation_reports(
    reports: list[ValidationReport],
) -> CompositeValidationView:
    """Fold N shard validation reports into a single composite view.

    Per-claim and per-issue lists are concatenated in shard order so
    consumers can still rebuild per-claim positions deterministically.
    """
    if not reports:
        return CompositeValidationView(
            transaction_set=None,
            implementation_version=None,
            transaction_count=0,
            segment_count=0,
            claim_count=0,
            error_count=0,
            warning_count=0,
            valid=True,
            snip_summary={},
            issues=[],
            claims=[],
        )

    transaction_count = sum(r.transaction_count for r in reports)
    segment_count = sum(r.segment_count for r in reports)
    issues: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    error_count = 0
    warning_count = 0
    valid = True

    snip_summary: dict[str, dict[str, int]] = {}
    for report in reports:
        for issue in report.issues:
            issues.append(issue.to_dict())
        for claim in report.claims:
            claims.append(claim.to_dict())
        error_count += len(report.errors)
        warning_count += len(report.warnings)
        valid = valid and report.is_valid
        for level, counts in report.snip_summary().items():
            slot = snip_summary.setdefault(level, {"errors": 0, "warnings": 0})
            slot["errors"] += counts.get("errors", 0)
            slot["warnings"] += counts.get("warnings", 0)

    first = reports[0]
    return CompositeValidationView(
        transaction_set=first.transaction_set,
        implementation_version=first.implementation_version,
        transaction_count=transaction_count,
        segment_count=segment_count,
        claim_count=len(claims),
        error_count=error_count,
        warning_count=warning_count,
        valid=valid,
        snip_summary=snip_summary,
        issues=issues,
        claims=claims,
    )


def merge_scrub_reports(reports: list[ScrubReport]) -> CompositeScrubView:
    """Fold N shard scrub reports into a single composite view."""
    if not reports:
        return CompositeScrubView(
            claim_count=0,
            finding_count=0,
            deny_count=0,
            review_count=0,
            clean=True,
            edits_run=[],
            category_summary={},
            findings=[],
        )

    findings: list[ScrubFinding] = []
    edits_run: list[str] = []
    claim_count = 0
    for report in reports:
        claim_count += report.claim_count
        findings.extend(report.findings)
        for name in report.edits_run:
            if name not in edits_run:
                edits_run.append(name)

    deny_count = sum(1 for f in findings if f.severity.value == "deny")
    review_count = sum(1 for f in findings if f.severity.value == "review")
    category_summary: dict[str, int] = {}
    for f in findings:
        category_summary[f.category.value] = category_summary.get(f.category.value, 0) + 1

    clean = not any(f.severity.blocks_submission for f in findings)

    return CompositeScrubView(
        claim_count=claim_count,
        finding_count=len(findings),
        deny_count=deny_count,
        review_count=review_count,
        clean=clean,
        edits_run=edits_run,
        category_summary=category_summary,
        findings=[f.to_dict() for f in findings],
    )


def merge_fhir_bundles(
    shard_bundles: list[dict[str, Any] | None],
) -> CompositeFhirBundle | None:
    """Combine per-shard FHIR bundles into a composite ``collection`` bundle.

    Returns ``None`` when no shard produced a bundle (e.g. the input was
    a 270 with no claim to map). Each shard bundle's ``entry`` list is
    flattened into the composite bundle.
    """
    bundles = [b for b in shard_bundles if b]
    if not bundles:
        return None

    composite = CompositeFhirBundle()
    for bundle in bundles:
        entries = bundle.get("entry") if isinstance(bundle, dict) else None
        if isinstance(entries, list):
            composite.entry.extend(entries)
        else:
            # If the shard bundle itself is a single resource (no wrapper),
            # wrap it so the consumer always sees uniform entries.
            composite.entry.append({"resource": bundle})
    return composite


# ---------------------------------------------------------------------------
# Supervisor verdict fold (deterministic worst-of rule)
# ---------------------------------------------------------------------------

_VERDICT_RANK = {"APPROVE": 0, "FLAG": 1, "REJECT": 2}


def worst_of_verdict(verdicts: list[str]) -> str:
    """Return the most pessimistic verdict from a list (REJECT > FLAG > APPROVE).

    Unknown verdicts are treated as FLAG so they never silently approve.
    """
    if not verdicts:
        return "APPROVE"
    rank = -1
    pick = "APPROVE"
    for v in verdicts:
        candidate = v if v in _VERDICT_RANK else "FLAG"
        if _VERDICT_RANK[candidate] > rank:
            rank = _VERDICT_RANK[candidate]
            pick = candidate
    return pick


def min_confidence(values: list[float]) -> float:
    """Return the smallest finite confidence; defaults to 0.0 when empty."""
    finite = [v for v in values if v == v]  # filter NaN
    if not finite:
        return 0.0
    return float(min(finite))
