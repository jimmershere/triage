"""Loop-aware X12 parser.

Parses raw X12 text into a positioned envelope tree (Interchange -> FunctionalGroup
-> Transaction -> Segment) and collects structural parse issues as it goes.

The parser is deliberately resilient: malformed input still yields a partial tree
plus :class:`ValidationIssue` records (SNIP type 1) so downstream validation can
continue and report as much as possible.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import Severity, SnipType, ValidationIssue

# Length of a well-formed ISA segment including its segment terminator.
ISA_LEN = 106


@dataclass
class Delimiters:
    """Delimiter set for one interchange."""

    element: str = "*"
    component: str = ":"
    repetition: str = "^"
    segment: str = "~"

    def to_dict(self) -> dict[str, str]:
        return {
            "element": self.element,
            "component": self.component,
            "repetition": self.repetition,
            "segment": self.segment,
        }


@dataclass
class Segment:
    """One parsed X12 segment.

    ``elements`` holds the *data* elements only — element 01 is ``elements[0]``.
    Use :meth:`elem` for 1-based, bounds-safe access matching X12 notation.
    """

    seg_id: str
    elements: list[str]
    position: int                 # 1-based index within the enclosing transaction
    raw: str
    _component_sep: str = ":"

    def elem(self, n: int) -> str:
        """Return data element ``n`` (1-based), or ``""`` if absent."""
        if n < 1 or n > len(self.elements):
            return ""
        return self.elements[n - 1]

    def has_elem(self, n: int) -> bool:
        return 1 <= n <= len(self.elements) and self.elements[n - 1] != ""

    def comp(self, n: int, c: int) -> str:
        """Return component ``c`` (1-based) of composite data element ``n``."""
        value = self.elem(n)
        if not value:
            return ""
        parts = value.split(self._component_sep)
        if c < 1 or c > len(parts):
            return ""
        return parts[c - 1]

    def components(self, n: int) -> list[str]:
        """Return all components of data element ``n``."""
        value = self.elem(n)
        if not value:
            return []
        return value.split(self._component_sep)

    @property
    def max_element(self) -> int:
        return len(self.elements)


@dataclass
class Transaction:
    """One ST..SE transaction set."""

    set_code: str                  # ST01: 837, 835, 270, ...
    control_number: str            # ST02
    implementation_version: str    # ST03 (5010 implementation convention reference)
    segments: list[Segment] = field(default_factory=list)
    st: Segment | None = None
    se: Segment | None = None

    @property
    def declared_segment_count(self) -> int | None:
        """SE01 — segment count declared by the transaction trailer."""
        if self.se is None or not self.se.has_elem(1):
            return None
        try:
            return int(self.se.elem(1))
        except ValueError:
            return None

    @property
    def actual_segment_count(self) -> int:
        """Actual ST..SE inclusive segment count."""
        return len(self.segments)

    def find_all(self, seg_id: str) -> list[Segment]:
        return [s for s in self.segments if s.seg_id == seg_id]

    def first(self, seg_id: str) -> Segment | None:
        for s in self.segments:
            if s.seg_id == seg_id:
                return s
        return None


@dataclass
class FunctionalGroup:
    """One GS..GE functional group."""

    functional_id: str             # GS01: HC, HP, HB, HN, HR, HS, ...
    control_number: str            # GS06
    version: str                   # GS08
    transactions: list[Transaction] = field(default_factory=list)
    gs: Segment | None = None
    ge: Segment | None = None

    @property
    def declared_transaction_count(self) -> int | None:
        """GE01 — transaction count declared by the group trailer."""
        if self.ge is None or not self.ge.has_elem(1):
            return None
        try:
            return int(self.ge.elem(1))
        except ValueError:
            return None


@dataclass
class Interchange:
    """One ISA..IEA interchange envelope."""

    delimiters: Delimiters
    sender_id: str = ""
    receiver_id: str = ""
    control_number: str = ""       # ISA13
    usage_indicator: str = ""      # ISA15: P (production) / T (test)
    groups: list[FunctionalGroup] = field(default_factory=list)
    isa: Segment | None = None
    iea: Segment | None = None

    @property
    def declared_group_count(self) -> int | None:
        """IEA01 — group count declared by the interchange trailer."""
        if self.iea is None or not self.iea.has_elem(1):
            return None
        try:
            return int(self.iea.elem(1))
        except ValueError:
            return None


@dataclass
class X12Document:
    """Parsed X12 document — one or more interchanges."""

    raw: str
    delimiters: Delimiters
    interchanges: list[Interchange] = field(default_factory=list)
    parse_issues: list[ValidationIssue] = field(default_factory=list)
    segment_count: int = 0

    @property
    def transactions(self) -> list[Transaction]:
        out: list[Transaction] = []
        for ic in self.interchanges:
            for grp in ic.groups:
                out.extend(grp.transactions)
        return out

    @property
    def primary_transaction_set(self) -> str | None:
        for txn in self.transactions:
            if txn.set_code:
                return txn.set_code
        return None

    @property
    def primary_version(self) -> str | None:
        for txn in self.transactions:
            if txn.implementation_version:
                return txn.implementation_version
        return None


def _scan_terminator(text: str) -> str | None:
    """Recover the segment terminator from a malformed ISA header.

    Locates the GS/TA1/IEA/ST segment that follows the ISA and returns the
    (non-alphanumeric, non-whitespace) character immediately preceding it.
    """
    for tag in ("GS", "TA1", "IEA", "ST"):
        idx = text.find(tag, 3)
        while idx != -1:
            j = idx - 1
            while j >= 0 and text[j] in "\r\n":
                j -= 1
            if j >= 3:
                cand = text[j]
                if not cand.isalnum() and not cand.isspace():
                    return cand
            idx = text.find(tag, idx + 1)
    return None


def detect_delimiters(text: str) -> tuple[Delimiters, ValidationIssue | None]:
    """Detect the delimiter set from a leading ISA segment.

    A well-formed ISA is exactly 106 characters with the segment terminator at
    offset 105. When the ISA padding is off (a common real-world defect) the
    terminator is recovered by scanning, and an integrity issue is reported.
    """
    spec = "X12 005010 Appendix B — Interchange Control Structure"
    if text.startswith("ISA") and len(text) >= ISA_LEN:
        seg_term = text[105]
        if not seg_term.isalnum() and not seg_term.isspace():
            return (
                Delimiters(
                    element=text[3],
                    component=text[104],
                    repetition=text[82],
                    segment=seg_term,
                ),
                None,
            )
        # ISA is present but not a well-formed 106-character header — recover.
        recovered = _scan_terminator(text)
        if recovered is not None:
            isa_end = text.find(recovered)
            component = text[isa_end - 1] if isa_end > 4 else ":"
            issue = ValidationIssue(
                snip_type=SnipType.INTEGRITY,
                severity=Severity.ERROR,
                code="INT.ISA.LENGTH",
                message=(
                    "ISA header is not the required 106 characters; segment "
                    "delimiters were recovered by scanning the interchange."
                ),
                segment_id="ISA",
                spec_ref=spec,
            )
            return (
                Delimiters(
                    element=text[3],
                    component=component,
                    repetition="^",
                    segment=recovered,
                ),
                issue,
            )

    issue = ValidationIssue(
        snip_type=SnipType.INTEGRITY,
        severity=Severity.FATAL if not text.startswith("ISA") else Severity.ERROR,
        code="INT.ISA.HEADER",
        message=(
            "Interchange does not begin with a well-formed ISA header; using "
            "conventional delimiters (* : ^ ~)."
        ),
        segment_id="ISA",
        spec_ref=spec,
    )
    return Delimiters(), issue


def _split_segments(text: str, delim: Delimiters) -> list[str]:
    cleaned = text.replace("\r", "").replace("\n", "")
    return [s for s in cleaned.split(delim.segment) if s.strip()]


def parse(text: str, *, source: str = "<memory>") -> X12Document:
    """Parse raw X12 text into an :class:`X12Document`."""
    delim, delim_issue = detect_delimiters(text)
    doc = X12Document(raw=text, delimiters=delim)
    if delim_issue is not None:
        doc.parse_issues.append(delim_issue)

    raw_segments = _split_segments(text, delim)
    doc.segment_count = len(raw_segments)
    if not raw_segments:
        doc.parse_issues.append(
            ValidationIssue(
                snip_type=SnipType.INTEGRITY,
                severity=Severity.FATAL,
                code="INT.EMPTY",
                message="No X12 segments could be parsed from the input.",
            )
        )
        return doc

    current_ic: Interchange | None = None
    current_grp: FunctionalGroup | None = None
    current_txn: Transaction | None = None
    txn_position = 0

    def make_segment(parts: list[str], pos: int, raw: str) -> Segment:
        seg_id = parts[0].strip().upper() if parts else ""
        return Segment(
            seg_id=seg_id,
            elements=parts[1:],
            position=pos,
            raw=raw,
            _component_sep=delim.component,
        )

    for raw_seg in raw_segments:
        parts = raw_seg.split(delim.element)
        seg_id = parts[0].strip().upper() if parts else ""

        if seg_id == "ISA":
            current_ic = Interchange(delimiters=delim)
            seg = make_segment(parts, 0, raw_seg)
            current_ic.isa = seg
            current_ic.sender_id = seg.elem(6).strip()
            current_ic.receiver_id = seg.elem(8).strip()
            current_ic.control_number = seg.elem(13).strip()
            current_ic.usage_indicator = seg.elem(15).strip()
            doc.interchanges.append(current_ic)
            current_grp = None
            current_txn = None

        elif seg_id == "GS":
            seg = make_segment(parts, 0, raw_seg)
            grp = FunctionalGroup(
                functional_id=seg.elem(1).strip(),
                control_number=seg.elem(6).strip(),
                version=seg.elem(8).strip(),
                gs=seg,
            )
            if current_ic is None:
                current_ic = _orphan_interchange(doc, delim, "GS")
            current_ic.groups.append(grp)
            current_grp = grp
            current_txn = None

        elif seg_id == "ST":
            txn_position = 1
            seg = make_segment(parts, txn_position, raw_seg)
            txn = Transaction(
                set_code=seg.elem(1).strip(),
                control_number=seg.elem(2).strip(),
                implementation_version=seg.elem(3).strip(),
                st=seg,
            )
            txn.segments.append(seg)
            if current_grp is None:
                current_grp = _orphan_group(doc, current_ic, delim, "ST")
                current_ic = doc.interchanges[-1]
            current_grp.transactions.append(txn)
            current_txn = txn

        elif seg_id == "SE":
            if current_txn is not None:
                txn_position += 1
                seg = make_segment(parts, txn_position, raw_seg)
                current_txn.segments.append(seg)
                current_txn.se = seg
                current_txn = None
            else:
                doc.parse_issues.append(
                    _orphan_issue("SE", "transaction (ST)")
                )

        elif seg_id == "GE":
            seg = make_segment(parts, 0, raw_seg)
            if current_grp is not None:
                current_grp.ge = seg
                current_grp = None
            else:
                doc.parse_issues.append(_orphan_issue("GE", "functional group (GS)"))

        elif seg_id == "IEA":
            seg = make_segment(parts, 0, raw_seg)
            if current_ic is not None:
                current_ic.iea = seg
                current_ic = None
            else:
                doc.parse_issues.append(_orphan_issue("IEA", "interchange (ISA)"))

        elif seg_id == "TA1":
            # Interchange acknowledgment travels inside the ISA/IEA envelope.
            seg = make_segment(parts, 0, raw_seg)
            # Not attached to a transaction; recorded for completeness only.

        else:
            if current_txn is not None:
                txn_position += 1
                seg = make_segment(parts, txn_position, raw_seg)
                current_txn.segments.append(seg)
            elif seg_id:
                doc.parse_issues.append(
                    ValidationIssue(
                        snip_type=SnipType.INTEGRITY,
                        severity=Severity.ERROR,
                        code="INT.SEG.ORPHAN",
                        message=(
                            f"Segment {seg_id} appears outside any ST..SE "
                            "transaction envelope."
                        ),
                        segment_id=seg_id,
                        spec_ref="X12 005010 Appendix B",
                    )
                )

    return doc


def _orphan_interchange(doc: X12Document, delim: Delimiters, seg: str) -> Interchange:
    ic = Interchange(delimiters=delim)
    doc.interchanges.append(ic)
    doc.parse_issues.append(_orphan_issue(seg, "interchange (ISA)"))
    return ic


def _orphan_group(
    doc: X12Document, ic: Interchange | None, delim: Delimiters, seg: str
) -> FunctionalGroup:
    if ic is None:
        ic = Interchange(delimiters=delim)
        doc.interchanges.append(ic)
    grp = FunctionalGroup(functional_id="", control_number="", version="")
    ic.groups.append(grp)
    doc.parse_issues.append(_orphan_issue(seg, "functional group (GS)"))
    return grp


def _orphan_issue(seg: str, missing_parent: str) -> ValidationIssue:
    return ValidationIssue(
        snip_type=SnipType.INTEGRITY,
        severity=Severity.ERROR,
        code="INT.ENV.ORPHAN",
        message=f"{seg} segment has no enclosing {missing_parent}.",
        segment_id=seg,
        spec_ref="X12 005010 Appendix B — Control Structures",
    )
