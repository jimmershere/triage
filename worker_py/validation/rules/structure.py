"""Table-driven structural validation against the WPC TR3 reference.

This is the Workstream 2 keystone: instead of a handful of hand-coded element
checks, every element of every segment in a transaction is validated against the
empirically-extracted implementation-guide tables
(:mod:`worker_py.validation.reference`).

What is checked here is deliberately the loop-*invariant* subset, so the pass is
purely additive and cannot produce a false rejection that depends on loop
context (the loop-aware guide walker in :mod:`guide_837` still owns required /
situational presence):

* **SNIP Type 1 — data type** (``ID``/``AN``/``N``/``Nn``/``R``/``DT``/``TM``):
  a value that cannot be the declared type is an error. Type and length are
  constant for a given (segment, element, component) wherever it appears.
* **SNIP Type 1 — min/max length**: a present value outside the guide's length
  bounds is an error.
* **SNIP Type 5 — enumerated value binding**: when the guide enumerates the
  permitted values for an ``ID`` position, a value outside that set is reported.
  Because the bundled reference unions a code's appearances across loops, this
  is emitted as a *warning* by default (a value valid in one loop but not
  another would otherwise be a false reject); per-partner SNIP policy can
  escalate Type 5 to enforce.

If the reference bundle for a transaction is absent, this pass is a no-op and the
engine falls back to the hand-coded guide rules.
"""
from __future__ import annotations

import re

from ..model import Severity, SnipType, ValidationIssue, ValidationReport
from ..parser import Segment, Transaction
from ..reference import ElementSpec, get_reference

# Implementation-version prefix -> bundled reference transaction id.
_VERSION_PREFIXES = {
    "005010X222": "005010X222",  # 837P
    "005010X223": "005010X223",  # 837I (reference optional)
    "005010X224": "005010X224",  # 837D (reference optional)
}

# Envelope/control segments validated by common.validate_envelopes already.
_SKIP_SEGMENTS = {"ISA", "GS", "GE", "IEA", "ST", "SE"}

_DIGITS = re.compile(r"^\d+$")
_DECIMAL = re.compile(r"^-?\d+(\.\d+)?$")


def _reference_for(txn: Transaction):
    version = (txn.implementation_version or "").strip().upper()
    for prefix, txid in _VERSION_PREFIXES.items():
        if version.startswith(prefix):
            return get_reference(txid)
    return None


def _significant_len(value: str, data_type: str) -> int:
    """X12 length: digits only for numeric/decimal (sign and '.' don't count)."""
    if data_type.upper().startswith("N") or data_type.upper() == "R":
        return len(value.replace("-", "").replace(".", ""))
    return len(value)


def _type_ok(value: str, data_type: str) -> bool:
    dt = (data_type or "").upper()
    if not dt or dt in ("AN", "ID"):
        return True  # alphanumeric / identifier: any printable; length handles it
    if dt == "R":
        return bool(_DECIMAL.match(value))
    if dt.startswith("N"):
        # N or N0..N9 — integer with optional implied decimal places.
        return bool(re.match(r"^-?\d+$", value))
    if dt == "DT":
        return bool(_DIGITS.match(value)) and len(value) in (6, 8)
    if dt == "TM":
        return bool(_DIGITS.match(value)) and 4 <= len(value) <= 8
    return True  # B (binary) or unknown: don't guess


def _check_value(
    spec: ElementSpec,
    value: str,
    seg: Segment,
    txn: Transaction,
    report: ValidationReport,
    *,
    component: int | None,
) -> None:
    if value == "":
        return  # presence is loop-dependent — owned by the guide walker
    ref = spec.ref
    common = dict(
        segment_id=seg.seg_id,
        segment_position=seg.position,
        element_position=spec.element,
        component_position=component,
        loop_id=spec.loop_id,
        transaction_set=txn.set_code,
        transaction_control=txn.control_number,
    )

    # SNIP 1 — data type.
    if not _type_ok(value, spec.data_type):
        report.add(
            ValidationIssue(
                snip_type=SnipType.INTEGRITY,
                severity=Severity.ERROR,
                code=f"STRUCT.{ref}.TYPE",
                message=(
                    f"{ref} ({spec.description}) must be type {spec.data_type}; "
                    f"value '{value}' is not."
                ),
                expected=spec.data_type,
                actual=value,
                spec_ref=f"{txn.implementation_version} {ref}",
                **common,
            )
        )
        return  # a wrong-typed value's length/enum check is meaningless

    # SNIP 1 — min/max length.
    length = _significant_len(value, spec.data_type)
    if spec.max_len is not None and length > spec.max_len:
        report.add(
            ValidationIssue(
                snip_type=SnipType.INTEGRITY,
                severity=Severity.ERROR,
                code=f"STRUCT.{ref}.MAXLEN",
                message=(
                    f"{ref} ({spec.description}) exceeds max length "
                    f"{spec.max_len} (got {length})."
                ),
                expected=f"<= {spec.max_len}",
                actual=str(length),
                spec_ref=f"{txn.implementation_version} {ref}",
                **common,
            )
        )
    elif spec.min_len is not None and length < spec.min_len:
        report.add(
            ValidationIssue(
                snip_type=SnipType.INTEGRITY,
                severity=Severity.ERROR,
                code=f"STRUCT.{ref}.MINLEN",
                message=(
                    f"{ref} ({spec.description}) below min length "
                    f"{spec.min_len} (got {length})."
                ),
                expected=f">= {spec.min_len}",
                actual=str(length),
                spec_ref=f"{txn.implementation_version} {ref}",
                **common,
            )
        )

    # SNIP 5 — enumerated value binding (warn: union may be loop-incomplete).
    if spec.enumerated and spec.data_type.upper() == "ID":
        if value.strip().upper() not in spec.values:
            report.add(
                ValidationIssue(
                    snip_type=SnipType.CODE_SET,
                    severity=Severity.WARNING,
                    code=f"STRUCT.{ref}.CODESET",
                    message=(
                        f"{ref} ({spec.description}) value '{value}' is not in the "
                        f"implementation guide's permitted set for this position."
                    ),
                    expected="/".join(sorted(spec.values)[:12]),
                    actual=value,
                    spec_ref=f"{txn.implementation_version} {ref}",
                    **common,
                )
            )


def validate_structure(txn: Transaction, report: ValidationReport) -> None:
    """Validate every element of ``txn`` against the bundled TR3 reference."""
    ref = _reference_for(txn)
    if ref is None:
        return
    for seg in txn.segments:
        if seg.seg_id in _SKIP_SEGMENTS or not ref.has_segment(seg.seg_id):
            continue
        for n in range(1, seg.max_element + 1):
            value = seg.elem(n)
            if value == "":
                continue
            if ref.is_composite(seg.seg_id, n):
                comps = seg.components(n)
                for ci, cval in enumerate(comps, start=1):
                    cspec = ref.element(seg.seg_id, n, ci)
                    if cspec is not None:
                        _check_value(
                            cspec, cval, seg, txn, report, component=ci
                        )
            else:
                spec = ref.element(seg.seg_id, n, None)
                if spec is not None:
                    _check_value(spec, value, seg, txn, report, component=None)
