"""Code-set loading and format validators.

Membership code sets are loaded lazily from JSON files under ``data/``. Each file
has the shape::

    {
      "name": "place_of_service",
      "description": "...",
      "source": "...",
      "complete": true,
      "codes": { "01": "Pharmacy", ... }
    }

When ``complete`` is ``false`` the bundled table is a curated subset; an unknown
code should be reported as a *warning* rather than an *error* so a partial table
never produces false rejections.

Licensing notes:
- POS, claim-frequency, CARC, RARC, X12 enumerated codes — public.
- ICD-10-CM — published by CMS/CDC (public domain); the full ~74k table is not
  bundled. :func:`is_valid_icd10_cm` does format validation.
- CPT — copyright AMA. Not bundled. :func:`is_valid_hcpcs_cpt` does format
  validation only. HCPCS Level II is public but also validated by format here.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent / "data"
_CACHE: dict[str, "CodeSet"] = {}
_LOCK = threading.Lock()


@dataclass
class CodeSet:
    """An enumerated external code set."""

    name: str
    description: str = ""
    source: str = ""
    complete: bool = True
    codes: dict[str, str] = field(default_factory=dict)

    def is_valid(self, code: str | None) -> bool:
        if code is None:
            return False
        return code.strip().upper() in self.codes

    def describe(self, code: str | None) -> str | None:
        if code is None:
            return None
        return self.codes.get(code.strip().upper())

    def __len__(self) -> int:
        return len(self.codes)


_EMPTY = CodeSet(name="<missing>", complete=False)


def get_codeset(name: str) -> CodeSet:
    """Load (and cache) a bundled code set by name.

    A missing data file yields an empty, ``complete=False`` set so callers
    degrade to format-only checks instead of crashing.
    """
    with _LOCK:
        if name in _CACHE:
            return _CACHE[name]
        path = _DATA_DIR / f"{name}.json"
        if not path.exists():
            _CACHE[name] = CodeSet(name=name, complete=False)
            return _CACHE[name]
        raw = json.loads(path.read_text(encoding="utf-8"))
        codes = {str(k).strip().upper(): str(v) for k, v in raw.get("codes", {}).items()}
        cs = CodeSet(
            name=raw.get("name", name),
            description=raw.get("description", ""),
            source=raw.get("source", ""),
            complete=bool(raw.get("complete", True)),
            codes=codes,
        )
        _CACHE[name] = cs
        return cs


def available_codesets() -> list[str]:
    """List the names of all bundled code-set data files."""
    if not _DATA_DIR.exists():
        return []
    return sorted(p.stem for p in _DATA_DIR.glob("*.json"))


# ---------------------------------------------------------------------------
# Format validators for sets too large or proprietary to bundle in full.
# ---------------------------------------------------------------------------

_ICD10_CM_RE = re.compile(r"^[A-TV-Z][0-9][0-9A-Z](\.?[0-9A-Z]{1,4})?$")
# HCPCS Level II and CDT dental codes: one letter (A-V, including D for dental)
# followed by four digits.
_HCPCS_LEVEL2_RE = re.compile(r"^[A-V][0-9]{4}$")
_CPT_CAT1_RE = re.compile(r"^[0-9]{5}$")
_CPT_CAT2_RE = re.compile(r"^[0-9]{4}F$")
_CPT_CAT3_RE = re.compile(r"^[0-9]{4}T$")
_TAXONOMY_RE = re.compile(r"^[0-9A-Z]{9}[0-9A-Z]$")
_REVENUE_RE = re.compile(r"^[0-9]{3,4}$")
_NPI_RE = re.compile(r"^[0-9]{10}$")


def _luhn_is_valid(number: str) -> bool:
    """Return True when ``number`` satisfies the Luhn checksum."""
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def is_valid_npi(npi: str | None) -> bool:
    """Validate a National Provider Identifier.

    An NPI is 10 digits whose final digit is a Luhn check digit computed over
    the 9-digit identifier prefixed with the NPI issuer code ``80840``.
    """
    if not npi or not _NPI_RE.match(npi):
        return False
    return _luhn_is_valid("80840" + npi)


def is_valid_icd10_cm(code: str | None) -> bool:
    """Format-validate an ICD-10-CM diagnosis code (decimal point optional)."""
    if not code:
        return False
    return bool(_ICD10_CM_RE.match(code.strip().upper()))


def is_valid_hcpcs_cpt(code: str | None) -> bool:
    """Format-validate a procedure code (CPT category I/II/III or HCPCS II)."""
    if not code:
        return False
    c = code.strip().upper()
    return bool(
        _CPT_CAT1_RE.match(c)
        or _CPT_CAT2_RE.match(c)
        or _CPT_CAT3_RE.match(c)
        or _HCPCS_LEVEL2_RE.match(c)
    )


def is_valid_taxonomy(code: str | None) -> bool:
    """Format-validate a 10-character NUCC provider taxonomy code."""
    if not code:
        return False
    return bool(_TAXONOMY_RE.match(code.strip().upper()))


def is_valid_revenue_code(code: str | None) -> bool:
    """Format-validate an institutional (NUBC) revenue code."""
    if not code:
        return False
    return bool(_REVENUE_RE.match(code.strip()))
