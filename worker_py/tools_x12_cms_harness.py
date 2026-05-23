#!/usr/bin/env python3
"""TurboHEDI X12 -> CMS authenticity harness.

Purpose:
- parse 837P/X222-ish X12 files
- verify a focused set of high-value implementation-guide rules
- project claims into a CMS-friendly JSON structure for downstream mapping work

This is not a full adjudication engine. It is a concrete verification harness meant to
prove the repo can parse real 837 content, check core control/data rules, and emit a
stable normalized payload for future CMS mapping.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any


@dataclass
class ValidationIssue:
    severity: str
    code: str
    message: str
    segment: str | None = None
    claim_id: str | None = None
    spec_ref: str | None = None


@dataclass
class ServiceLine:
    line_no: str | None = None
    procedure_code: str | None = None
    charge_amount: str | None = None
    units: str | None = None
    diagnosis_pointer: str | None = None
    raw_sv1: str | None = None


@dataclass
class CMSClaimProjection:
    claim_id: str | None = None
    total_charge: str | None = None
    claim_frequency_code: str | None = None
    place_of_service: str | None = None
    filing_indicator_code: str | None = None
    patient_control_number: str | None = None
    billing_provider_name: str | None = None
    billing_provider_npi: str | None = None
    billing_provider_tax_id: str | None = None
    subscriber_last_name: str | None = None
    subscriber_first_name: str | None = None
    subscriber_id: str | None = None
    patient_last_name: str | None = None
    patient_first_name: str | None = None
    payer_name: str | None = None
    payer_id: str | None = None
    diagnosis_codes: list[str] = field(default_factory=list)
    service_lines: list[ServiceLine] = field(default_factory=list)


@dataclass
class HarnessReport:
    file_path: str
    detected_transaction: str | None
    implementation_version: str | None
    segment_count: int
    claim_count: int
    valid: bool
    issues: list[ValidationIssue]
    claims: list[CMSClaimProjection]
    spec_bundle: dict[str, str]


SPEC_HINTS = {
    "ST01": "CSV seq 3: ST01 must be 837",
    "ST03": "CSV seq 5: ST03 implementation guide version required",
    "BHT01": "CSV seq 8: BHT01 must be 0019",
    "BHT02": "CSV seq 9 / context rows 8-9: BHT02 allowed 00 or 18",
    "BHT06": "CSV seq 13 / context rows 395-397: BHT06 allowed 31, CH, RP",
    "SUBMITTER": "CSV seq 17-24: Loop 1000A submitter NM1 required",
    "RECEIVER": "context row 31 / CSV seq around 50: Loop 1000B receiver NM1 required",
    "BILLING_PROVIDER": "context rows 409-411: Loop 2010AA billing provider NM1*85 required",
    "BILLING_PROVIDER_NPI": "context rows 44 / 410: NM109 NPI required in normal HIPAA flow",
    "BILLING_PROVIDER_TAX_ID": "context rows 416 / 112 / 327: REF*EI or REF*SY must be 9 digits",
    "CLAIM": "CLM segment required for claim projection",
    "CONTROL": "ISA/IEA, GS/GE, ST/SE control numbers must match",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _spec_paths() -> dict[str, Path]:
    docs_root = _repo_root().parent / "docs" / "turbohedi" / "x222-005010"
    return {
        "bundle_readme": docs_root / "README.md",
        "pdf": docs_root / "source" / "x222-005010-PDF.pdf",
        "csv": docs_root / "source" / "x222-005010-CSV.csv",
        "td_context": docs_root / "extracted" / "td" / "005010X222 Health Care Claim Professional" / "context.txt",
        "xsd": docs_root / "extracted" / "xsd" / "837-Q1A1.xsd",
    }


def _load_spec_bundle() -> dict[str, str]:
    return {k: str(v) for k, v in _spec_paths().items() if v.exists()}


def parse_x12(text: str) -> list[list[str]]:
    if not text.strip():
        return []
    elem_sep = "*"
    seg_sep = "~"
    if text.startswith("ISA") and len(text) >= 106:
        elem_sep = text[3]
        seg_sep = text[105]
    cleaned = text.replace("\r", "").replace("\n", "")
    return [seg.split(elem_sep) for seg in cleaned.split(seg_sep) if seg.strip()]


def _get(segments: list[list[str]], tag: str, start: int = 0) -> tuple[int | None, list[str] | None]:
    for idx in range(start, len(segments)):
        seg = segments[idx]
        if seg and seg[0].upper() == tag:
            return idx, seg
    return None, None


def _append_issue(issues: list[ValidationIssue], severity: str, code: str, message: str, *, segment: str | None = None, claim_id: str | None = None) -> None:
    issues.append(
        ValidationIssue(
            severity=severity,
            code=code,
            message=message,
            segment=segment,
            claim_id=claim_id,
            spec_ref=SPEC_HINTS.get(code),
        )
    )


def _safe_decimal(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return format(Decimal(value), "f")
    except Exception:
        return None


def _extract_claims(segments: list[list[str]], issues: list[ValidationIssue]) -> list[CMSClaimProjection]:
    claims: list[CMSClaimProjection] = []
    current_claim: CMSClaimProjection | None = None
    current_service_line: ServiceLine | None = None

    billing_provider_name = None
    billing_provider_npi = None
    billing_provider_tax_id = None
    subscriber_last = None
    subscriber_first = None
    subscriber_id = None
    patient_last = None
    patient_first = None
    payer_name = None
    payer_id = None
    diagnosis_codes: list[str] = []
    last_hl_code = None

    for seg in segments:
        tag = seg[0].upper()

        if tag == "HL" and len(seg) > 3:
            last_hl_code = seg[3]

        if tag == "NM1" and len(seg) > 1:
            entity = seg[1]
            if entity == "85":
                billing_provider_name = seg[3] if len(seg) > 3 else None
                if len(seg) > 9:
                    billing_provider_npi = seg[9] or None
            elif entity == "IL":
                subscriber_last = seg[3] if len(seg) > 3 else None
                subscriber_first = seg[4] if len(seg) > 4 else None
                subscriber_id = seg[9] if len(seg) > 9 else None
            elif entity == "QC":
                patient_last = seg[3] if len(seg) > 3 else None
                patient_first = seg[4] if len(seg) > 4 else None
            elif entity == "PR":
                payer_name = seg[3] if len(seg) > 3 else None
                payer_id = seg[9] if len(seg) > 9 else None

        elif tag == "REF" and len(seg) > 2:
            if seg[1] in {"EI", "SY"} and not billing_provider_tax_id:
                billing_provider_tax_id = seg[2]

        elif tag == "HI":
            for part in seg[1:]:
                if ":" in part:
                    code = part.split(":", 1)[1].strip()
                    if code:
                        diagnosis_codes.append(code)

        elif tag == "CLM":
            if current_service_line and current_claim:
                current_claim.service_lines.append(current_service_line)
                current_service_line = None
            if current_claim:
                current_claim.diagnosis_codes = list(dict.fromkeys(diagnosis_codes))
                claims.append(current_claim)
                diagnosis_codes = []
            current_claim = CMSClaimProjection(
                claim_id=seg[1] if len(seg) > 1 else None,
                patient_control_number=seg[1] if len(seg) > 1 else None,
                total_charge=_safe_decimal(seg[2] if len(seg) > 2 else None),
                place_of_service=(seg[5].split(":")[0] if len(seg) > 5 and seg[5] else None),
                claim_frequency_code=(seg[5].split(":")[-1] if len(seg) > 5 and ":" in seg[5] else None),
                billing_provider_name=billing_provider_name,
                billing_provider_npi=billing_provider_npi,
                billing_provider_tax_id=billing_provider_tax_id,
                subscriber_last_name=subscriber_last,
                subscriber_first_name=subscriber_first,
                subscriber_id=subscriber_id,
                patient_last_name=patient_last or subscriber_last,
                patient_first_name=patient_first or subscriber_first,
                payer_name=payer_name,
                payer_id=payer_id,
            )
            if current_claim.claim_id is None:
                _append_issue(issues, "error", "CLAIM", "CLM segment missing claim identifier", segment="CLM")

        elif tag == "SBR" and current_claim and len(seg) > 9:
            current_claim.filing_indicator_code = seg[9] or current_claim.filing_indicator_code

        elif tag == "LX":
            if current_service_line and current_claim:
                current_claim.service_lines.append(current_service_line)
            current_service_line = ServiceLine(line_no=seg[1] if len(seg) > 1 else None)

        elif tag == "SV1" and current_claim:
            if current_service_line is None:
                current_service_line = ServiceLine()
            composite = seg[1] if len(seg) > 1 else None
            proc = None
            if composite and ":" in composite:
                parts = composite.split(":")
                proc = parts[1] if len(parts) > 1 else parts[0]
            current_service_line.procedure_code = proc
            current_service_line.charge_amount = _safe_decimal(seg[2] if len(seg) > 2 else None)
            current_service_line.units = seg[4] if len(seg) > 4 else None
            current_service_line.diagnosis_pointer = seg[7] if len(seg) > 7 else None
            current_service_line.raw_sv1 = "*".join(seg)

    if current_service_line and current_claim:
        current_claim.service_lines.append(current_service_line)
    if current_claim:
        current_claim.diagnosis_codes = list(dict.fromkeys(diagnosis_codes))
        claims.append(current_claim)

    return claims


def validate(segments: list[list[str]]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    _, isa = _get(segments, "ISA")
    _, iea = _get(segments, "IEA")
    _, gs = _get(segments, "GS")
    _, ge = _get(segments, "GE")
    _, st = _get(segments, "ST")
    _, se = _get(segments, "SE")
    _, bht = _get(segments, "BHT")

    if isa and iea and len(isa) > 13 and len(iea) > 2 and isa[13] != iea[2]:
        _append_issue(issues, "error", "CONTROL", f"ISA13/IEA02 mismatch: {isa[13]} != {iea[2]}", segment="ISA/IEA")
    if gs and ge and len(gs) > 6 and len(ge) > 2 and gs[6] != ge[2]:
        _append_issue(issues, "error", "CONTROL", f"GS06/GE02 mismatch: {gs[6]} != {ge[2]}", segment="GS/GE")
    if st and se and len(st) > 2 and len(se) > 2 and st[2] != se[2]:
        _append_issue(issues, "error", "CONTROL", f"ST02/SE02 mismatch: {st[2]} != {se[2]}", segment="ST/SE")

    if not st:
        _append_issue(issues, "error", "ST01", "Missing ST segment", segment="ST")
    else:
        if len(st) <= 1 or st[1] != "837":
            _append_issue(issues, "error", "ST01", f"Expected ST01=837, got {st[1] if len(st) > 1 else None}", segment="ST")
        if len(st) <= 3 or "005010X222" not in st[3]:
            _append_issue(issues, "warning", "ST03", f"Expected ST03 to reference 005010X222, got {st[3] if len(st) > 3 else None}", segment="ST")

    if not bht:
        _append_issue(issues, "error", "BHT01", "Missing BHT segment", segment="BHT")
    else:
        if len(bht) <= 1 or bht[1] != "0019":
            _append_issue(issues, "error", "BHT01", f"Expected BHT01=0019, got {bht[1] if len(bht) > 1 else None}", segment="BHT")
        if len(bht) <= 2 or bht[2] not in {"00", "18"}:
            _append_issue(issues, "error", "BHT02", f"Expected BHT02 in {{00,18}}, got {bht[2] if len(bht) > 2 else None}", segment="BHT")
        if len(bht) <= 6 or bht[6] not in {"31", "CH", "RP"}:
            _append_issue(issues, "warning", "BHT06", f"Expected BHT06 in {{31,CH,RP}}, got {bht[6] if len(bht) > 6 else None}", segment="BHT")

    submitter_ok = any(seg[0] == "NM1" and len(seg) > 1 and seg[1] == "41" for seg in segments)
    receiver_ok = any(seg[0] == "NM1" and len(seg) > 1 and seg[1] == "40" for seg in segments)
    billing_ok = any(seg[0] == "NM1" and len(seg) > 1 and seg[1] == "85" for seg in segments)
    if not submitter_ok:
        _append_issue(issues, "error", "SUBMITTER", "Missing Loop 1000A submitter NM1*41", segment="NM1")
    if not receiver_ok:
        _append_issue(issues, "error", "RECEIVER", "Missing Loop 1000B receiver NM1*40", segment="NM1")
    if not billing_ok:
        _append_issue(issues, "error", "BILLING_PROVIDER", "Missing Loop 2010AA billing provider NM1*85", segment="NM1")

    for seg in segments:
        tag = seg[0]
        if tag == "NM1" and len(seg) > 9 and seg[1] == "85":
            qualifier = seg[8] if len(seg) > 8 else None
            ident = seg[9] if len(seg) > 9 else None
            if qualifier == "XX" and ident and not re.fullmatch(r"\d{10}", ident):
                _append_issue(issues, "error", "BILLING_PROVIDER_NPI", f"Billing provider NPI must be 10 digits, got {ident}", segment="NM1")
        elif tag == "REF" and len(seg) > 2 and seg[1] in {"EI", "SY"}:
            if not re.fullmatch(r"\d{9}", seg[2]):
                _append_issue(issues, "error", "BILLING_PROVIDER_TAX_ID", f"Tax ID must be 9 digits, got {seg[2]}", segment="REF")

    if not any(seg[0] == "CLM" for seg in segments):
        _append_issue(issues, "error", "CLAIM", "No CLM segments found", segment="CLM")

    return issues


def run_harness(path: Path) -> HarnessReport:
    text = path.read_text(encoding="utf-8", errors="ignore")
    segments = parse_x12(text)
    issues = validate(segments)
    claims = _extract_claims(segments, issues)
    _, st = _get(segments, "ST")
    detected = st[1] if st and len(st) > 1 else None
    version = st[3] if st and len(st) > 3 else None
    valid = not any(issue.severity == "error" for issue in issues)
    return HarnessReport(
        file_path=str(path),
        detected_transaction=detected,
        implementation_version=version,
        segment_count=len(segments),
        claim_count=len(claims),
        valid=valid,
        issues=issues,
        claims=claims,
        spec_bundle=_load_spec_bundle(),
    )


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify X12 837 payloads and emit CMS projection JSON")
    parser.add_argument("input", type=Path, help="Path to X12 file")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print summary")
    args = parser.parse_args()

    report = run_harness(args.input)
    if args.pretty:
        print(f"file: {report.file_path}")
        print(f"transaction: {report.detected_transaction}")
        print(f"version: {report.implementation_version}")
        print(f"segments: {report.segment_count}")
        print(f"claims: {report.claim_count}")
        print(f"valid: {report.valid}")
        print("issues:")
        for issue in report.issues[:40]:
            print(f"- {issue.severity.upper()} {issue.code}: {issue.message}")
        if len(report.issues) > 40:
            print(f"- ... {len(report.issues) - 40} more")
        print("spec_bundle:")
        for key, value in report.spec_bundle.items():
            print(f"- {key}: {value}")
    else:
        print(json.dumps(report, default=_json_default, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
