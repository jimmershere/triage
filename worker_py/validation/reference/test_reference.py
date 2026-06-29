"""Tests for the empirically-extracted X12 TR3 reference (Workstream 2 keystone)."""
from __future__ import annotations

import pytest

from validation.reference import extract_internal_codesets, get_reference
from validation.reference.build_codesets import build


@pytest.fixture(scope="module")
def ref():
    r = get_reference("005010X222")
    if r is None:
        pytest.skip("005010X222 reference bundle not present")
    return r


def test_reference_loads_many_elements(ref):
    assert ref.transaction == "005010X222"
    assert "Professional" in ref.name
    assert len(ref) > 1000  # the 837P guide has well over a thousand positions


def test_simple_element_spec(ref):
    dmg03 = ref.element("DMG", 3)
    assert dmg03 is not None
    assert dmg03.data_type == "ID"
    assert (dmg03.min_len, dmg03.max_len) == (1, 1)
    assert dmg03.required
    assert set(dmg03.values) >= {"F", "M"}


def test_composite_lookup(ref):
    assert ref.is_composite("CLM", 5)
    comps = ref.composite_specs("CLM", 5)
    assert [c.component for c in comps][:3] == [1, 2, 3]
    facility = ref.element("CLM", 5, 2)
    assert facility is not None and facility.data_type == "ID"


def test_numeric_element_attributes(ref):
    sv102 = ref.element("SV1", 2)  # line charge amount
    assert sv102 is not None
    assert sv102.data_type == "R"


def test_internal_codesets_extracted(ref):
    cs = extract_internal_codesets("005010X222")
    assert set(cs) >= {"gender", "entity_identifier", "relationship"}
    assert set(cs["gender"]) >= {"F", "M"}
    assert len(cs["entity_identifier"]) >= 10


def test_build_codesets_is_union_only(ref):
    """Deepening must never shrink an existing curated table (no false rejects)."""
    report = build(write=False)
    assert report  # at least one code set merged
    for name, r in report.items():
        assert r["after"] >= r["before"], f"{name} shrank: {r}"
        assert r["added"] >= 0
