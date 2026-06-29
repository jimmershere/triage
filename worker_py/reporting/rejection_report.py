"""Flat-file human-readable rejection report (Workstream 7).

Flat-file submitters cannot read an X12 999. The migration blocker is a
position-keyed, plain-English report the mainframe automation can consume to
strip exactly the failed claims — while Triage *never edits the flat file*.

The correlation chain is:

    999 error (CTX CLM01 + IK3 position)  ->  CLM01  ->  flat-file position

At ingest we capture each flat-file claim's position (record offset / claim
ordinal) and its deterministic Claimtrace identity, and carry a CLM01 <->
flat-file-position map through 837 generation/validation. This module joins a
:class:`ValidationReport` (or a generated 999) back to those positions and emits
the report as **CSV + PDF + JSON**. The position-keyed JSON is the artifact
mainframe automation consumes.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from validation.model import ValidationIssue, ValidationReport
from validation.parser import parse


# ---------------------------------------------------------------------------
# Flat-file position model
# ---------------------------------------------------------------------------

@dataclass
class FlatFileClaim:
    """One claim's position in the originating flat file plus its identity."""

    position: int                      # 1-based claim ordinal within the file
    claim_id: str                      # CLM01 carried into the generated 837
    patient_control_number: str | None = None
    offset: int | None = None          # byte/record offset, if tracked
    claimtrace_id: str | None = None   # deterministic Claimtrace identity

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "claim_id": self.claim_id,
            "patient_control_number": self.patient_control_number,
            "offset": self.offset,
            "claimtrace_id": self.claimtrace_id,
        }


# 277CA-style status mapping. The 277CA cannot express "accepted with warning";
# rejections use claim-status category codes (CSCC) + claim-status codes (CSC).
def status_for_issue(issue: ValidationIssue) -> tuple[str, str]:
    """Map a validation issue to a (277 category, status) pair.

    A6/21 = rejected for missing information; A7/* = rejected for invalid
    information (with a more specific CSC for common code-set / NPI errors).
    """
    if "MISSING" in issue.code:
        return ("A6", "21")  # missing/invalid information
    code = issue.code
    if code.startswith("CODE.HI") or "ICD10" in code:
        return ("A7", "255")  # diagnosis code
    if "NPI" in code:
        return ("A7", "562")  # entity's National Provider Identifier (NPI)
    if code.startswith("CODE.") or "PROCEDURE" in code:
        return ("A7", "454")  # procedure code for services rendered
    if code.startswith("BAL"):
        return ("A7", "400")  # claim is out of balance
    return ("A7", "21")


@dataclass
class RejectionError:
    """One failure reason attached to a rejected claim."""

    error_code: str               # stable rule id (e.g. REQ.CLM01.ID)
    reason: str                   # plain-English explanation
    segment_id: str | None = None
    segment_position: int | None = None
    element_ref: str | None = None
    loop_id: str | None = None
    snip_type: int | None = None
    status_category: str | None = None  # 277CA CSCC (e.g. A7)
    status_code: str | None = None      # 277CA CSC

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class RejectionRow:
    """Report row for one flat-file claim."""

    flat_file_position: int
    claim_id: str
    patient_control_number: str | None = None
    claim_offset: int | None = None
    claimtrace_id: str | None = None
    passed: bool = True
    errors: list[RejectionError] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "pass" if self.passed else "fail"

    def to_dict(self) -> dict[str, Any]:
        return {
            "flat_file_position": self.flat_file_position,
            "claim_id": self.claim_id,
            "patient_control_number": self.patient_control_number,
            "claim_offset": self.claim_offset,
            "claimtrace_id": self.claimtrace_id,
            "status": self.status,
            "errors": [e.to_dict() for e in self.errors],
        }


@dataclass
class RejectionReport:
    """A complete rejection report for one submitted flat file."""

    rows: list[RejectionRow] = field(default_factory=list)
    source: str = "<flatfile>"
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.rows if r.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.rows if not r.passed)

    # -- Claimtrace linkage -------------------------------------------------

    def content_hash(self) -> str:
        """Deterministic hash of the position-keyed report content."""
        blob = json.dumps(self.to_json_dict(), sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_claimtrace_event(self, *, service_name: str = "reporting") -> dict[str, Any]:
        """Build an append-only Claimtrace event linking this report.

        The worker persists this via the journal store so every generated
        rejection report is traceable. The event id is content-derived so
        re-emitting the same report is idempotent.
        """
        content = self.content_hash()
        return {
            "operation_type": "rejection.report.generated",
            "service_name": service_name,
            "event_id": f"report-{content[:32]}",
            "content_hash": content,
            "source": self.source,
            "generated_at": self.generated_at.isoformat(),
            "claim_count": len(self.rows),
            "passed": self.passed_count,
            "failed": self.failed_count,
            "correlation_ids": {
                "flat_file_positions": [r.flat_file_position for r in self.rows if not r.passed],
                "claim_ids": [r.claim_id for r in self.rows if not r.passed],
                "claimtrace_ids": [r.claimtrace_id for r in self.rows if r.claimtrace_id],
            },
        }

    # -- JSON (position-keyed: what mainframe automation consumes) ----------

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "generated_at": self.generated_at.isoformat(),
            "claim_count": len(self.rows),
            "passed": self.passed_count,
            "failed": self.failed_count,
            "claims": {str(r.flat_file_position): r.to_dict() for r in self.rows},
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_json_dict(), indent=indent, sort_keys=False)

    # -- CSV ---------------------------------------------------------------

    _CSV_COLUMNS = [
        "flat_file_position",
        "claim_offset",
        "claim_id",
        "patient_control_number",
        "claimtrace_id",
        "status",
        "segment",
        "segment_position",
        "element",
        "loop",
        "error_code",
        "reason",
        "status_category",
        "status_code",
    ]

    def to_csv(self) -> str:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=self._CSV_COLUMNS)
        writer.writeheader()
        for row in self.rows:
            if row.passed or not row.errors:
                writer.writerow(
                    {
                        "flat_file_position": row.flat_file_position,
                        "claim_offset": row.claim_offset,
                        "claim_id": row.claim_id,
                        "patient_control_number": row.patient_control_number,
                        "claimtrace_id": row.claimtrace_id,
                        "status": row.status,
                    }
                )
                continue
            for err in row.errors:
                writer.writerow(
                    {
                        "flat_file_position": row.flat_file_position,
                        "claim_offset": row.claim_offset,
                        "claim_id": row.claim_id,
                        "patient_control_number": row.patient_control_number,
                        "claimtrace_id": row.claimtrace_id,
                        "status": row.status,
                        "segment": err.segment_id,
                        "segment_position": err.segment_position,
                        "element": err.element_ref,
                        "loop": err.loop_id,
                        "error_code": err.error_code,
                        "reason": err.reason,
                        "status_category": err.status_category,
                        "status_code": err.status_code,
                    }
                )
        return buf.getvalue()

    # -- PDF ---------------------------------------------------------------

    def render_pdf(self) -> bytes:
        """Render the report as a PDF. Requires the optional ``reportlab`` dep."""
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import letter
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.platypus import (
                Paragraph,
                SimpleDocTemplate,
                Spacer,
                Table,
                TableStyle,
            )
        except ImportError as exc:  # pragma: no cover - exercised only w/o reportlab
            raise RuntimeError(
                "PDF rendering requires the 'reportlab' package to be installed"
            ) from exc

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=letter, title="Claim Rejection Report")
        styles = getSampleStyleSheet()
        flow: list[Any] = [
            Paragraph("Triage Claim Rejection Report", styles["Title"]),
            Paragraph(f"Source file: {self.source}", styles["Normal"]),
            Paragraph(f"Generated: {self.generated_at.isoformat()}", styles["Normal"]),
            Paragraph(
                f"Claims: {len(self.rows)} &nbsp; Passed: {self.passed_count} "
                f"&nbsp; Failed: {self.failed_count}",
                styles["Normal"],
            ),
            Spacer(1, 12),
        ]
        data = [["Pos", "Claim ID", "PCN", "Status", "Reason(s)"]]
        for row in self.rows:
            reasons = (
                "\n".join(
                    f"[{e.error_code}] {e.reason}" for e in row.errors
                )
                if row.errors
                else "Accepted"
            )
            data.append(
                [
                    str(row.flat_file_position),
                    row.claim_id,
                    row.patient_control_number or "",
                    row.status.upper(),
                    Paragraph(reasons.replace("\n", "<br/>"), styles["BodyText"]),
                ]
            )
        table = Table(data, colWidths=[35, 90, 80, 50, 260], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ]
            )
        )
        flow.append(table)
        doc.build(flow)
        return buf.getvalue()


# ---------------------------------------------------------------------------
# Position-map construction
# ---------------------------------------------------------------------------

def position_map_from_flatfile(
    entries: Iterable[Mapping[str, Any]]
) -> dict[str, FlatFileClaim]:
    """Build a CLM01 -> :class:`FlatFileClaim` map from ingest position records.

    Each entry supplies at least ``claim_id``; ``position`` defaults to the
    1-based ordinal of appearance when absent.
    """
    out: dict[str, FlatFileClaim] = {}
    for ordinal, entry in enumerate(entries, start=1):
        claim_id = str(entry.get("claim_id") or "").strip()
        if not claim_id:
            continue
        out[claim_id] = FlatFileClaim(
            position=int(entry.get("position", ordinal)),
            claim_id=claim_id,
            patient_control_number=entry.get("patient_control_number"),
            offset=entry.get("offset"),
            claimtrace_id=entry.get("claimtrace_id"),
        )
    return out


def position_map_from_x12(text: str) -> dict[str, FlatFileClaim]:
    """Derive a CLM01 -> position map from an 837 whose claim order mirrors the
    originating flat file (claim ordinal = flat-file position)."""
    doc = parse(text)
    out: dict[str, FlatFileClaim] = {}
    ordinal = 0
    for txn in doc.transactions:
        if txn.set_code != "837":
            continue
        for seg in txn.segments:
            if seg.seg_id == "CLM":
                ordinal += 1
                clm01 = seg.elem(1).strip()
                if clm01:
                    out[clm01] = FlatFileClaim(
                        position=ordinal,
                        claim_id=clm01,
                        patient_control_number=clm01,
                    )
    return out


def _as_map(
    position_claims: Mapping[str, FlatFileClaim] | Iterable[Mapping[str, Any]]
) -> dict[str, FlatFileClaim]:
    if isinstance(position_claims, Mapping):
        return dict(position_claims)
    return position_map_from_flatfile(position_claims)


# ---------------------------------------------------------------------------
# Report builders
# ---------------------------------------------------------------------------

def build_report(
    position_claims: Mapping[str, FlatFileClaim] | Iterable[Mapping[str, Any]],
    report: ValidationReport,
    *,
    source: str = "<flatfile>",
) -> RejectionReport:
    """Join a :class:`ValidationReport` back to flat-file positions.

    Every claim in the position map gets a row; claims with one or more
    rejecting (FATAL/ERROR) findings are marked failed with per-error detail.
    """
    claim_map = _as_map(position_claims)
    errors_by_claim: dict[str, list[RejectionError]] = {}
    for issue in report.issues:
        if not issue.severity.rejects:
            continue
        if not issue.claim_id:
            continue
        category, status_code = status_for_issue(issue)
        errors_by_claim.setdefault(issue.claim_id, []).append(
            RejectionError(
                error_code=issue.code,
                reason=issue.message,
                segment_id=issue.segment_id,
                segment_position=issue.segment_position,
                element_ref=issue.element_ref,
                loop_id=issue.loop_id,
                snip_type=int(issue.snip_type),
                status_category=category,
                status_code=status_code,
            )
        )

    rejection = RejectionReport(source=source)
    for claim in sorted(claim_map.values(), key=lambda c: c.position):
        claim_errors = errors_by_claim.get(claim.claim_id, [])
        rejection.rows.append(
            RejectionRow(
                flat_file_position=claim.position,
                claim_id=claim.claim_id,
                patient_control_number=claim.patient_control_number,
                claim_offset=claim.offset,
                claimtrace_id=claim.claimtrace_id,
                passed=not claim_errors,
                errors=claim_errors,
            )
        )
    return rejection


# IK304 implementation segment syntax error code -> plain English.
_IK3_ERROR_REASON = {
    "1": "Unrecognized segment ID",
    "2": "Unexpected segment",
    "3": "Mandatory segment missing",
    "4": "Loop occurs over maximum times",
    "5": "Segment exceeds maximum use",
    "6": "Segment not in defined transaction set",
    "7": "Segment not in proper sequence",
    "8": "Segment has data element errors",
}


def build_report_from_999(
    ack_999_text: str,
    position_claims: Mapping[str, FlatFileClaim] | Iterable[Mapping[str, Any]],
    *,
    source: str = "<flatfile>",
) -> RejectionReport:
    """Build a rejection report from a generated 999, exercising the documented
    join: CTX CLM01 + IK3 position -> CLM01 -> flat-file position."""
    claim_map = _as_map(position_claims)
    doc = parse(ack_999_text)
    errors_by_claim: dict[str, list[RejectionError]] = {}

    for txn in doc.transactions:
        if txn.set_code != "999":
            continue
        pending: RejectionError | None = None
        for seg in txn.segments:
            if seg.seg_id == "IK3":
                code = seg.elem(4)
                pending = RejectionError(
                    error_code=f"IK3.{code}" if code else "IK3",
                    reason=_IK3_ERROR_REASON.get(code, "Segment error"),
                    segment_id=seg.elem(1) or None,
                    segment_position=int(seg.elem(2)) if seg.elem(2).isdigit() else None,
                    loop_id=seg.elem(3) or None,
                    status_category="A7",
                    status_code="21",
                )
            elif seg.seg_id == "CTX" and pending is not None:
                # CTX*CLM01:<value> business unit identifier.
                if seg.comp(1, 1) == "CLM01":
                    clm01 = seg.comp(1, 2)
                    if clm01:
                        errors_by_claim.setdefault(clm01, []).append(pending)
                        pending = None
            elif seg.seg_id == "IK4" and pending is not None:
                syntax = seg.elem(3)
                pending.reason += f"; element error code {syntax}" if syntax else ""

    rejection = RejectionReport(source=source)
    for claim in sorted(claim_map.values(), key=lambda c: c.position):
        claim_errors = errors_by_claim.get(claim.claim_id, [])
        rejection.rows.append(
            RejectionRow(
                flat_file_position=claim.position,
                claim_id=claim.claim_id,
                patient_control_number=claim.patient_control_number,
                claim_offset=claim.offset,
                claimtrace_id=claim.claimtrace_id,
                passed=not claim_errors,
                errors=claim_errors,
            )
        )
    return rejection
