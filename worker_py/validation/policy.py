"""Per-partner SNIP severity policy engine (Workstream 7).

Every validation check already declares a WEDI SNIP type (:class:`SnipType`) and
a stable, grep-able rule id (the ``code`` field on :class:`ValidationIssue`).
This module adds the *toggle* layer on top: a resolver that decides, per
``(transaction_type, snip_type)``, whether a finding should

* ``enforce-reject`` — keep its rejecting severity (FATAL/ERROR),
* ``warn``           — downgrade a rejecting finding to a WARNING, or
* ``off``            — silence it to INFO (still detected and reported, never
  dropped — Triage *detects and reports, never modifies*).

The policy data is owned by Workstream 6 (`partner_snip_policy`, effective
dated). :func:`policy_from_rows` builds a :class:`SnipPolicy` from those rows so
the worker/validation engine never has to import the API/database layer.

A named default policy ``edig-parity-v1`` mirrors EDIG leniency: SNIP types 1-2
are enforced, 3-6 warn, and 7 is off. It is the policy applied at migration so
Triage reproduces EDIG accept/reject behaviour, to be tightened per partner.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Iterable, Mapping

from .model import Severity, SnipType, ValidationReport


class PolicyMode(str, Enum):
    """How a SNIP finding is treated for a given partner/transaction."""

    ENFORCE_REJECT = "enforce-reject"
    WARN = "warn"
    OFF = "off"


# A downgrade target for each non-enforcing mode.
_MODE_TARGET = {
    PolicyMode.WARN: Severity.WARNING,
    PolicyMode.OFF: Severity.INFO,
}


def _coerce_mode(value: str | PolicyMode) -> PolicyMode:
    if isinstance(value, PolicyMode):
        return value
    cleaned = str(value).strip().lower()
    for mode in PolicyMode:
        if cleaned == mode.value:
            return mode
    # Tolerate a few friendly aliases used in companion guides / UIs.
    if cleaned in ("enforce", "reject", "error", "fatal"):
        return PolicyMode.ENFORCE_REJECT
    if cleaned in ("warning", "advisory"):
        return PolicyMode.WARN
    if cleaned in ("ignore", "disabled", "none"):
        return PolicyMode.OFF
    raise ValueError(f"unknown SNIP policy severity '{value}'")


@dataclass(frozen=True)
class PolicyAdjustment:
    """Record of one finding whose severity a policy changed."""

    code: str
    snip_type: int
    transaction_type: str | None
    from_severity: str
    to_severity: str
    mode: str


@dataclass
class PolicyApplication:
    """Outcome of applying a :class:`SnipPolicy` to a report."""

    policy_name: str
    adjustments: list[PolicyAdjustment] = field(default_factory=list)

    @property
    def adjusted_count(self) -> int:
        return len(self.adjustments)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_name": self.policy_name,
            "adjusted_count": self.adjusted_count,
            "adjustments": [a.__dict__ for a in self.adjustments],
        }


@dataclass
class SnipPolicy:
    """Resolved SNIP severity policy for one partner.

    ``default_modes`` maps a SNIP type (1-7) to a :class:`PolicyMode`. Anything
    not listed defaults to ``enforce-reject`` so an unconfigured partner is
    strict by default. ``overrides`` are keyed by ``(transaction_type, snip)``
    and win over the per-type default — mirroring the Oracle B2B / IBM Sterling
    model where the trading-partner+document setting overrides the global one.
    """

    name: str
    default_modes: dict[int, PolicyMode] = field(default_factory=dict)
    overrides: dict[tuple[str, int], PolicyMode] = field(default_factory=dict)

    def mode_for(
        self, snip_type: SnipType | int, transaction_type: str | None = None
    ) -> PolicyMode:
        snip = int(snip_type)
        if transaction_type:
            key = (transaction_type.strip(), snip)
            if key in self.overrides:
                return self.overrides[key]
        return self.default_modes.get(snip, PolicyMode.ENFORCE_REJECT)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "default_modes": {str(k): v.value for k, v in sorted(self.default_modes.items())},
            "overrides": {
                f"{txn}:{snip}": mode.value
                for (txn, snip), mode in sorted(self.overrides.items())
            },
        }


# ---------------------------------------------------------------------------
# Named built-in policies
# ---------------------------------------------------------------------------

#: Strict default — every SNIP type rejects. Equivalent to "no policy".
STRICT_ALL = SnipPolicy(
    name="strict-all",
    default_modes={int(s): PolicyMode.ENFORCE_REJECT for s in SnipType},
)

#: EDIG-parity migration default: enforce syntax + IG (1-2), warn 3-6, off 7.
EDIG_PARITY_V1 = SnipPolicy(
    name="edig-parity-v1",
    default_modes={
        int(SnipType.INTEGRITY): PolicyMode.ENFORCE_REJECT,
        int(SnipType.REQUIREMENT): PolicyMode.ENFORCE_REJECT,
        int(SnipType.BALANCING): PolicyMode.WARN,
        int(SnipType.SITUATIONAL): PolicyMode.WARN,
        int(SnipType.CODE_SET): PolicyMode.WARN,
        int(SnipType.LINE_BALANCING): PolicyMode.WARN,
        int(SnipType.GUIDE_SPECIFIC): PolicyMode.OFF,
    },
)

_NAMED_POLICIES = {p.name: p for p in (STRICT_ALL, EDIG_PARITY_V1)}


def named_policy(name: str) -> SnipPolicy:
    """Return a built-in policy by name, defaulting to ``edig-parity-v1``."""
    return _NAMED_POLICIES.get(name, EDIG_PARITY_V1)


def available_policies() -> list[str]:
    return sorted(_NAMED_POLICIES)


# ---------------------------------------------------------------------------
# Building a policy from effective-dated partner_snip_policy rows (WS6)
# ---------------------------------------------------------------------------

def _row_in_effect(row: Mapping[str, Any], as_of: date) -> bool:
    eff_from = row.get("effective_from")
    eff_to = row.get("effective_to")
    if isinstance(eff_from, str) and eff_from:
        eff_from = date.fromisoformat(eff_from[:10])
    if isinstance(eff_to, str) and eff_to:
        eff_to = date.fromisoformat(eff_to[:10])
    if eff_from and as_of < eff_from:
        return False
    if eff_to and as_of > eff_to:
        return False
    return True


def policy_from_rows(
    name: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    as_of: date | None = None,
    base: SnipPolicy | None = None,
) -> SnipPolicy:
    """Build a :class:`SnipPolicy` from ``partner_snip_policy`` rows.

    Each row carries ``snip_type``, ``severity`` (a :class:`PolicyMode` value)
    and optional ``transaction_type``/``effective_from``/``effective_to``. Rows
    whose effective window does not contain ``as_of`` are ignored. ``base``
    supplies fall-through defaults (defaults to ``edig-parity-v1``).
    """
    as_of = as_of or date.today()
    base = base or EDIG_PARITY_V1
    default_modes = dict(base.default_modes)
    overrides = dict(base.overrides)
    for row in rows:
        if not _row_in_effect(row, as_of):
            continue
        snip = int(row["snip_type"])
        mode = _coerce_mode(row["severity"])
        txn = (row.get("transaction_type") or "").strip()
        if txn and txn not in ("*", "ALL", "all"):
            overrides[(txn, snip)] = mode
        else:
            default_modes[snip] = mode
    return SnipPolicy(name=name, default_modes=default_modes, overrides=overrides)


# ---------------------------------------------------------------------------
# Applying a policy to a validation report
# ---------------------------------------------------------------------------

def apply_policy(
    report: ValidationReport,
    policy: SnipPolicy | str | None,
    *,
    transaction_type: str | None = None,
) -> PolicyApplication:
    """Adjust issue severities in ``report`` per ``policy`` and report changes.

    The report is mutated in place: rejecting findings (FATAL/ERROR) whose SNIP
    type is configured ``warn`` become WARNINGs, and ``off`` become INFO. The
    finding is never removed — it remains visible for the rejection report and
    audit trail. ``enforce-reject`` (and unknown SNIP types) are left untouched.
    """
    if policy is None:
        return PolicyApplication(policy_name="none")
    if isinstance(policy, str):
        policy = named_policy(policy)

    default_txn = transaction_type or report.transaction_set
    application = PolicyApplication(policy_name=policy.name)

    for issue in report.issues:
        if not issue.severity.rejects:
            continue
        txn = issue.transaction_set or default_txn
        mode = policy.mode_for(issue.snip_type, txn)
        target = _MODE_TARGET.get(mode)
        if target is None or target == issue.severity:
            continue
        application.adjustments.append(
            PolicyAdjustment(
                code=issue.code,
                snip_type=int(issue.snip_type),
                transaction_type=txn,
                from_severity=issue.severity.value,
                to_severity=target.value,
                mode=mode.value,
            )
        )
        issue.severity = target
    return application
