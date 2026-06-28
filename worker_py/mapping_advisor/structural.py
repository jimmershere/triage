"""Structural alignment: X12 element extraction + candidate generation.

Candidate generation is driven by *structural* features — loop-path context,
value/shape alignment, and (optionally) historical confirmation — rather than
by any opaque model. The transparent :class:`MappingFeatures` produced here are
fed to the logistic scorer for ranking.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Iterable, Mapping, Optional

from .model import FlatField, MappingCandidate, MappingFeatures, X12ElementRef

try:  # package context (api imports worker_py.mapping_advisor)
    from worker_py.validation.parser import parse
except ModuleNotFoundError:  # script context (worker_py on sys.path)
    from validation.parser import parse

# Envelope segments carry control structure, not mappable business data.
_ENVELOPE = {"ISA", "GS", "ST", "SE", "GE", "IEA"}

# Cap how many elements we extract to keep candidate generation bounded.
_MAX_ELEMENTS = 600


def extract_elements(x12_text: str) -> list[X12ElementRef]:
    """Extract data elements with a loop-path breadcrumb for each transaction.

    The breadcrumb is a coarse, deterministic structural context built from the
    hierarchical level (HL03), claim (CLM), and service-line (LX) anchors — the
    features the aligner needs without hard-coding a full TR3 loop model.
    """
    doc = parse(x12_text)
    elements: list[X12ElementRef] = []
    for txn in doc.transactions:
        hl: Optional[str] = None
        in_clm = False
        lx: Optional[str] = None
        for seg in txn.segments:
            seg_id = seg.seg_id
            if seg_id == "HL":
                hl = f"HL*{seg.elem(3)}" if seg.has_elem(3) else "HL"
                in_clm = False
                lx = None
            elif seg_id == "CLM":
                in_clm = True
                lx = None
            elif seg_id == "LX":
                lx = f"LX*{seg.elem(1)}" if seg.has_elem(1) else "LX"

            if seg_id in _ENVELOPE:
                continue

            parts = [p for p in (hl, "CLM" if in_clm else None, lx) if p]
            loop_path = "/".join(parts) if parts else "ROOT"

            for pos in range(1, seg.max_element + 1):
                value = seg.elem(pos).strip()
                if not value:
                    continue
                elements.append(
                    X12ElementRef(
                        loop_path=loop_path,
                        segment_id=seg_id,
                        element_position=pos,
                        value=value,
                        transaction_set=txn.set_code,
                    )
                )
                if len(elements) >= _MAX_ELEMENTS:
                    return elements
    return elements


def _normalize(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch.isalnum())


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _type_of(value: str) -> str:
    if not value:
        return "empty"
    stripped = value.strip()
    if stripped.replace(".", "", 1).replace("-", "", 1).isdigit():
        return "numeric"
    digit_run = _digits(stripped)
    if len(digit_run) in (6, 8) and digit_run == stripped:
        return "date"
    if stripped.isalpha():
        return "alpha"
    return "alnum"


def _value_match(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    na, nb = _normalize(a), _normalize(b)
    if na and na == nb:
        return 1.0
    da, db = _digits(a), _digits(b)
    if da and da == db:
        return 0.9
    if len(na) >= 3 and (na in nb or nb in na):
        return 0.6
    return 0.0


def source_key(elem: X12ElementRef) -> str:
    """Stable historical-lookup key independent of sample value."""
    return f"{elem.transaction_set}|{elem.loop_path}|{elem.segment_id}|{elem.element_position}"


def _compute_features(
    elem: X12ElementRef,
    fld: FlatField,
    historical: Optional[Mapping[str, Iterable[str]]],
) -> MappingFeatures:
    exact = 1.0 if (elem.value and elem.value == fld.value) else 0.0
    normalized = _value_match(elem.value, fld.value)

    name_basis = max(
        SequenceMatcher(None, fld.name.lower(), elem.segment_id.lower()).ratio(),
        SequenceMatcher(
            None, fld.name.lower(), f"{elem.segment_id}{elem.element_position:02d}".lower()
        ).ratio(),
    )

    type_match = 1.0 if _type_of(elem.value) == _type_of(fld.value) else 0.0

    if fld.kind == "fixed" and fld.length > 0:
        diff = abs(len(elem.value) - fld.length)
        length_fit = max(0.0, 1.0 - diff / float(fld.length))
    else:
        length_fit = 0.5

    historical_support = 0.0
    if historical:
        confirmed = historical.get(source_key(elem))
        if confirmed and fld.name in set(confirmed):
            historical_support = 1.0

    return MappingFeatures(
        value_exact_match=exact,
        value_normalized_match=normalized,
        name_similarity=round(name_basis, 4),
        type_match=type_match,
        length_fit=round(length_fit, 4),
        historical_support=historical_support,
    )


def generate_candidates(
    elements: list[X12ElementRef],
    fields: list[FlatField],
    *,
    historical: Optional[Mapping[str, Iterable[str]]] = None,
) -> list[MappingCandidate]:
    """Generate all element↔field candidates with structural features.

    Pairs whose features are entirely empty (no value, name, or historical
    signal) are dropped to keep the candidate set meaningful.
    """
    candidates: list[MappingCandidate] = []
    for elem in elements:
        for fld in fields:
            features = _compute_features(elem, fld, historical)
            signal = (
                features.value_exact_match
                + features.value_normalized_match
                + features.name_similarity
                + features.historical_support
            )
            if signal <= 0.0:
                continue
            rationale = _rationale(features)
            candidates.append(
                MappingCandidate(
                    source=elem,
                    target=fld,
                    features=features,
                    rationale=rationale,
                )
            )
    return candidates


def _rationale(features: MappingFeatures) -> str:
    reasons: list[str] = []
    if features.value_exact_match >= 1.0:
        reasons.append("exact value match")
    elif features.value_normalized_match >= 0.9:
        reasons.append("normalized value match")
    elif features.value_normalized_match > 0:
        reasons.append("partial value match")
    if features.historical_support >= 1.0:
        reasons.append("confirmed historically")
    if features.name_similarity >= 0.5:
        reasons.append("name similarity")
    if features.type_match >= 1.0:
        reasons.append("compatible type")
    return "; ".join(reasons) or "weak structural signal"
