"""Core data model for the TurboHEDI X12 validation engine.

Defines the SNIP taxonomy, severity levels, the :class:`ValidationIssue` record,
claim/service-line projections, and the :class:`ValidationReport` aggregate that
every validation run produces.

Reference: WEDI Strategic National Implementation Process (SNIP) transaction
compliance types 1-7.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class SnipType(int, Enum):
    """WEDI SNIP transaction testing types.

    Each type is a progressively deeper level of EDI conformance checking.
    A claim must pass all seven to be considered fully HIPAA-compliant.
    """

    INTEGRITY = 1          # EDI syntax: valid segments, elements, delimiters
    REQUIREMENT = 2        # HIPAA implementation-guide required fields present
    BALANCING = 3          # Transaction totals balance (amounts, counts)
    SITUATIONAL = 4        # Inter-segment situational data relationships
    CODE_SET = 5           # External code sets valid (ICD-10, HCPCS, POS, ...)
    LINE_BALANCING = 6     # Product/service line-level balancing
    GUIDE_SPECIFIC = 7     # Trading-partner / implementation-guide specific rules

    @property
    def label(self) -> str:
        return _SNIP_LABELS[self]


_SNIP_LABELS: dict["SnipType", str] = {
    SnipType.INTEGRITY: "EDI Standard Integrity",
    SnipType.REQUIREMENT: "HIPAA Implementation Guide Requirement",
    SnipType.BALANCING: "HIPAA Balancing",
    SnipType.SITUATIONAL: "HIPAA Situational",
    SnipType.CODE_SET: "External Code Set",
    SnipType.LINE_BALANCING: "Product/Service Line Balancing",
    SnipType.GUIDE_SPECIFIC: "Implementation Guide Specific",
}


class Severity(str, Enum):
    """Severity of a validation finding."""

    FATAL = "fatal"      # cannot continue parsing/processing
    ERROR = "error"      # rejects the claim/transaction
    WARNING = "warning"  # accepted but flagged
    INFO = "info"        # informational only

    @property
    def rejects(self) -> bool:
        """True when a finding at this severity blocks acceptance."""
        return self in (Severity.FATAL, Severity.ERROR)


# Severity ordering for sorting / comparison.
_SEVERITY_RANK = {
    Severity.FATAL: 0,
    Severity.ERROR: 1,
    Severity.WARNING: 2,
    Severity.INFO: 3,
}


@dataclass
class ValidationIssue:
    """A single validation finding.

    The location fields are all optional so the same record works for an
    interchange-level integrity error and a deep service-line situational error.
    """

    snip_type: SnipType
    severity: Severity
    code: str                     # stable, grep-able rule code, e.g. "REQ.CLM.MISSING"
    message: str
    segment_id: str | None = None         # e.g. "CLM"
    segment_position: int | None = None   # 1-based segment index within transaction
    element_position: int | None = None   # 1-based element index (CLM05 -> 5)
    component_position: int | None = None # 1-based component within a composite
    loop_id: str | None = None            # e.g. "2300", "2400"
    transaction_set: str | None = None    # e.g. "837"
    transaction_control: str | None = None  # ST02 of the owning transaction
    claim_id: str | None = None
    line_no: str | None = None
    expected: str | None = None
    actual: str | None = None
    spec_ref: str | None = None           # implementation-guide citation

    @property
    def element_ref(self) -> str | None:
        """Human-readable element reference, e.g. ``CLM05`` or ``CLM05-3``."""
        if not self.segment_id or self.element_position is None:
            return None
        ref = f"{self.segment_id}{self.element_position:02d}"
        if self.component_position is not None:
            ref += f"-{self.component_position}"
        return ref

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["snip_type"] = int(self.snip_type)
        d["snip_label"] = self.snip_type.label
        d["severity"] = self.severity.value
        d["element_ref"] = self.element_ref
        return d


@dataclass
class ServiceLineProjection:
    """Normalized service line extracted from a claim."""

    line_no: str | None = None
    procedure_code: str | None = None
    procedure_qualifier: str | None = None
    modifiers: list[str] = field(default_factory=list)
    charge_amount: str | None = None
    units: str | None = None
    unit_basis: str | None = None
    diagnosis_pointers: list[str] = field(default_factory=list)
    place_of_service: str | None = None
    service_date: str | None = None
    revenue_code: str | None = None   # institutional (837I)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClaimProjection:
    """Normalized, CMS-friendly projection of one claim."""

    claim_id: str | None = None
    patient_control_number: str | None = None
    total_charge: str | None = None
    place_of_service: str | None = None
    facility_code: str | None = None
    type_of_bill: str | None = None        # institutional (837I) CLM05-1
    claim_frequency_code: str | None = None
    filing_indicator_code: str | None = None
    provider_signature_on_file: str | None = None
    billing_provider_name: str | None = None
    billing_provider_npi: str | None = None
    billing_provider_tax_id: str | None = None
    rendering_provider_npi: str | None = None
    subscriber_last_name: str | None = None
    subscriber_first_name: str | None = None
    subscriber_id: str | None = None
    patient_last_name: str | None = None
    patient_first_name: str | None = None
    patient_dob: str | None = None
    patient_gender: str | None = None
    payer_name: str | None = None
    payer_id: str | None = None
    statement_from_date: str | None = None
    statement_to_date: str | None = None
    diagnosis_codes: list[str] = field(default_factory=list)
    service_lines: list[ServiceLineProjection] = field(default_factory=list)

    @property
    def line_charge_total(self) -> float:
        total = 0.0
        for line in self.service_lines:
            if line.charge_amount:
                try:
                    total += float(line.charge_amount)
                except (TypeError, ValueError):
                    pass
        return round(total, 2)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["line_charge_total"] = self.line_charge_total
        return d


@dataclass
class ValidationReport:
    """Aggregate result of validating one X12 document."""

    source: str = "<memory>"
    transaction_set: str | None = None        # 837, 835, 270, ...
    implementation_version: str | None = None  # 005010X222A1
    standard: str = "X12"
    interchange_count: int = 0
    group_count: int = 0
    transaction_count: int = 0
    segment_count: int = 0
    claim_count: int = 0
    issues: list[ValidationIssue] = field(default_factory=list)
    claims: list[ClaimProjection] = field(default_factory=list)
    snip_levels_run: list[int] = field(default_factory=list)

    # -- issue helpers ------------------------------------------------------

    def add(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity.rejects]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def is_valid(self) -> bool:
        """True when no fatal/error issue was raised."""
        return not self.errors

    def issues_for_snip(self, snip: SnipType) -> list[ValidationIssue]:
        return [i for i in self.issues if i.snip_type == snip]

    def issues_for_claim(self, claim_id: str) -> list[ValidationIssue]:
        return [i for i in self.issues if i.claim_id == claim_id]

    def highest_severity(self) -> Severity | None:
        if not self.issues:
            return None
        return min((i.severity for i in self.issues), key=lambda s: _SEVERITY_RANK[s])

    # -- serialization ------------------------------------------------------

    def snip_summary(self) -> dict[str, dict[str, int]]:
        """Per-SNIP-level error/warning counts."""
        summary: dict[str, dict[str, int]] = {}
        for snip in SnipType:
            if int(snip) not in self.snip_levels_run:
                continue
            level_issues = self.issues_for_snip(snip)
            summary[f"snip{int(snip)}_{snip.name.lower()}"] = {
                "errors": sum(1 for i in level_issues if i.severity.rejects),
                "warnings": sum(1 for i in level_issues if i.severity == Severity.WARNING),
            }
        return summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "standard": self.standard,
            "transaction_set": self.transaction_set,
            "implementation_version": self.implementation_version,
            "interchange_count": self.interchange_count,
            "group_count": self.group_count,
            "transaction_count": self.transaction_count,
            "segment_count": self.segment_count,
            "claim_count": self.claim_count,
            "valid": self.is_valid,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "snip_levels_run": sorted(self.snip_levels_run),
            "snip_summary": self.snip_summary(),
            "issues": [i.to_dict() for i in self.issues],
            "claims": [c.to_dict() for c in self.claims],
        }
