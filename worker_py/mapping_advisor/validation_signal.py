"""Infer validation-rule suggestions from 999 IK3/IK4 error positions.

The 999 reports syntax/IG errors at segment (IK3) and element (IK4) level, with
the offending loop id (IK303) and error codes (IK304 segment, IK403 element).
These error positions are a strong supervised signal for *which* mapping /
validation rules are wrong, so we translate them into advisory
``ValidationRuleSuggestion`` records (loop/segment/element + inferred
requirement) that a human can review alongside the candidate mappings.

Reference: ASC X12 005010X231A1 (999 Implementation Acknowledgment).
"""

from __future__ import annotations

from typing import Optional

from .model import ValidationRuleSuggestion

try:  # package context
    from worker_py.validation.parser import parse
except ModuleNotFoundError:  # script context
    from validation.parser import parse

# IK304 — implementation segment syntax error codes.
_SEGMENT_ERROR = {
    "1": "unrecognized segment id",
    "2": "unexpected segment",
    "3": "mandatory segment missing — ensure the mapping emits this segment",
    "4": "loop occurs over maximum times",
    "5": "segment exceeds maximum use",
    "6": "segment not in transaction set",
    "7": "segment not in proper sequence",
    "8": "segment has data element errors",
}

# IK403 — implementation data element syntax error codes.
_ELEMENT_ERROR = {
    "1": "mandatory data element missing — map a source field to this position",
    "2": "conditional required data element missing",
    "3": "too many data elements",
    "4": "the data element is too short",
    "5": "the data element is too long",
    "6": "invalid character in data element",
    "7": "element must use a valid code value",
    "8": "element must be a valid date",
    "9": "element must be a valid time",
    "10": "exclusion condition violated",
    "12": "too many repetitions",
    "13": "too many components",
}


def _leading_int(value: str) -> Optional[int]:
    digits = ""
    for ch in value:
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else None


def _confidence(occurrences: int) -> float:
    return round(min(0.95, 0.5 + 0.1 * occurrences), 4)


def infer_validation_rules(ack_999_text: str) -> list[ValidationRuleSuggestion]:
    """Parse a 999 and infer advisory validation rules from its error positions."""
    if not ack_999_text or not ack_999_text.strip():
        return []
    doc = parse(ack_999_text)

    # Aggregate by (loop, segment, element_position, error_code).
    aggregate: dict[tuple, dict] = {}
    current_claim: Optional[str] = None

    for txn in doc.transactions:
        last_ik3: Optional[tuple[str, str]] = None  # (loop, segment)
        for seg in txn.segments:
            sid = seg.seg_id
            if sid == "AK2":
                # A new acknowledged transaction set — reset claim context so a
                # business-unit CTX does not bleed across transactions.
                current_claim = None
            elif sid == "CTX":
                # The "business unit identifier" CTX carries CLM01 for 837s,
                # e.g. CTX*CLM01:CLAIMID. Capture it as a claim reference.
                for pos in range(1, seg.max_element + 1):
                    comp0 = seg.comp(pos, 1)
                    if comp0.upper() == "CLM01":
                        current_claim = seg.comp(pos, 2) or current_claim
            elif sid == "IK3":
                segment_id = seg.elem(1)
                loop_id = seg.elem(3) or "?"
                error_code = seg.elem(4)
                last_ik3 = (loop_id, segment_id)
                if error_code and error_code != "8":
                    requirement = _SEGMENT_ERROR.get(
                        error_code, f"segment syntax error code {error_code}"
                    )
                    _add(
                        aggregate,
                        loop_id,
                        segment_id,
                        None,
                        error_code,
                        requirement,
                        current_claim,
                    )
            elif sid == "IK4" and last_ik3 is not None:
                loop_id, segment_id = last_ik3
                element_position = _leading_int(seg.elem(1))
                error_code = seg.elem(3)
                requirement = _ELEMENT_ERROR.get(
                    error_code, f"element syntax error code {error_code}"
                )
                _add(
                    aggregate,
                    loop_id,
                    segment_id,
                    element_position,
                    error_code,
                    requirement,
                    current_claim,
                )

    suggestions: list[ValidationRuleSuggestion] = []
    for (loop_id, segment_id, element_position, error_code), data in aggregate.items():
        suggestions.append(
            ValidationRuleSuggestion(
                loop_path=loop_id,
                segment_id=segment_id,
                element_position=element_position,
                error_code=error_code,
                requirement=data["requirement"],
                occurrences=data["occurrences"],
                confidence=_confidence(data["occurrences"]),
                claim_refs=sorted(data["claims"]),
            )
        )
    # Most-frequent, most-confident first.
    suggestions.sort(key=lambda s: (s.occurrences, s.confidence), reverse=True)
    return suggestions


def _add(aggregate, loop_id, segment_id, element_position, error_code, requirement, claim):
    key = (loop_id, segment_id, element_position, error_code)
    entry = aggregate.setdefault(
        key, {"occurrences": 0, "requirement": requirement, "claims": set()}
    )
    entry["occurrences"] += 1
    if claim:
        entry["claims"].add(claim)
