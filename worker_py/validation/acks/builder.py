"""X12 envelope builder used by the acknowledgment generators.

Assembles ISA/GS/ST..SE/GE/IEA envelopes, auto-computing the SE01 segment
count, GE01 transaction count and IEA01 group count, and renders to text with
a configurable delimiter set.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..parser import Delimiters


def now_stamp(dt: datetime | None = None) -> tuple[str, str, str, str]:
    """Return (isa_date YYMMDD, gs_date CCYYMMDD, time HHMM, control)."""
    dt = dt or datetime.now(timezone.utc)
    return (
        dt.strftime("%y%m%d"),
        dt.strftime("%Y%m%d"),
        dt.strftime("%H%M"),
        dt.strftime("%H%M%S"),
    )


class X12Builder:
    """Incrementally assemble a single-interchange X12 document."""

    def __init__(self, delimiters: Delimiters | None = None) -> None:
        self.delim = delimiters or Delimiters()
        self._segments: list[list[str]] = []
        self._isa_idx: int | None = None
        self._gs_idx: int | None = None
        self._st_idx: int | None = None
        self._isa_control = ""
        self._gs_control = ""
        self._st_control = ""

    @staticmethod
    def _pad(value: str, width: int) -> str:
        return str(value)[:width].ljust(width)

    # -- envelope ----------------------------------------------------------

    def interchange(
        self,
        *,
        sender: str,
        receiver: str,
        control: str,
        date: str,
        time: str,
        usage: str = "P",
    ) -> "X12Builder":
        self._isa_control = str(control).rjust(9, "0")[:9]
        self._isa_idx = len(self._segments)
        self._segments.append(
            [
                "ISA",
                "00",
                self._pad("", 10),
                "00",
                self._pad("", 10),
                "ZZ",
                self._pad(sender, 15),
                "ZZ",
                self._pad(receiver, 15),
                date,
                time,
                self.delim.repetition,
                "00501",
                self._isa_control,
                "0",
                usage,
                self.delim.component,
            ]
        )
        return self

    def group(
        self,
        *,
        functional_id: str,
        sender: str,
        receiver: str,
        date: str,
        time: str,
        control: str,
        version: str,
    ) -> "X12Builder":
        self._gs_control = str(control)
        self._gs_idx = len(self._segments)
        self._segments.append(
            ["GS", functional_id, sender, receiver, date, time, str(control), "X", version]
        )
        return self

    def transaction(self, *, set_code: str, control: str, version: str) -> "X12Builder":
        self._st_control = str(control)
        self._st_idx = len(self._segments)
        self._segments.append(["ST", set_code, str(control), version])
        return self

    def add(self, seg_id: str, *elements: object) -> "X12Builder":
        """Append a body segment. ``None`` elements render as empty."""
        self._segments.append(
            [seg_id, *("" if e is None else str(e) for e in elements)]
        )
        return self

    def end_transaction(self) -> "X12Builder":
        if self._st_idx is None:
            raise RuntimeError("end_transaction() called before transaction()")
        count = len(self._segments) - self._st_idx + 1  # ST..SE inclusive
        self._segments.append(["SE", str(count), self._st_control])
        return self

    def end_group(self) -> "X12Builder":
        if self._gs_idx is None:
            raise RuntimeError("end_group() called before group()")
        txn_count = sum(
            1 for s in self._segments[self._gs_idx:] if s and s[0] == "ST"
        )
        self._segments.append(["GE", str(txn_count), self._gs_control])
        return self

    def end_interchange(self) -> "X12Builder":
        if self._isa_idx is None:
            raise RuntimeError("end_interchange() called before interchange()")
        grp_count = sum(
            1 for s in self._segments[self._isa_idx:] if s and s[0] == "GS"
        )
        self._segments.append(["IEA", str(grp_count), self._isa_control])
        return self

    # -- render ------------------------------------------------------------

    def render(self) -> str:
        out: list[str] = []
        for seg in self._segments:
            seg_id = seg[0]
            elements = list(seg[1:])
            # Trim trailing empty elements (X12 convention) — but never for the
            # fixed-length ISA header.
            if seg_id != "ISA":
                while elements and elements[-1] == "":
                    elements.pop()
            line = seg_id
            if elements:
                line += self.delim.element + self.delim.element.join(elements)
            out.append(line + self.delim.segment)
        return "".join(out)
