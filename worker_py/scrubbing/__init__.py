"""TurboHEDI claim-scrubbing engine.

Applies CMS payment edits to projected claims before submission:

- NCCI Procedure-to-Procedure (PTP) unbundling
- NCCI Medically Unlikely Edits (MUE) unit limits
- NCD/LCD medical-necessity coverage
- Modifier-to-procedure relationship rules
- ICD-10 diagnosis sequencing rules
- Age / gender demographic edits
- Duplicate-claim / duplicate-line detection
- Member eligibility on date of service

Public surface:
- :func:`scrubbing.engine.scrub_claims` — scrub a list of claim projections.
- :func:`scrubbing.engine.scrub_document` — validate + scrub raw X12 text.
- :class:`scrubbing.model.ScrubReport` — the scrubbing result.
"""
from __future__ import annotations

from .engine import scrub_claims, scrub_document
from .model import EditCategory, ScrubFinding, ScrubReport, ScrubSeverity

__all__ = [
    "EditCategory",
    "ScrubFinding",
    "ScrubReport",
    "ScrubSeverity",
    "scrub_claims",
    "scrub_document",
]
