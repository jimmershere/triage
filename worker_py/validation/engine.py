"""Validation engine orchestrator.

Parses raw X12, runs the envelope rules (SNIP 1-3), dispatches each transaction
to its implementation-guide validator (SNIP 2/4/6/7), then runs external
code-set validation (SNIP 5). Produces a single :class:`ValidationReport`.

Usage::

    from validation import validate_document
    report = validate_document(raw_x12_text)
    print(report.is_valid, report.error_count)
"""
from __future__ import annotations

import json
from pathlib import Path

from .model import SnipType, ValidationReport
from .parser import Transaction, X12Document, parse
from .rules import (
    common,
    guide_278,
    guide_835,
    guide_837,
    guide_claim_status,
    guide_eligibility,
    snip5_codeset,
)

# Transaction set -> implementation-guide validator. Each validator takes a
# Transaction + ValidationReport and returns its list of ClaimProjection.
# The 837 validator auto-selects the Professional / Institutional / Dental
# variant from the ST03 implementation version.
_GUIDES = {
    "837": guide_837.validate_837,
    "835": guide_835.validate_835,
    "270": guide_eligibility.validate_270,
    "271": guide_eligibility.validate_271,
    "276": guide_claim_status.validate_276,
    "277": guide_claim_status.validate_277,
    "278": guide_278.validate_278,
}

# SNIP levels exercised once a claim-bearing guide has run.
_CLAIM_SNIP_LEVELS = [
    int(SnipType.INTEGRITY),
    int(SnipType.REQUIREMENT),
    int(SnipType.BALANCING),
    int(SnipType.SITUATIONAL),
    int(SnipType.CODE_SET),
    int(SnipType.LINE_BALANCING),
    int(SnipType.GUIDE_SPECIFIC),
]

# SNIP levels exercised when only envelope validation could run.
_ENVELOPE_SNIP_LEVELS = [
    int(SnipType.INTEGRITY),
    int(SnipType.REQUIREMENT),
    int(SnipType.BALANCING),
]


def validate_document(
    text: str,
    *,
    source: str = "<memory>",
    expected_transaction: str | None = None,
) -> ValidationReport:
    """Validate raw X12 text and return a :class:`ValidationReport`."""
    doc = parse(text, source=source)
    return validate_parsed(
        doc, source=source, expected_transaction=expected_transaction
    )


def validate_parsed(
    doc: X12Document,
    *,
    source: str = "<memory>",
    expected_transaction: str | None = None,
) -> ValidationReport:
    """Validate an already-parsed :class:`X12Document`.

    Exposed separately so acknowledgment generators can parse once and reuse
    both the envelope tree and the validation result.
    """
    report = ValidationReport(source=source, standard="X12")
    report.interchange_count = len(doc.interchanges)
    report.segment_count = doc.segment_count
    report.group_count = sum(len(ic.groups) for ic in doc.interchanges)

    transactions = doc.transactions
    report.transaction_count = len(transactions)
    report.transaction_set = doc.primary_transaction_set
    report.implementation_version = doc.primary_version

    # SNIP 1-3 — envelope / control structure.
    common.validate_envelopes(doc, report)

    if (
        expected_transaction
        and report.transaction_set
        and report.transaction_set != expected_transaction
    ):
        from .model import Severity, ValidationIssue

        report.add(
            ValidationIssue(
                snip_type=SnipType.REQUIREMENT,
                severity=Severity.ERROR,
                code="ENV.TXN.UNEXPECTED",
                message=(
                    f"Expected transaction set {expected_transaction} but the "
                    f"document carries {report.transaction_set}."
                ),
                expected=expected_transaction,
                actual=report.transaction_set,
            )
        )

    # SNIP 2/4/6/7 — per-transaction implementation-guide validation.
    ran_guide = False
    for txn in transactions:
        guide = _GUIDES.get(txn.set_code)
        if guide is None:
            _note_unsupported(txn, report)
            continue
        claims = guide(txn, report)
        report.claims.extend(claims)
        ran_guide = True

    report.claim_count = len(report.claims)

    # SNIP 5 — external code sets, over the projected claims.
    if report.claims:
        snip5_codeset.validate_codesets(report)

    report.snip_levels_run = (
        _CLAIM_SNIP_LEVELS if ran_guide else _ENVELOPE_SNIP_LEVELS
    )
    return report


def validate_file(path: str | Path) -> ValidationReport:
    """Validate an X12 file on disk."""
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="ignore")
    return validate_document(text, source=str(p))


def _note_unsupported(txn: Transaction, report: ValidationReport) -> None:
    """Record an informational note for a transaction with no guide yet."""
    from .model import Severity, ValidationIssue

    report.add(
        ValidationIssue(
            snip_type=SnipType.REQUIREMENT,
            severity=Severity.INFO,
            code="ENV.GUIDE.UNSUPPORTED",
            message=(
                f"Transaction set {txn.set_code or '<unknown>'} "
                f"({txn.implementation_version or 'version unknown'}) has no "
                "implementation-guide validator yet; only envelope validation "
                "was applied."
            ),
            transaction_set=txn.set_code,
        )
    )


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate an X12 healthcare transaction (SNIP 1-7)."
    )
    parser.add_argument("input", type=Path, help="Path to an X12 file")
    parser.add_argument("--json", action="store_true", help="Emit the full JSON report")
    parser.add_argument(
        "--max-issues", type=int, default=40, help="Issues to print in summary mode"
    )
    args = parser.parse_args(argv)

    report = validate_file(args.input)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0 if report.is_valid else 1

    print(f"file:        {report.source}")
    print(f"transaction: {report.transaction_set} ({report.implementation_version})")
    print(f"segments:    {report.segment_count}")
    print(f"claims:      {report.claim_count}")
    print(f"valid:       {report.is_valid}")
    print(f"errors:      {len(report.errors)}   warnings: {len(report.warnings)}")
    print("snip summary:")
    for level, counts in report.snip_summary().items():
        print(f"  {level}: {counts['errors']} error(s), {counts['warnings']} warning(s)")
    if report.issues:
        print("issues:")
        for issue in report.issues[: args.max_issues]:
            ref = issue.element_ref or issue.segment_id or "-"
            print(
                f"  [{issue.severity.value.upper()}] snip{int(issue.snip_type)} "
                f"{issue.code} ({ref}): {issue.message}"
            )
        if len(report.issues) > args.max_issues:
            print(f"  ... {len(report.issues) - args.max_issues} more")
    return 0 if report.is_valid else 1


if __name__ == "__main__":
    raise SystemExit(_main())
