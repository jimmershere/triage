"""Position-addressable X12 TR3 reference loaded from WPC table data.

The reference is built from the Washington Publishing Company (WPC) /DISA
implementation-guide CSV export (``x222-005010-CSV.csv`` for 005010X222, the
Health Care Claim: Professional 837P). That file is the rendered TR3 in flat
form: one row per segment / element / composite position carrying the data
``Type``, ``Min|Max`` length, ``Usage`` (Required / Situational / Not used),
``Repeat``, ``Loop ID``, the enumerated valid ``Value`` bindings, and the
free-text ``Situational Rule``.

The WPC/DISA license permits importing this data into a value-added "syntax
analyzer" — which is exactly this engine's use. We never redistribute the guide
text; we expose validation *results*.

This model is consumed by :mod:`worker_py.validation.rules.structure_837` to do
table-driven SNIP Type 1 (element attributes/length/type), Type 2 (required
usage) and Type 5 (enumerated value membership) checks.
"""
from __future__ import annotations

import csv
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

def _reference_root() -> Path:
    """Locate the (license-provisioned) reference bundle root.

    Honors ``TRIAGE_X12_REFERENCE_DIR`` (set when the bundle is mounted into a
    container), then falls back to candidate locations for the source tree and
    common deployment layouts. The licensed WPC data is mounted at runtime, not
    baked into the image, so this must be resolvable independent of package
    location.
    """
    import os
    env = os.getenv("TRIAGE_X12_REFERENCE_DIR", "").strip()
    candidates = []
    if env:
        candidates.append(Path(env))
    here = Path(__file__).resolve()
    candidates += [
        here.parents[3] / "reference" / "x12",  # source-tree layout
        Path("/reference/x12"),                  # conventional container mount
        Path("/app/reference/x12"),
        Path.cwd() / "reference" / "x12",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0] if candidates else Path("reference/x12")

# CLM05-01 -> ("CLM", 5, 1) ; DMG03 -> ("DMG", 3, None) ; "ST" -> segment header
_ELEMENT_RE = re.compile(r"^([A-Z][A-Z0-9]{1,2})(\d{2})(?:-(\d{2}))?$")


@dataclass(frozen=True)
class ElementSpec:
    """One element / composite position in the implementation guide."""

    seq: int
    ref: str                       # "CLM05-01" or "DMG03"
    segment: str                   # "CLM"
    element: int                   # 5
    component: int | None          # 1, or None for a simple element
    description: str = ""
    data_type: str = ""            # ID / AN / N / R / DT / TM / Nn ...
    min_len: int | None = None
    max_len: int | None = None
    usage: str = ""                # "R" required, "S" situational, "N" not used
    repeat: int | None = None
    loop_id: str | None = None
    values: dict[str, str] = field(default_factory=dict)  # enumerated bindings
    situational_rule: str = ""

    @property
    def required(self) -> bool:
        return self.usage.upper().startswith("R")

    @property
    def not_used(self) -> bool:
        u = self.usage.upper()
        return u.startswith("N") or u == "NOT USED"

    @property
    def enumerated(self) -> bool:
        return bool(self.values)


def _parse_ref(token: str) -> tuple[str, int, int | None] | None:
    m = _ELEMENT_RE.match(token.strip())
    if not m:
        return None
    seg, ele, comp = m.group(1), int(m.group(2)), m.group(3)
    return seg, ele, (int(comp) if comp else None)


def _parse_minmax(token: str) -> tuple[int | None, int | None]:
    token = (token or "").strip()
    if not token or "|" not in token:
        return None, None
    lo, _, hi = token.partition("|")
    try:
        return int(lo), int(hi)
    except ValueError:
        return None, None


def _parse_values(token: str) -> dict[str, str]:
    """Parse the multi-line ``Value`` cell ("F=Female\\nM=Male") into a dict."""
    out: dict[str, str] = {}
    for line in (token or "").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        code, _, desc = line.partition("=")
        code = code.strip().upper()
        if code:
            out[code] = desc.strip()
    return out


def _parse_repeat(token: str) -> int | None:
    token = (token or "").strip().replace(">", "")
    if not token or not token.isdigit():
        return None
    return int(token)


class TransactionReference:
    """All element specs for one transaction, addressable by position."""

    def __init__(self, transaction: str, name: str, source: str) -> None:
        self.transaction = transaction
        self.name = name
        self.source = source
        self.elements: list[ElementSpec] = []
        # (segment, element, component) -> spec ; component None stored as 0
        self._by_pos: dict[tuple[str, int, int], ElementSpec] = {}
        self._segments: set[str] = set()

    def add(self, spec: ElementSpec) -> None:
        self.elements.append(spec)
        key = (spec.segment, spec.element, spec.component or 0)
        # The guide repeats some elements across loops; keep the first (most
        # general) spec but merge enumerated values so membership stays a union.
        existing = self._by_pos.get(key)
        if existing is None:
            self._by_pos[key] = spec
        elif spec.values and not existing.values:
            self._by_pos[key] = spec
        elif spec.values and existing.values and spec.values != existing.values:
            merged = dict(existing.values)
            merged.update(spec.values)
            self._by_pos[key] = ElementSpec(
                **{**existing.__dict__, "values": merged}
            )
        self._segments.add(spec.segment)

    def element(
        self, segment: str, element: int, component: int | None = None
    ) -> ElementSpec | None:
        return self._by_pos.get((segment.upper(), element, component or 0))

    def has_segment(self, segment: str) -> bool:
        return segment.upper() in self._segments

    def composite_specs(self, segment: str, element: int) -> list[ElementSpec]:
        """Component specs for a composite element, ordered by component no."""
        seg = segment.upper()
        out = [
            spec
            for (s, e, c), spec in self._by_pos.items()
            if s == seg and e == element and c >= 1
        ]
        return sorted(out, key=lambda sp: sp.component or 0)

    def is_composite(self, segment: str, element: int) -> bool:
        return self.element(segment, element, 1) is not None

    def __len__(self) -> int:
        return len(self.elements)


_TRANSACTION_NAMES = {
    "005010X222": "Health Care Claim: Professional (837P)",
    "005010X223": "Health Care Claim: Institutional (837I)",
    "005010X224": "Health Care Claim: Dental (837D)",
}


def load_reference_csv(csv_path: str | Path, transaction: str) -> TransactionReference:
    """Build a :class:`TransactionReference` from a WPC implementation-guide CSV."""
    path = Path(csv_path)
    ref = TransactionReference(
        transaction=transaction,
        name=_TRANSACTION_NAMES.get(transaction, transaction),
        source=f"WPC/DISA {transaction} table data (CSV export)",
    )
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            token = (row.get("Element Identifier") or "").strip()
            parsed = _parse_ref(token)
            if parsed is None:
                continue  # segment-header / loop / structural row, not an element
            segment, element, component = parsed
            lo, hi = _parse_minmax(row.get("Min|Max", ""))
            try:
                seq = int((row.get("Sequence") or "0").strip() or 0)
            except ValueError:
                seq = 0
            ref.add(
                ElementSpec(
                    seq=seq,
                    ref=token,
                    segment=segment,
                    element=element,
                    component=component,
                    description=(row.get("Description") or "").strip(),
                    data_type=(row.get("Type") or "").strip(),
                    min_len=lo,
                    max_len=hi,
                    usage=(row.get("Usage") or "").strip(),
                    repeat=_parse_repeat(row.get("Repeat", "")),
                    loop_id=(row.get("Loop ID") or "").strip() or None,
                    values=_parse_values(row.get("Value", "")),
                    situational_rule=(row.get("Situational Rule") or "").strip(),
                )
            )
    return ref


def _default_csv_path(transaction: str) -> Path | None:
    base = _reference_root() / transaction / "source"
    if not base.exists():
        return None
    for candidate in sorted(base.glob("*CSV*.csv")):
        return candidate
    return None


_CACHE: dict[str, TransactionReference] = {}
_LOCK = threading.Lock()


def get_reference(transaction: str = "005010X222") -> TransactionReference | None:
    """Load (and cache) the bundled reference for a transaction, or ``None``.

    Returns ``None`` when the reference bundle is not present so callers degrade
    to the hand-coded guide rules rather than crashing.
    """
    with _LOCK:
        if transaction in _CACHE:
            return _CACHE[transaction]
        csv_path = _default_csv_path(transaction)
        if csv_path is None:
            return None
        ref = load_reference_csv(csv_path, transaction)
        _CACHE[transaction] = ref
        return ref


def clear_cache() -> None:
    """Drop the in-process reference cache (used on hot reload)."""
    with _LOCK:
        _CACHE.clear()


# --- internal X12 enumerated code-list extraction -------------------------

# Map a guide element position to the bundled code-set file it should populate.
# Only *internal* X12 enumerations carried in the guide are listed here; external
# code sources (POS source 237, CARC/RARC, ICD-10) come from the dated registry.
_CODESET_ELEMENTS: dict[str, tuple[str, int, int | None]] = {
    "gender": ("DMG", 3, None),
    "claim_frequency": ("CLM", 5, 3),
    "entity_identifier": ("NM1", 1, None),
    "entity_type": ("NM1", 2, None),
    "id_qualifier": ("NM1", 8, None),
    "relationship": ("SBR", 2, None),
    "filing_indicator": ("SBR", 9, None),
    "facility_code_qualifier": ("CLM", 5, 2),
    "provider_code": ("CLM", 5, 1),
}


def extract_internal_codesets(
    transaction: str = "005010X222",
) -> dict[str, dict[str, str]]:
    """Return ``{codeset_name: {code: description}}`` from the guide enumerations."""
    ref = get_reference(transaction)
    out: dict[str, dict[str, str]] = {}
    if ref is None:
        return out
    for name, (seg, ele, comp) in _CODESET_ELEMENTS.items():
        spec = ref.element(seg, ele, comp)
        if spec and spec.values:
            out[name] = dict(spec.values)
    return out


def iter_required_elements(
    transaction: str = "005010X222",
) -> Iterable[ElementSpec]:
    ref = get_reference(transaction)
    if ref is None:
        return []
    return [e for e in ref.elements if e.required]
