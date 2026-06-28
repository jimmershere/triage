"""Parse raw 835 text into the structured :class:`Era835` model.

This is a focused, restore-oriented parser (distinct from the validation parser
in ``worker_py/validation``): it captures the full segment structure grouped by
claim/service so a stored 835 can be faithfully rebuilt and reversed. It does
not perform validation — that responsibility stays with the SNIP engine.
"""
from __future__ import annotations

from .model import Claim, Delimiters, Era835, Segment, Service

ISA_LEN = 106


def _scan_terminator(text: str, element: str) -> str | None:
    """Recover the segment terminator from a non-standard ISA by scanning for the
    first envelope tag that follows it and returning the delimiter just before."""
    for tag in ("GS", "TA1", "IEA", "ST"):
        idx = text.find(tag, 3)
        while idx != -1:
            j = idx - 1
            while j >= 0 and text[j] in "\r\n":
                j -= 1
            if j >= 3:
                cand = text[j]
                if cand != element and not cand.isalnum() and not cand.isspace():
                    return cand
            idx = text.find(tag, idx + 1)
    return None


def detect_delimiters(text: str) -> Delimiters:
    """Detect the delimiter set from a leading ISA segment.

    A well-formed ISA is exactly 106 characters with the segment terminator at
    offset 105. Real-world ISAs are frequently mis-padded, so when offset 105
    does not yield a plausible terminator (or collides with the element
    separator) the terminator is recovered by scanning for the following GS/ST.
    """
    if not text.startswith("ISA"):
        return Delimiters()
    element = text[3]
    if len(text) >= ISA_LEN:
        seg_term = text[105]
        if (
            seg_term != element
            and not seg_term.isalnum()
            and not seg_term.isspace()
        ):
            return Delimiters(
                element=element,
                component=text[104],
                repetition=text[82],
                segment=seg_term,
            )
    recovered = _scan_terminator(text, element)
    if recovered is not None:
        return Delimiters(element=element, component=":", repetition="^", segment=recovered)
    return Delimiters(element=element)


def _split_segments(text: str, delim: Delimiters) -> list[str]:
    cleaned = text.replace("\r", "").replace("\n", "")
    return [s for s in cleaned.split(delim.segment) if s.strip()]


def _make_segment(raw: str, delim: Delimiters) -> Segment:
    parts = raw.split(delim.element)
    seg_id = parts[0].strip().upper() if parts else ""
    return Segment(seg_id=seg_id, elements=[p for p in parts[1:]])


def parse_835(text: str) -> Era835:
    """Parse a single 835 transaction set into an :class:`Era835`.

    Resilient to envelope variations: the ISA/GS/ST envelope (when present) is
    captured in the preamble so a faithful rebuild is possible. Only the first
    transaction set is modelled (835 remittance files carry one ST..SE per
    payment in the Triage pipeline).
    """
    delim = detect_delimiters(text)
    raw_segments = _split_segments(text, delim)

    era = Era835(delimiters=delim)
    current_claim: Claim | None = None
    current_service: Service | None = None
    seen_se = False
    pending_lead: list[Segment] = []

    def close_claim() -> None:
        nonlocal current_claim, current_service
        if current_claim is not None:
            era.claims.append(current_claim)
        current_claim = None
        current_service = None

    for raw in raw_segments:
        seg = _make_segment(raw, delim)
        sid = seg.seg_id

        if sid in ("SE", "GE", "IEA"):
            close_claim()
            seen_se = True
            era.trailer.append(seg)
            continue

        if seen_se:
            # Anything after SE/GE/IEA is envelope trailer; preserve in order.
            era.trailer.append(seg)
            continue

        if sid == "PLB":
            close_claim()
            era.plbs.append(seg)
            continue

        if sid == "LX":
            close_claim()
            pending_lead.append(seg)
            continue

        if sid == "CLP":
            close_claim()
            current_claim = Claim(clp=seg, lead=pending_lead)
            pending_lead = []
            continue

        if sid == "SVC":
            if current_claim is None:
                # Defensive: an SVC with no enclosing CLP — keep in preamble.
                era.preamble.append(seg)
                continue
            current_service = Service(svc=seg)
            current_claim.services.append(current_service)
            continue

        # Any other segment is attached to the deepest open scope.
        if current_claim is None:
            # Header / loop 1000 content (and any stray pending LX).
            if pending_lead:
                era.preamble.extend(pending_lead)
                pending_lead = []
            era.preamble.append(seg)
        elif current_service is not None:
            current_service.children.append(seg)
        else:
            current_claim.children.append(seg)

    close_claim()
    if pending_lead:
        era.preamble.extend(pending_lead)
    return era
