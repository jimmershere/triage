"""Claim-scrubbing engine orchestrator.

Runs every CMS payment edit over a set of claim projections and aggregates the
findings into a single :class:`ScrubReport`.

Usage::

    from scrubbing import scrub_document
    report = scrub_document(raw_x12_837_text)
    print(report.is_clean, len(report.deny_findings))
"""
from __future__ import annotations

import json
from pathlib import Path

from validation.model import ClaimProjection

from .edits import (
    coverage,
    demographics,
    diagnosis,
    duplicates,
    eligibility,
    modifiers,
    ncci_mue,
    ncci_ptp,
)
from .model import ScrubReport

# Ordered list of (edit name, apply function). Order is cosmetic — findings are
# aggregated — but kept stable for readable reports.
_EDITS = [
    ("ncci_ptp", ncci_ptp.apply),
    ("ncci_mue", ncci_mue.apply),
    ("coverage", coverage.apply),
    ("modifiers", modifiers.apply),
    ("diagnosis_sequencing", diagnosis.apply),
    ("demographics", demographics.apply),
    ("duplicates", duplicates.apply),
    ("eligibility", eligibility.apply),
]


def scrub_claims(
    claims: list[ClaimProjection],
    *,
    roster: dict[str, list[dict]] | None = None,
) -> ScrubReport:
    """Run every CMS payment edit over ``claims`` and return a scrub report."""
    report = ScrubReport(claim_count=len(claims))
    for name, apply in _EDITS:
        apply(claims, report, roster=roster)
        report.edits_run.append(name)
    return report


def scrub_document(
    text: str,
    *,
    roster: dict[str, list[dict]] | None = None,
) -> ScrubReport:
    """Validate raw X12 837 text, then scrub the projected claims."""
    from validation.engine import validate_document

    validation = validate_document(text)
    return scrub_claims(validation.claims, roster=roster)


def scrub_file(path: str | Path, *, roster: dict | None = None) -> ScrubReport:
    """Validate and scrub an X12 file on disk."""
    return scrub_document(
        Path(path).read_text(encoding="utf-8", errors="ignore"), roster=roster
    )


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Scrub an X12 837 claim file against CMS payment edits."
    )
    parser.add_argument("input", type=Path, help="Path to an X12 837 file")
    parser.add_argument("--json", action="store_true", help="Emit the full JSON report")
    args = parser.parse_args(argv)

    report = scrub_file(args.input)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0 if report.is_clean else 1

    print(f"claims scrubbed: {report.claim_count}")
    print(f"findings:        {len(report.findings)}")
    print(f"  deny:          {len(report.deny_findings)}")
    print(f"  review:        {len(report.review_findings)}")
    print(f"clean:           {report.is_clean}")
    print("by category:")
    for category, count in sorted(report.category_summary().items()):
        print(f"  {category}: {count}")
    for finding in report.findings[:40]:
        print(
            f"  [{finding.severity.value.upper()}] {finding.code} "
            f"(claim {finding.claim_id}): {finding.message}"
        )
    if len(report.findings) > 40:
        print(f"  ... {len(report.findings) - 40} more")
    return 0 if report.is_clean else 1


if __name__ == "__main__":
    raise SystemExit(_main())
