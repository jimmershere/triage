"""Tests for table-driven TR3 structural validation (Workstream 2 keystone)."""
from __future__ import annotations

import pytest

from validation import validate_document
from validation.model import Severity, SnipType
from validation.reference import get_reference

pytestmark = pytest.mark.skipif(
    get_reference("005010X222") is None,
    reason="005010X222 reference bundle not present",
)

# A small but spec-conformant 837P interchange used as the clean baseline.
_CLEAN_837P = (
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


def _struct(report):
    return [i for i in report.issues if i.code.startswith("STRUCT.")]


def test_clean_claim_has_no_structural_errors():
    r = validate_document(_CLEAN_837P)
    errs = [i for i in _struct(r) if i.severity in (Severity.ERROR, Severity.FATAL)]
    assert errs == [], [i.code for i in errs]


def test_bad_data_type_is_type1_error():
    bad = _CLEAN_837P.replace("SV1*HC:99213*100*UN*1***1~", "SV1*HC:99213*ABC*UN*1***1~")
    r = validate_document(bad)
    hits = [i for i in _struct(r) if i.code == "STRUCT.SV102.TYPE"]
    assert hits, "expected a data-type error on SV102"
    assert hits[0].snip_type == SnipType.INTEGRITY
    assert hits[0].severity == Severity.ERROR


def test_overlong_value_is_maxlen_error():
    bad = _CLEAN_837P.replace("DMG*D8*19800101*F~", "DMG*D8*19800101*FEMALE~")
    r = validate_document(bad)
    # gender is ID len 1/1 -> 'FEMALE' is both wrong enum and overlong.
    codes = {i.code for i in _struct(r)}
    assert "STRUCT.DMG03.MAXLEN" in codes or "STRUCT.DMG03.TYPE" in codes




def test_composite_component_is_validated():
    # CLM05-02 facility code qualifier is ID; inject a too-long component.
    bad = _CLEAN_837P.replace("11:B:1", "11:BBBB:1")
    r = validate_document(bad)
    codes = {i.code for i in _struct(r)}
    assert any(c.startswith("STRUCT.CLM05-02") for c in codes), codes
