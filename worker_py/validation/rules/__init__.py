"""Rule modules for the X12 validation engine.

Each module exposes validation functions that take parsed structures plus a
:class:`validation.model.ValidationReport` and append :class:`ValidationIssue`
records. Modules are intentionally small and SNIP-focused so coverage is easy
to audit.
"""
