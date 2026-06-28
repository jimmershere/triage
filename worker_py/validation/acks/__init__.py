"""Conformant X12 acknowledgment generation.

From a parsed document plus its :class:`ValidationReport` this package emits the
three acknowledgments a CMS-facing claims system must return:

- **TA1** — Interchange Acknowledgment (ISA/IEA envelope integrity).
- **999** — Implementation Acknowledgment (005010X231A1).
- **277CA** — Health Care Claim Acknowledgment (005010X214).
"""
from __future__ import annotations

from typing import Any

from ..parser import parse
from .ack277ca import generate_277ca
from .ack999 import generate_999
from .ta1 import generate_ta1

__all__ = [
    "generate_ta1",
    "generate_999",
    "generate_277ca",
    "generate_acknowledgments",
    "ACK_PROFILE_999_ONLY",
    "ACK_PROFILE_999_PLUS_277CA",
    "ACK_PROFILES",
    "generate_acks_for_profile",
    "handle_ack_generate",
    "ack_claimtrace_events",
]

# Per-partner acknowledgement profiles (Workstream 7). EDIG does ISA/IEA + 999
# only today, so 999_only is the migration default; the 277CA generator stays
# "dark" until a partner explicitly opts in.
ACK_PROFILE_999_ONLY = "999_only"
ACK_PROFILE_999_PLUS_277CA = "999_plus_277CA"
ACK_PROFILES = (ACK_PROFILE_999_ONLY, ACK_PROFILE_999_PLUS_277CA)


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


def generate_acks_for_profile(doc, report, *, ack_profile: str = ACK_PROFILE_999_ONLY) -> dict[str, str]:
    """Generate the acknowledgements configured for a partner's ack profile.

    Always emits TA1 (interchange) and 999 (implementation). The 277CA is only
    emitted for an 837 when the partner has opted in to ``999_plus_277CA`` —
    otherwise it stays dark, matching EDIG behaviour at migration.
    """
    acks: dict[str, str] = {
        "TA1": generate_ta1(doc, report),
        "999": generate_999(doc, report),
    }
    if ack_profile == ACK_PROFILE_999_PLUS_277CA and report.transaction_set == "837":
        acks["277CA"] = generate_277ca(doc, report)
    return acks


def handle_ack_generate(payload: dict[str, Any]) -> dict[str, Any]:
    """Consume an ``ack.generate`` message and emit the configured ack type.

    ``payload`` carries the original ``x12`` text, the partner ``ack_profile``
    (default ``999_only``) and an optional ``snip_policy`` name to apply before
    determining accept/reject. Returns the acknowledgements plus a compact
    validation summary and any passthrough correlation ids.
    """
    from ..engine import validate_parsed
    from ..policy import apply_policy

    text = payload.get("x12") or payload.get("x12_text") or ""
    ack_profile = payload.get("ack_profile") or ACK_PROFILE_999_ONLY
    if ack_profile not in ACK_PROFILES:
        ack_profile = ACK_PROFILE_999_ONLY

    doc = parse(text)
    report = validate_parsed(doc)
    policy_application = None
    if payload.get("snip_policy"):
        policy_application = apply_policy(report, payload["snip_policy"]).to_dict()

    acks = generate_acks_for_profile(doc, report, ack_profile=ack_profile)
    return {
        "ack_profile": ack_profile,
        "transaction_set": report.transaction_set,
        "acknowledgments": acks,
        "valid": report.is_valid,
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
        "snip_policy": policy_application,
        "correlation_ids": payload.get("correlation_ids", {}),
        "claimtrace_events": ack_claimtrace_events(
            acks, correlation_ids=payload.get("correlation_ids", {})
        ),
    }


def ack_claimtrace_events(
    acks: dict[str, str], *, correlation_ids: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Build append-only Claimtrace events for each generated acknowledgement.

    Every ack is linked to the claim trail so the journal records exactly what
    was returned to the partner. Event ids are content-derived (idempotent).
    """
    import hashlib

    events: list[dict[str, Any]] = []
    for ack_type, text in acks.items():
        content = hashlib.sha256(text.encode("utf-8")).hexdigest()
        events.append(
            {
                "operation_type": f"ack.{ack_type.lower()}.generated",
                "service_name": "ack-generator",
                "event_id": f"ack-{ack_type.lower()}-{content[:32]}",
                "content_hash": content,
                "ack_type": ack_type,
                "correlation_ids": correlation_ids or {},
            }
        )
    return events
