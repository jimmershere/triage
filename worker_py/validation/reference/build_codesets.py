"""Regenerate bundled internal X12 code-set tables from the WPC TR3 reference.

Run as ``python -m validation.reference.build_codesets`` from ``worker_py``.

Only *internal* X12 enumerations carried inline in the implementation guide are
regenerated here (gender, entity identifier, relationship, claim filing
indicator, ...). External code sources (Place of Service source 237, CARC/RARC,
ICD-10-CM/PCS, NUCC taxonomy) are NOT touched — those are owned by the
effective-dated registry and their published files.

The output keeps the loader's on-disk shape:
``{name, description, source, complete, codes:{code: desc}}`` and sets
``complete: true`` because a guide enumeration is the authoritative full set for
that position (so an unknown value is an error, not a warning).
"""
from __future__ import annotations

import json
from pathlib import Path

from .x12_reference import _CODESET_ELEMENTS, extract_internal_codesets, get_reference

_DATA_DIR = Path(__file__).resolve().parent.parent / "codesets" / "data"

_DESCRIPTIONS = {
    "gender": "Administrative gender code (DMG03 / X12 DE 1068).",
    "entity_identifier": "Entity identifier code (NM101 / X12 DE 98).",
    "entity_type": "Entity type qualifier (NM102 / X12 DE 1065).",
    "id_qualifier": "Identification code qualifier (NM108 / X12 DE 66).",
    "relationship": "Individual relationship code (SBR02 / X12 DE 1069).",
    "filing_indicator": "Claim filing indicator code (SBR09 / X12 DE 1032).",
    "facility_code_qualifier": "Facility code qualifier (CLM05-02 / X12 DE 1332).",
    "provider_code": "Provider code (CLM05-01 / X12 DE 1331).",
}


def build(write: bool = True) -> dict[str, dict[str, int]]:
    """Merge guide enumerations into the bundled code-set files (union, never
    shrink). Returns ``{name: {"before": n, "after": n, "added": n}}``.

    Existing curated codes are preserved; guide-enumerated codes are added.
    Descriptions prefer an existing non-empty value, then the guide's. This
    guarantees coverage only grows, so deepening never introduces a false
    reject against codes a prior curation already accepted.
    """
    ref = get_reference("005010X222")
    if ref is None:
        raise SystemExit(
            "Reference bundle not found under reference/x12/005010X222/source/"
        )
    extracted = extract_internal_codesets("005010X222")
    report: dict[str, dict[str, int]] = {}
    for name, guide_codes in extracted.items():
        if not guide_codes:
            continue
        path = _DATA_DIR / f"{name}.json"
        existing: dict[str, str] = {}
        existing_complete = True
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            existing = {
                str(k).strip().upper(): str(v)
                for k, v in raw.get("codes", {}).items()
            }
            existing_complete = bool(raw.get("complete", True))
        merged = dict(existing)
        added = 0
        for code, desc in guide_codes.items():
            if code not in merged:
                merged[code] = desc
                added += 1
            elif not merged[code] and desc:
                merged[code] = desc
        report[name] = {
            "before": len(existing),
            "after": len(merged),
            "added": added,
        }
        if write:
            payload = {
                "name": name,
                "description": _DESCRIPTIONS.get(name, ""),
                "source": (
                    "WPC/DISA 005010X222 (837P) implementation-guide enumeration "
                    "(union with prior curation); imported via validation.reference"
                ),
                # A guide enumeration is authoritative; keep complete True unless
                # the prior file deliberately marked the table a partial sample.
                "complete": existing_complete,
                "codes": {k: merged[k] for k in sorted(merged)},
            }
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    return report


if __name__ == "__main__":  # pragma: no cover
    result = build(write=True)
    for name, r in sorted(result.items()):
        print(f"  {name}: {r['before']} -> {r['after']} (+{r['added']})")
    print(f"merged guide enumerations into {len(result)} code-set files")
