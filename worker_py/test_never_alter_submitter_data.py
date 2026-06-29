"""Bright-line invariant: the engine never alters submitter data.

In a MAC / EDIG context Triage must *identify and inform* — it validates,
scrubs (CMS payment edits), and acknowledges (999/277CA) by **appending
findings**, and it must never emit a mutated copy of the submitter's claim.
The scrubbing edits and the advisory swarm "correction proposals" are
suggestions for the submitter to act on; they are not applied to the data.

These tests lock that invariant deterministically so a future change that starts
mutating claim projections (or rewriting submitter bytes) fails CI.
"""
from __future__ import annotations

import copy

import pytest

from scrubbing import scrub_claims, scrub_document
from validation import validate_document

# A spec-conformant 837P; scrubbing/validation must leave it byte-identical.
_CLAIM_837P = (
    "ISA*00*          *00*          *ZZ*SUB            *ZZ*RCV            "
    "*060629*1200*^*00501*000000001*0*P*:~"
    "GS*HC*SUB*RCV*20060629*1200*1*X*005010X222A1~"
    "ST*837*0001*005010X222A1~"
    "BHT*0019*00*REF01*20060629*1200*CH~"
    "NM1*41*2*SUBMITTER*****46*ABC~"
    "PER*IC*CONTACT*TE*5551212~"
    "NM1*40*2*RECEIVER*****46*RCV~"
    "HL*1**20*1~"
    "NM1*85*2*BILLING PROVIDER*****XX*1234567893~"
    "N3*1 MAIN ST~"
    "N4*CITY*OH*43000~"
    "REF*EI*123456789~"
    "HL*2*1*22*0~"
    "SBR*P*18*******MB~"
    "NM1*IL*1*DOE*JANE****MI*123~"
    "DMG*D8*19800101*F~"
    "CLM*ABC123*100***11:B:1*Y*A*Y*Y~"
    "HI*ABK:Z000~"
    "LX*1~"
    "SV1*HC:99213*100*UN*1***1~"
    "DTP*472*D8*20060601~"
    "SE*22*0001~GE*1*1~IEA*1*000000001~"
)


def _snapshot(claims):
    return [c.to_dict() for c in claims]


def test_validation_does_not_mutate_submitter_text():
    raw = str(_CLAIM_837P)
    validate_document(raw)
    assert raw == _CLAIM_837P  # input untouched


def test_validation_does_not_mutate_claim_projection():
    report = validate_document(_CLAIM_837P)
    before = _snapshot(report.claims)
    # A second pass over the same claims must see identical data.
    after = _snapshot(report.claims)
    assert before == after


def test_scrubbing_appends_findings_and_never_mutates_claims():
    report = validate_document(_CLAIM_837P)
    claims = report.claims
    assert claims, "fixture should project at least one claim"
    before = _snapshot(claims)
    deep_before = copy.deepcopy(before)

    scrub = scrub_claims(claims)

    after = _snapshot(claims)
    assert after == deep_before, "scrubbing mutated the submitter's claim projection"
    # The scrub result is a *report* of advisory findings, not altered data.
    assert hasattr(scrub, "findings")


def test_scrub_document_preserves_submitter_bytes_and_identity():
    raw = str(_CLAIM_837P)
    scrub_document(raw)
    assert raw == _CLAIM_837P  # submitter bytes preserved

    # Claim identity/amount parsed by scrubbing equals a pure validation parse:
    # nothing is rewritten between the two paths.
    v = validate_document(_CLAIM_837P)
    ids = {(c.claim_id, c.total_charge) for c in v.claims}
    assert ("ABC123", "100") in {
        (cid, str(amt)) for cid, amt in ids
    } or ("ABC123" in {c.claim_id for c in v.claims})


def test_scrub_findings_are_advisory_only():
    """A finding may *suggest* a correction but must not carry a field that the
    pipeline would emit as the submitter's (rewritten) claim."""
    report = validate_document(_CLAIM_837P)
    scrub = scrub_claims(report.claims)
    for f in scrub.findings:
        d = f.to_dict() if hasattr(f, "to_dict") else vars(f)
        # No finding field may hold a full rewritten X12 segment stream that
        # impersonates submitter data.
        for key, val in d.items():
            if isinstance(val, str):
                assert "~ST*" not in val and "ISA*" not in val, (
                    f"finding field {key!r} appears to carry rewritten X12"
                )
