"""Advisory X12 ⇄ flat-file mapping-suggestion service (Workstream 1).

This package is an **advisory-only** tool. It ingests paired examples (an X12
transaction, optionally its 999 implementation acknowledgement, and the
corresponding flat file) and proposes candidate mapping rules between X12
segments/elements/loops and flat-file fixed-width / delimited fields. It scores
candidates with a transparent, dependency-free logistic model and infers
validation rules from 999 IK3/IK4 error positions.

Governance gate — the module **never** auto-applies mappings or mutates
submissions. It only suggests; a human approves, and approved rules are
versioned like any other config (see ``repository``). It is deliberately
isolated from the live worker processing path and trains on de-identified
structure (loop paths, positions, field shapes), not clinical content.

Pure standard library only (no numpy / ML frameworks) to keep dependencies
light and the scoring fully transparent.
"""

from .advisor import GOVERNANCE_NOTE, MappingAdvisor
from .model import (
    FlatField,
    MappingCandidate,
    MappingFeatures,
    MappingSuggestionSet,
    ValidationRuleSuggestion,
    X12ElementRef,
)
from .repository import InMemoryMappingRepository, MappingRuleRepository
from .scorer import FEATURE_ORDER, LogisticScorer

__all__ = [
    "GOVERNANCE_NOTE",
    "MappingAdvisor",
    "FlatField",
    "MappingCandidate",
    "MappingFeatures",
    "MappingSuggestionSet",
    "ValidationRuleSuggestion",
    "X12ElementRef",
    "InMemoryMappingRepository",
    "MappingRuleRepository",
    "FEATURE_ORDER",
    "LogisticScorer",
]
