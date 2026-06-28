"""Render and transform :class:`Era835` models back into X12 835 text.

* :func:`build_835` renders a model to conformant 835 text, recomputing the
  SE01 segment count so the rebuilt transaction is structurally valid.
* :func:`reconstruct_835` rebuilds an 835 from stored structured data when the
  original artifact is lost (it is otherwise identical to :func:`build_835`; the
  "reconstruction" marker lives in Claimtrace lineage, not in the remittance
  data, so amounts and codes are never altered).
* :func:`build_reversal` derives a downstream reversal (CLP02=22, every amount
  negated, original CARC/RARC reason codes echoed unchanged), preserving the
  balancing relationships by construction.
"""
from __future__ import annotations

from decimal import Decimal

from .model import (
    CAS_AMOUNT_POSITIONS,
    PLB_AMOUNT_POSITIONS,
    Claim,
    Era835,
    Segment,
)


def _ordered_body(era: Era835) -> list[Segment]:
    segs: list[Segment] = list(era.preamble)
    for claim in era.claims:
        segs.extend(claim.lead)
        segs.append(claim.clp)
        segs.extend(claim.children)
        for service in claim.services:
            segs.append(service.svc)
            segs.extend(service.children)
    segs.extend(era.plbs)
    return segs


def _recompute_se(body: list[Segment], trailer: list[Segment]) -> None:
    combined = body + trailer
    st_idx = next((i for i, s in enumerate(combined) if s.seg_id == "ST"), None)
    se_idx = next((i for i, s in enumerate(combined) if s.seg_id == "SE"), None)
    if st_idx is None or se_idx is None or se_idx < st_idx:
        return
    count = se_idx - st_idx + 1
    combined[se_idx].set_elem(1, str(count))


def build_835(era: Era835, *, recompute_segment_count: bool = True) -> str:
    """Render ``era`` to conformant 835 text."""
    body = _ordered_body(era)
    trailer = list(era.trailer)
    if recompute_segment_count:
        _recompute_se(body, trailer)
    delim = era.delimiters
    return "".join(seg.render(delim) for seg in (*body, *trailer))


def reconstruct_835(era: Era835) -> str:
    """Rebuild an 835 from stored structured data (lost-original recovery).

    Identical in output to :func:`build_835` — reconstruction never edits
    amounts or codes; it only re-renders the immutably stored structure.
    """
    return build_835(era.copy())


def _negate(value: str) -> str:
    if not value:
        return value
    try:
        amount = Decimal(value)
    except Exception:
        return value
    if amount == 0:
        return value
    return str(-amount)


def _negate_cas_amounts(seg: Segment) -> None:
    for pos in CAS_AMOUNT_POSITIONS:
        current = seg.elem(pos)
        if current:
            seg.set_elem(pos, _negate(current))


def build_reversal(era: Era835) -> Era835:
    """Return a new :class:`Era835` modelling a full reversal of ``era``.

    Per ASC X12N 005010X221A1 reversal semantics: CLP02 becomes 22 (reversal),
    every monetary amount is negated, and the original CARC/RARC reason codes are
    echoed unchanged at the level they originally appeared. Because each amount
    is negated symmetrically, all three balancing relationships continue to hold.
    """
    reversed_era = era.copy()

    # BPR02 transaction payment amount.
    bpr = reversed_era._first("BPR")
    if bpr is not None and bpr.elem(2):
        bpr.set_elem(2, _negate(bpr.elem(2)))

    for claim in reversed_era.claims:
        _reverse_claim(claim)

    for plb in reversed_era.plbs:
        for pos in PLB_AMOUNT_POSITIONS:
            current = plb.elem(pos)
            if current:
                plb.set_elem(pos, _negate(current))

    return reversed_era


def _reverse_claim(claim: Claim) -> None:
    claim.clp.set_elem(2, "22")  # status -> reversal of previous payment
    for pos in (3, 4, 5):  # CLP03 charge, CLP04 paid, CLP05 patient responsibility
        current = claim.clp.elem(pos)
        if current:
            claim.clp.set_elem(pos, _negate(current))

    for seg in claim.children:
        if seg.seg_id == "CAS":
            _negate_cas_amounts(seg)
        elif seg.seg_id == "AMT" and seg.elem(2):
            seg.set_elem(2, _negate(seg.elem(2)))

    for service in claim.services:
        for pos in (2, 3):  # SVC02 charge, SVC03 paid
            current = service.svc.elem(pos)
            if current:
                service.svc.set_elem(pos, _negate(current))
        for seg in service.children:
            if seg.seg_id == "CAS":
                _negate_cas_amounts(seg)
            elif seg.seg_id == "AMT" and seg.elem(2):
                seg.set_elem(2, _negate(seg.elem(2)))
