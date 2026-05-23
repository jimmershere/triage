"""Data model for the claim-scrubbing engine."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum
from typing import Any


class ScrubSeverity(str, Enum):
    """Disposition severity of a scrubbing finding."""

    DENY = "deny"          # the line/claim would be denied as billed
    REVIEW = "review"      # suspend for manual review before submission
    ADVISORY = "advisory"  # informational — no payment impact on its own

    @property
    def blocks_submission(self) -> bool:
        return self in (ScrubSeverity.DENY, ScrubSeverity.REVIEW)


class EditCategory(str, Enum):
    """Category of CMS payment edit that produced a finding."""

    NCCI_PTP = "ncci_ptp"
    NCCI_MUE = "ncci_mue"
    COVERAGE = "coverage"
    MODIFIER = "modifier"
    DIAGNOSIS_SEQUENCING = "diagnosis_sequencing"
    DEMOGRAPHIC = "demographic"
    DUPLICATE = "duplicate"
    ELIGIBILITY = "eligibility"


@dataclass
class ScrubFinding:
    """One scrubbing finding against a claim or service line."""

    category: EditCategory
    severity: ScrubSeverity
    code: str                          # stable rule code, e.g. "PTP.BUNDLED"
    message: str
    claim_id: str | None = None
    line_no: str | None = None
    procedure_code: str | None = None
    related_code: str | None = None    # the other code in a pair edit
    diagnosis_code: str | None = None
    resolution: str | None = None      # suggested remediation
    source: str | None = None          # edit-table source / citation

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["category"] = self.category.value
        d["severity"] = self.severity.value
        return d


@dataclass
class ScrubReport:
    """Aggregate result of scrubbing one or more claims."""

    claim_count: int = 0
    findings: list[ScrubFinding] = field(default_factory=list)
    edits_run: list[str] = field(default_factory=list)

    def add(self, finding: ScrubFinding) -> None:
        self.findings.append(finding)

    @property
    def deny_findings(self) -> list[ScrubFinding]:
        return [f for f in self.findings if f.severity == ScrubSeverity.DENY]

    @property
    def review_findings(self) -> list[ScrubFinding]:
        return [f for f in self.findings if f.severity == ScrubSeverity.REVIEW]

    @property
    def is_clean(self) -> bool:
        """True when no finding blocks submission."""
        return not any(f.severity.blocks_submission for f in self.findings)

    def findings_for_claim(self, claim_id: str) -> list[ScrubFinding]:
        return [f for f in self.findings if f.claim_id == claim_id]

    def findings_for_category(self, category: EditCategory) -> list[ScrubFinding]:
        return [f for f in self.findings if f.category == category]

    def category_summary(self) -> dict[str, int]:
        summary: dict[str, int] = {}
        for finding in self.findings:
            summary[finding.category.value] = summary.get(finding.category.value, 0) + 1
        return summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_count": self.claim_count,
            "finding_count": len(self.findings),
            "deny_count": len(self.deny_findings),
            "review_count": len(self.review_findings),
            "clean": self.is_clean,
            "edits_run": self.edits_run,
            "category_summary": self.category_summary(),
            "findings": [f.to_dict() for f in self.findings],
        }


def parse_x12_date(value: str | None) -> date | None:
    """Parse an X12 D8 (CCYYMMDD) date string."""
    if not value or len(value) != 8 or not value.isdigit():
        return None
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        return None


def age_on(dob: str | None, service: str | None) -> int | None:
    """Compute patient age in whole years on a service date (both D8 strings)."""
    born = parse_x12_date(dob)
    dos = parse_x12_date(service)
    if born is None or dos is None:
        return None
    years = dos.year - born.year
    if (dos.month, dos.day) < (born.month, born.day):
        years -= 1
    return years if years >= 0 else None
