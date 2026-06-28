"""Structured, serializable model of an 835 remittance.

The model captures enough structure to faithfully *rebuild* an 835 from stored
data (reconstruction) and to derive a reversal, while preserving the ordering of
non-financial segments. It is deliberately lossless at the segment level: each
segment keeps its full element list so reconstruction reproduces the original
codes and amounts, and the two balancing relationships hold by construction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

# CAS amount elements are 1-based positions 3,6,9,12,15,18; the reason code sits
# one element before each amount (2,5,8,11,14,17). PLB amounts are at 4,6,8,...
CAS_GROUP_POS = 1
CAS_REASON_POSITIONS = (2, 5, 8, 11, 14, 17)
CAS_AMOUNT_POSITIONS = (3, 6, 9, 12, 15, 18)
CAS_QUANTITY_POSITIONS = (4, 7, 10, 13, 16, 19)
PLB_AMOUNT_POSITIONS = (4, 6, 8, 10, 12, 14)


def to_decimal(value: Optional[str]) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


@dataclass
class Delimiters:
    """X12 delimiter set for one interchange."""

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

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> "Delimiters":
        return cls(
            element=data.get("element", "*"),
            component=data.get("component", ":"),
            repetition=data.get("repetition", "^"),
            segment=data.get("segment", "~"),
        )


@dataclass
class Segment:
    """One X12 segment as ``seg_id`` plus its ordered data elements.

    ``elements`` holds *data* elements only — element 01 is ``elements[0]``.
    Use :meth:`elem` for 1-based, bounds-safe access matching X12 notation.
    """

    seg_id: str
    elements: list[str] = field(default_factory=list)

    def elem(self, n: int) -> str:
        if n < 1 or n > len(self.elements):
            return ""
        return self.elements[n - 1]

    def set_elem(self, n: int, value: str) -> None:
        while len(self.elements) < n:
            self.elements.append("")
        self.elements[n - 1] = value

    def render(self, delim: Delimiters) -> str:
        parts = [self.seg_id, *self.elements]
        # Drop trailing empty elements for a clean, conventional rendering.
        while len(parts) > 1 and parts[-1] == "":
            parts.pop()
        return delim.element.join(parts) + delim.segment

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.seg_id, "el": list(self.elements)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Segment":
        return cls(seg_id=data["id"], elements=list(data.get("el", [])))

    def copy(self) -> "Segment":
        return Segment(seg_id=self.seg_id, elements=list(self.elements))


@dataclass
class Adjustment:
    """A single CAS group-code/reason/amount triple (CARC at claim or line)."""

    group: str
    reason: str
    amount: Optional[Decimal]
    quantity: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "reason": self.reason,
            "amount": None if self.amount is None else str(self.amount),
            "quantity": self.quantity,
        }


def adjustments_in(seg: Segment) -> list[Adjustment]:
    """Extract the (group, reason, amount) triples carried by a CAS segment."""
    group = seg.elem(CAS_GROUP_POS)
    out: list[Adjustment] = []
    for reason_pos, amount_pos, qty_pos in zip(
        CAS_REASON_POSITIONS, CAS_AMOUNT_POSITIONS, CAS_QUANTITY_POSITIONS
    ):
        reason = seg.elem(reason_pos)
        amount = seg.elem(amount_pos)
        if not reason and not amount:
            continue
        out.append(
            Adjustment(
                group=group,
                reason=reason,
                amount=to_decimal(amount),
                quantity=seg.elem(qty_pos) or None,
            )
        )
    return out


@dataclass
class Service:
    """A service line (loop 2110): the SVC segment plus its child segments
    (line-level CAS, DTM, LQ remark codes, REF, AMT) in original order."""

    svc: Segment
    children: list[Segment] = field(default_factory=list)

    @property
    def charge(self) -> Optional[Decimal]:
        return to_decimal(self.svc.elem(2))

    @property
    def paid(self) -> Optional[Decimal]:
        return to_decimal(self.svc.elem(3))

    def cas_segments(self) -> list[Segment]:
        return [s for s in self.children if s.seg_id == "CAS"]

    def adjustments(self) -> list[Adjustment]:
        out: list[Adjustment] = []
        for seg in self.cas_segments():
            out.extend(adjustments_in(seg))
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "svc": self.svc.to_dict(),
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Service":
        return cls(
            svc=Segment.from_dict(data["svc"]),
            children=[Segment.from_dict(c) for c in data.get("children", [])],
        )


@dataclass
class Claim:
    """A claim payment loop (loop 2100): an optional lead (LX), the CLP segment,
    claim-level child segments (CAS, NM1, MIA/MOA, DTM, REF, AMT) and the service
    lines, all preserving original order."""

    clp: Segment
    lead: list[Segment] = field(default_factory=list)
    children: list[Segment] = field(default_factory=list)
    services: list[Service] = field(default_factory=list)

    @property
    def claim_id(self) -> str:
        return self.clp.elem(1)

    @property
    def status(self) -> str:
        return self.clp.elem(2)

    @property
    def charge(self) -> Optional[Decimal]:
        return to_decimal(self.clp.elem(3))

    @property
    def paid(self) -> Optional[Decimal]:
        return to_decimal(self.clp.elem(4))

    @property
    def patient_resp(self) -> Optional[Decimal]:
        return to_decimal(self.clp.elem(5))

    @property
    def payer_control_number(self) -> str:
        return self.clp.elem(7)

    def claim_cas_segments(self) -> list[Segment]:
        return [s for s in self.children if s.seg_id == "CAS"]

    def adjustments(self) -> list[Adjustment]:
        """All adjustments within the claim (claim-level + every service line)."""
        out: list[Adjustment] = []
        for seg in self.claim_cas_segments():
            out.extend(adjustments_in(seg))
        for svc in self.services:
            out.extend(svc.adjustments())
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "clp": self.clp.to_dict(),
            "lead": [s.to_dict() for s in self.lead],
            "children": [s.to_dict() for s in self.children],
            "services": [s.to_dict() for s in self.services],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Claim":
        return cls(
            clp=Segment.from_dict(data["clp"]),
            lead=[Segment.from_dict(s) for s in data.get("lead", [])],
            children=[Segment.from_dict(s) for s in data.get("children", [])],
            services=[Service.from_dict(s) for s in data.get("services", [])],
        )


@dataclass
class Era835:
    """A parsed 835 transaction set, structured for storage and rebuild."""

    delimiters: Delimiters
    preamble: list[Segment] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    plbs: list[Segment] = field(default_factory=list)
    trailer: list[Segment] = field(default_factory=list)

    # --- header accessors -------------------------------------------------

    def _first(self, seg_id: str) -> Optional[Segment]:
        for seg in self.preamble:
            if seg.seg_id == seg_id:
                return seg
        return None

    @property
    def st_control(self) -> str:
        st = self._first("ST")
        return st.elem(2) if st else ""

    @property
    def implementation_version(self) -> str:
        st = self._first("ST")
        return st.elem(3) if st else ""

    @property
    def trn(self) -> str:
        seg = self._first("TRN")
        return seg.elem(2) if seg else ""

    @property
    def bpr_amount(self) -> Optional[Decimal]:
        seg = self._first("BPR")
        return to_decimal(seg.elem(2)) if seg else None

    @property
    def bpr_method(self) -> str:
        seg = self._first("BPR")
        return seg.elem(4) if seg else ""

    @property
    def payer_id(self) -> str:
        for seg in self.preamble:
            if seg.seg_id == "N1" and seg.elem(1) == "PR":
                return seg.elem(4)
        return ""

    @property
    def payee_id(self) -> str:
        for seg in self.preamble:
            if seg.seg_id == "N1" and seg.elem(1) == "PE":
                return seg.elem(4)
        return ""

    @property
    def payer_name(self) -> str:
        for seg in self.preamble:
            if seg.seg_id == "N1" and seg.elem(1) == "PR":
                return seg.elem(2)
        return ""

    @property
    def payee_name(self) -> str:
        for seg in self.preamble:
            if seg.seg_id == "N1" and seg.elem(1) == "PE":
                return seg.elem(2)
        return ""

    @property
    def is_reversal(self) -> bool:
        return any(claim.status == "22" for claim in self.claims)

    # --- serialization ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "delimiters": self.delimiters.to_dict(),
            "preamble": [s.to_dict() for s in self.preamble],
            "claims": [c.to_dict() for c in self.claims],
            "plbs": [s.to_dict() for s in self.plbs],
            "trailer": [s.to_dict() for s in self.trailer],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Era835":
        return cls(
            delimiters=Delimiters.from_dict(data.get("delimiters", {})),
            preamble=[Segment.from_dict(s) for s in data.get("preamble", [])],
            claims=[Claim.from_dict(c) for c in data.get("claims", [])],
            plbs=[Segment.from_dict(s) for s in data.get("plbs", [])],
            trailer=[Segment.from_dict(s) for s in data.get("trailer", [])],
        )

    def copy(self) -> "Era835":
        return Era835.from_dict(self.to_dict())
