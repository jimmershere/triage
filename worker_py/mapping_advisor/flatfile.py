"""Flat-file parsing for the mapping advisor.

Supports two common flat-file shapes:

* **Fixed-width** — a layout of ``(name, start, length)`` fields.
* **Delimited** — a separator (e.g. ``,`` or ``|``) with optional header names.

The goal is to produce a list of :class:`FlatField` records (name + offset +
sample value) that the structural aligner can match against X12 elements. When
no layout is supplied for a fixed-width file, the parser cannot guess column
boundaries, so callers should provide a layout for fixed-width data and a
delimiter (or header row) for delimited data.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .model import FlatField


def _first_record(text: str) -> str:
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.strip():
            return line.rstrip("\n")
    return ""


def parse_fixed_width(
    record: str, layout: Sequence[tuple[str, int, int]]
) -> list[FlatField]:
    """Parse one fixed-width record using ``layout`` = ``[(name, start, length)]``.

    ``start`` is a 0-based character offset. Values are right/left-trimmed of
    surrounding whitespace for alignment scoring.
    """
    fields: list[FlatField] = []
    for name, start, length in layout:
        raw = record[start : start + length]
        fields.append(
            FlatField(
                name=name,
                start=start,
                length=length,
                value=raw.strip(),
                kind="fixed",
            )
        )
    return fields


def parse_delimited(
    record: str,
    delimiter: str = ",",
    names: Optional[Sequence[str]] = None,
) -> list[FlatField]:
    """Parse one delimited record into fields.

    When ``names`` is supplied it labels each column; otherwise columns are
    named ``col_000``, ``col_001``, ... by their index.
    """
    if not delimiter:
        delimiter = ","
    parts = record.split(delimiter)
    fields: list[FlatField] = []
    for index, raw in enumerate(parts):
        if names is not None and index < len(names):
            name = str(names[index]).strip() or f"col_{index:03d}"
        else:
            name = f"col_{index:03d}"
        fields.append(
            FlatField(
                name=name,
                start=index,
                length=0,
                value=raw.strip(),
                kind="delimited",
            )
        )
    return fields


def extract_fields(
    flat_file_text: str,
    *,
    layout: Optional[Sequence[tuple[str, int, int]]] = None,
    delimiter: Optional[str] = None,
    header: bool = False,
) -> list[FlatField]:
    """Extract fields from the first data record of ``flat_file_text``.

    Resolution order:

    1. If ``layout`` is given → fixed-width parse.
    2. Else if ``delimiter`` is given → delimited parse (optionally consuming a
       header row for field names).
    3. Else → best-effort: detect a common delimiter, else treat the whole
       record as a single field.
    """
    lines = [
        ln.rstrip("\n")
        for ln in flat_file_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if ln.strip()
    ]
    if not lines:
        return []

    if layout is not None:
        return parse_fixed_width(lines[0], layout)

    if delimiter is not None:
        names: Optional[list[str]] = None
        data_index = 0
        if header and len(lines) >= 1:
            names = [p.strip() for p in lines[0].split(delimiter)]
            data_index = 1 if len(lines) > 1 else 0
        record = lines[data_index] if data_index < len(lines) else lines[0]
        return parse_delimited(record, delimiter, names)

    # Best-effort delimiter sniff on the first record.
    record = lines[0]
    for candidate in ("|", ",", "\t", ";"):
        if candidate in record:
            return parse_delimited(record, candidate)
    return [FlatField(name="col_000", start=0, length=len(record), value=record.strip(), kind="fixed")]
