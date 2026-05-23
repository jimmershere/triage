"""Conformant X12 acknowledgment generation.

From a parsed document plus its :class:`ValidationReport` this package emits the
three acknowledgments a CMS-facing claims system must return:

- **TA1** — Interchange Acknowledgment (ISA/IEA envelope integrity).
- **999** — Implementation Acknowledgment (005010X231A1).
- **277CA** — Health Care Claim Acknowledgment (005010X214).
"""
from __future__ import annotations

from ..parser import parse
from .ack277ca import generate_277ca
from .ack999 import generate_999
from .ta1 import generate_ta1

__all__ = ["generate_ta1", "generate_999", "generate_277ca", "generate_acknowledgments"]


def generate_acknowledgments(text: str) -> dict[str, str]:
    """Parse, validate and return all three acknowledgments for an X12 document."""
    from ..engine import validate_parsed

    doc = parse(text)
    report = validate_parsed(doc)
    return {
        "TA1": generate_ta1(doc, report),
        "999": generate_999(doc, report),
        "277CA": generate_277ca(doc, report),
    }
