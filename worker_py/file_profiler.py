"""TurboHEDI Ingest Profiler — deterministic file metadata extraction.

Extracts cheap, deterministic metadata from inbound EDI files before any AI
step.  Produces a normalized FileProfile used by the complexity scorer and
routing engine.

Reference: 2026-05-13 Swarm Escalation MVP Design Pack § Scoring Model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4


@dataclass
class FileProfile:
    """Deterministic metadata snapshot for a single inbound EDI file."""

    file_id: str
    received_at: str  # ISO-8601
    partner_id: str | None = None
    source_system: str | None = None

    # Document identity
    document_standard: str | None = None  # X12, EDIFACT, UNKNOWN
    document_version: str | None = None   # e.g. 005010X222A1
    transaction_sets: list[str] = field(default_factory=list)  # e.g. ["837"]

    # Structural metrics
    segment_count: int = 0
    transaction_count: int = 0
    loop_count: int = 0
    byte_size: int = 0

    # Irregularity / novelty signals
    mixed_transaction_types: bool = False
    loop_irregularity_markers: list[str] = field(default_factory=list)
    custom_segment_present: bool = False
    schema_match_confidence: float = 1.0  # 0.0–1.0
    map_match_confidence: float = 1.0     # 0.0–1.0

    # Validation signals (populated after harness run)
    validation_anomaly_count: int = 0
    validation_anomaly_types: list[str] = field(default_factory=list)

    # Historical / contextual (populated from DB lookups)
    historical_failure_rate: float = 0.0  # 0.0–1.0
    retry_count: int = 0
    partner_reliability_tier: str = "unknown"  # stable, variable, new, unknown

    # Business signals (populated from config or partner metadata)
    business_criticality_class: str = "normal"  # low, normal, high, critical
    sla_class: str = "standard"                 # relaxed, standard, urgent
    compliance_materiality_flag: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON storage / audit logging."""
        return {
            "file_id": self.file_id,
            "received_at": self.received_at,
            "partner_id": self.partner_id,
            "source_system": self.source_system,
            "document_standard": self.document_standard,
            "document_version": self.document_version,
            "transaction_sets": self.transaction_sets,
            "segment_count": self.segment_count,
            "transaction_count": self.transaction_count,
            "loop_count": self.loop_count,
            "byte_size": self.byte_size,
            "mixed_transaction_types": self.mixed_transaction_types,
            "loop_irregularity_markers": self.loop_irregularity_markers,
            "custom_segment_present": self.custom_segment_present,
            "schema_match_confidence": self.schema_match_confidence,
            "map_match_confidence": self.map_match_confidence,
            "validation_anomaly_count": self.validation_anomaly_count,
            "validation_anomaly_types": self.validation_anomaly_types,
            "historical_failure_rate": self.historical_failure_rate,
            "retry_count": self.retry_count,
            "partner_reliability_tier": self.partner_reliability_tier,
            "business_criticality_class": self.business_criticality_class,
            "sla_class": self.sla_class,
            "compliance_materiality_flag": self.compliance_materiality_flag,
        }


# Known X12 loop-opening segments for irregularity detection
_LOOP_OPENERS = {"HL", "NM1", "CLM", "LX", "SBR", "PAT"}

# Known X12 transaction set codes we expect to handle
_KNOWN_TX_SETS = {"837", "835", "270", "271", "276", "277", "999"}

# Standard X12 segment tags (non-exhaustive but covers HIPAA transactions)
_STANDARD_SEGMENTS = {
    "ISA", "IEA", "GS", "GE", "ST", "SE", "BHT", "HL", "NM1", "N3", "N4",
    "REF", "PER", "CLM", "DTP", "AMT", "SV1", "SV2", "SV3", "LX", "HI",
    "SBR", "PAT", "DMG", "OI", "MOA", "CAS", "CLP", "SVC", "PLB", "BPR",
    "TRN", "DTM", "QTY", "EB", "LS", "LE", "EQ", "III", "AAA", "MSG",
    "CRC", "HSD", "PWK", "CR1", "CR2", "CR3", "CR5", "CR6", "CR7",
    "CTP", "MEA", "CN1", "K3", "NTE", "PS1", "LQ", "FRM", "SVD", "LIN",
    "CUR", "PRV", "TA1",
}


def _parse_segments(text: str) -> list[list[str]]:
    """Parse X12 text into segment lists.  Mirrors harness parser."""
    if not text.strip():
        return []
    elem_sep = "*"
    seg_sep = "~"
    if text.startswith("ISA") and len(text) >= 106:
        elem_sep = text[3]
        seg_sep = text[105]
    cleaned = text.replace("\r", "").replace("\n", "")
    return [seg.split(elem_sep) for seg in cleaned.split(seg_sep) if seg.strip()]


def profile_x12(
    text: str,
    *,
    file_id: str | None = None,
    partner_id: str | None = None,
    trading_partner_id: str | None = None,
    byte_size: int | None = None,
) -> FileProfile:
    """Build a FileProfile from raw X12 text content."""
    segments = _parse_segments(text)
    now = datetime.now(timezone.utc).isoformat()

    profile = FileProfile(
        file_id=file_id or uuid4().hex,
        received_at=now,
        partner_id=partner_id or trading_partner_id,
        document_standard="X12",
        segment_count=len(segments),
        byte_size=byte_size if byte_size is not None else len(text.encode("utf-8")),
    )

    # Extract transaction sets and version from ST segments
    tx_sets: list[str] = []
    st_versions: list[str] = []
    for seg in segments:
        if seg and seg[0].upper() == "ST":
            if len(seg) > 1:
                tx_sets.append(seg[1])
            if len(seg) > 3:
                st_versions.append(seg[3])

    profile.transaction_sets = list(dict.fromkeys(tx_sets))
    profile.transaction_count = len(tx_sets)
    profile.mixed_transaction_types = len(set(tx_sets)) > 1

    if st_versions:
        profile.document_version = st_versions[0]

    # Schema/map match confidence
    unknown_tx = [t for t in tx_sets if t not in _KNOWN_TX_SETS]
    if unknown_tx:
        profile.schema_match_confidence = max(0.0, 1.0 - 0.3 * len(unknown_tx))
    if profile.document_version and "005010" not in profile.document_version:
        profile.map_match_confidence = 0.5

    # Version mismatch across envelopes — different versions in same interchange
    unique_versions = list(dict.fromkeys(st_versions))
    if len(unique_versions) > 1:
        # Mixed versions = lower schema confidence proportional to variance
        profile.schema_match_confidence = min(
            profile.schema_match_confidence,
            max(0.3, 1.0 - 0.2 * (len(unique_versions) - 1)),
        )

    # Compliance materiality: mixed healthcare transaction types require careful handling
    healthcare_tx = {"837", "835", "276", "277", "270", "271", "278", "834"}
    present_hc = set(tx_sets) & healthcare_tx
    if len(present_hc) > 1:
        profile.compliance_materiality_flag = True

    # Loop counting and irregularity detection
    loop_count = 0
    hl_levels: list[str] = []
    irregularities: list[str] = []

    for seg in segments:
        tag = seg[0].upper()
        if tag in _LOOP_OPENERS:
            loop_count += 1
        if tag == "HL" and len(seg) > 3:
            hl_levels.append(seg[3])

    profile.loop_count = loop_count

    # Check for unusual HL nesting patterns
    if hl_levels:
        expected_order = ["20", "22", "23"]  # typical 837 subscriber/patient hierarchy
        seen = []
        for level in hl_levels:
            if level not in seen:
                seen.append(level)
        if seen and seen != expected_order[:len(seen)]:
            irregularities.append(f"unusual_hl_hierarchy:{','.join(seen)}")

    # Detect segments not in standard set
    segment_tags = {seg[0].upper() for seg in segments if seg}
    custom_tags = segment_tags - _STANDARD_SEGMENTS
    if custom_tags:
        profile.custom_segment_present = True
        irregularities.append(f"custom_segments:{','.join(sorted(custom_tags))}")

    # Check for control number mismatches (quick structural check)
    isa_ctrl = None
    iea_ctrl = None
    for seg in segments:
        tag = seg[0].upper()
        if tag == "ISA" and len(seg) > 13:
            isa_ctrl = seg[13].strip()
        elif tag == "IEA" and len(seg) > 2:
            iea_ctrl = seg[2].strip()
    if isa_ctrl and iea_ctrl and isa_ctrl != iea_ctrl:
        irregularities.append("control_number_mismatch")

    profile.loop_irregularity_markers = irregularities

    return profile


def profile_edifact(
    text: str,
    *,
    file_id: str | None = None,
    partner_id: str | None = None,
    byte_size: int | None = None,
) -> FileProfile:
    """Basic FileProfile for EDIFACT content."""
    now = datetime.now(timezone.utc).isoformat()
    # Simple segment count for EDIFACT (apostrophe-delimited)
    segments = [s.strip() for s in text.split("'") if s.strip()]

    profile = FileProfile(
        file_id=file_id or uuid4().hex,
        received_at=now,
        partner_id=partner_id,
        document_standard="EDIFACT",
        segment_count=len(segments),
        byte_size=byte_size if byte_size is not None else len(text.encode("utf-8")),
    )

    # Extract message type from UNH
    for seg in segments:
        if seg.startswith("UNH"):
            parts = seg.split("+")
            if len(parts) > 2:
                msg_parts = parts[2].split(":")
                if msg_parts:
                    profile.transaction_sets = [msg_parts[0]]
                    profile.transaction_count = 1
            break

    return profile


def profile_file(
    text: str,
    *,
    file_id: str | None = None,
    partner_id: str | None = None,
    trading_partner_id: str | None = None,
    byte_size: int | None = None,
) -> FileProfile:
    """Auto-detect format and build a FileProfile."""
    head = text.strip()[:3].upper()
    if head == "ISA":
        return profile_x12(
            text,
            file_id=file_id,
            partner_id=partner_id,
            trading_partner_id=trading_partner_id,
            byte_size=byte_size,
        )
    elif head in ("UNB", "UNH"):
        return profile_edifact(
            text,
            file_id=file_id,
            partner_id=partner_id,
            byte_size=byte_size,
        )
    else:
        return FileProfile(
            file_id=file_id or uuid4().hex,
            received_at=datetime.now(timezone.utc).isoformat(),
            partner_id=partner_id or trading_partner_id,
            document_standard="UNKNOWN",
            byte_size=byte_size if byte_size is not None else len(text.encode("utf-8")),
            schema_match_confidence=0.0,
            map_match_confidence=0.0,
        )


def enrich_with_harness(profile: FileProfile, harness_report: Any) -> FileProfile:
    """Enrich a FileProfile with validation results from the CMS harness."""
    if harness_report is None:
        return profile

    error_issues = [i for i in harness_report.issues if i.severity == "error"]
    warning_issues = [i for i in harness_report.issues if i.severity == "warning"]

    profile.validation_anomaly_count = len(error_issues) + len(warning_issues)
    codes = list(dict.fromkeys(i.code for i in harness_report.issues))
    profile.validation_anomaly_types = codes

    # Adjust map confidence if harness found structural issues
    if error_issues:
        profile.map_match_confidence = min(
            profile.map_match_confidence,
            max(0.1, 1.0 - 0.1 * len(error_issues)),
        )

    return profile
