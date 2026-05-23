"""External code-set definitions and validators for SNIP type 5 checking.

Bundled code sets live as JSON under ``codesets/data/``. Large proprietary or
very large sets (ICD-10-CM, the full CPT list) are validated by *format* and an
optional bundled sample rather than a complete membership table — see
:mod:`validation.codesets.loader` for details and licensing notes.
"""
from __future__ import annotations

from .loader import (
    CodeSet,
    get_codeset,
    is_valid_hcpcs_cpt,
    is_valid_icd10_cm,
    is_valid_npi,
    is_valid_revenue_code,
    is_valid_taxonomy,
)

__all__ = [
    "CodeSet",
    "get_codeset",
    "is_valid_hcpcs_cpt",
    "is_valid_icd10_cm",
    "is_valid_npi",
    "is_valid_revenue_code",
    "is_valid_taxonomy",
]
