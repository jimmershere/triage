"""Individual CMS payment-edit modules for the scrubbing engine.

Each module exposes an ``apply(claims, report, **ctx)`` function that inspects a
list of :class:`validation.model.ClaimProjection` and appends
:class:`scrubbing.model.ScrubFinding` records to the report.
"""
