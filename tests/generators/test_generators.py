"""Unit tests for the EDI test file generator library.

Tests envelope structure, size targeting, multi-part envelopes,
and adversarial file properties.
"""
from __future__ import annotations

import os
import random
import tempfile
import unittest

from . import (
    generate_837p,
    generate_837i,
    generate_837d,
    generate_835,
    generate_270,
    generate_271,
    generate_276,
    generate_278,
    generate_adversarial_files,
    wrap_multi_gs,
)


class EnvelopeStructureTestMixin:
    """Mixin providing ISA..IEA envelope structure assertions."""

    def assert_valid_envelope(self, content: str, expected_st_id: str | None = None):
        """Assert content has valid ISA/GS/ST..SE/GE/IEA structure."""
        self.assertTrue(content.startswith("ISA*"), "Must start with ISA")
        self.assertTrue(content.rstrip().endswith("~"), "Must end with segment terminator")

        segments = [s for s in content.split("~") if s.strip()]

        # ISA must be first
        self.assertTrue(segments[0].startswith("ISA*"))
        # IEA must be last
        self.assertTrue(segments[-1].startswith("IEA*"))

        # Find GS, GE, ST, SE segments
        gs_count = sum(1 for s in segments if s.startswith("GS*"))
        ge_count = sum(1 for s in segments if s.startswith("GE*"))
        st_count = sum(1 for s in segments if s.startswith("ST*"))
        se_count = sum(1 for s in segments if s.startswith("SE*"))

        self.assertGreaterEqual(gs_count, 1, "Must have at least one GS")
        self.assertEqual(gs_count, ge_count, "GS and GE counts must match")
        self.assertGreaterEqual(st_count, 1, "Must have at least one ST")
        self.assertEqual(st_count, se_count, "ST and SE counts must match")

        # ISA version check
        isa_elements = segments[0].split("*")
        self.assertEqual(isa_elements[12], "00501", "ISA12 must be 00501")

        # Validate transaction ID if specified
        if expected_st_id:
            for s in segments:
                if s.startswith("ST*"):
                    st_elements = s.split("*")
                    self.assertEqual(
                        st_elements[1], expected_st_id,
                        f"ST01 must be {expected_st_id}"
                    )
                    break


class Test837PGenerator(EnvelopeStructureTestMixin, unittest.TestCase):
    def test_basic_generation(self):
        content = generate_837p(claim_count=5)
        self.assert_valid_envelope(content, "837")

    def test_contains_required_segments(self):
        content = generate_837p(claim_count=3)
        segments = [s.split("*")[0] for s in content.split("~") if s.strip()]
        required = ["ISA", "GS", "ST", "BHT", "NM1", "PER", "HL",
                     "SBR", "CLM", "DTP", "HI", "LX", "SV1", "SE", "GE", "IEA"]
        for seg_id in required:
            self.assertIn(seg_id, segments, f"Missing required segment: {seg_id}")

    def test_version_005010x222a1(self):
        content = generate_837p(claim_count=1)
        self.assertIn("005010X222A1", content)

    def test_reproducibility(self):
        a = generate_837p(claim_count=5)
        b = generate_837p(claim_count=5)
        self.assertEqual(a, b, "Same seed should produce identical output")


class Test837IGenerator(EnvelopeStructureTestMixin, unittest.TestCase):
    def test_basic_generation(self):
        content = generate_837i(claim_count=5)
        self.assert_valid_envelope(content, "837")

    def test_contains_institutional_segments(self):
        content = generate_837i(claim_count=10)
        self.assertIn("005010X223A2", content)
        # SV2 segments should be present (institutional service lines)
        self.assertIn("SV2*", content)
        # At least some inpatient claims should have CL1
        self.assertIn("CL1*", content)


class Test837DGenerator(EnvelopeStructureTestMixin, unittest.TestCase):
    def test_basic_generation(self):
        content = generate_837d(claim_count=5)
        self.assert_valid_envelope(content, "837")

    def test_contains_dental_segments(self):
        content = generate_837d(claim_count=5)
        self.assertIn("005010X224A2", content)
        self.assertIn("SV3*", content)
        self.assertIn("TOO*", content)

    def test_ada_codes_present(self):
        content = generate_837d(claim_count=10)
        # At least one ADA code should appear
        self.assertTrue(
            any(code in content for code in ["D0120", "D1110", "D2140", "D2150"]),
            "ADA procedure codes should be present",
        )


class Test835Generator(EnvelopeStructureTestMixin, unittest.TestCase):
    def test_basic_generation(self):
        content = generate_835(claim_count=5)
        self.assert_valid_envelope(content, "835")

    def test_contains_remittance_segments(self):
        content = generate_835(claim_count=5)
        self.assertIn("005010X221A1", content)
        self.assertIn("BPR*", content)
        self.assertIn("TRN*", content)
        self.assertIn("CLP*", content)

    def test_claim_status_mix(self):
        content = generate_835(claim_count=50)
        segments = [s for s in content.split("~") if s.startswith("CLP*")]
        statuses = [s.split("*")[2] for s in segments]
        # With 50 claims, we should see a mix
        unique = set(statuses)
        self.assertTrue(len(unique) >= 2, "Should have mix of claim statuses")


class Test270Generator(EnvelopeStructureTestMixin, unittest.TestCase):
    def test_basic_generation(self):
        content = generate_270(member_count=5)
        self.assert_valid_envelope(content, "270")
        self.assertIn("005010X279A1", content)

    def test_contains_eligibility_segments(self):
        content = generate_270(member_count=5)
        self.assertIn("EQ*", content)
        self.assertIn("TRN*", content)


class Test271Generator(EnvelopeStructureTestMixin, unittest.TestCase):
    def test_basic_generation(self):
        content = generate_271(member_count=5)
        self.assert_valid_envelope(content, "271")
        self.assertIn("005010X279A1", content)

    def test_contains_eb_segments(self):
        content = generate_271(member_count=10)
        self.assertIn("EB*", content)
        # Should have both active and inactive with 10 members
        eb_segments = [s for s in content.split("~") if s.startswith("EB*")]
        self.assertTrue(len(eb_segments) > 0, "Should have EB segments")


class Test276Generator(EnvelopeStructureTestMixin, unittest.TestCase):
    def test_basic_generation(self):
        content = generate_276(inquiry_count=5)
        self.assert_valid_envelope(content, "276")
        self.assertIn("005010X212", content)

    def test_contains_status_segments(self):
        content = generate_276(inquiry_count=5)
        self.assertIn("STC*", content)
        self.assertIn("TRN*", content)


class Test278Generator(EnvelopeStructureTestMixin, unittest.TestCase):
    def test_basic_generation(self):
        content = generate_278(auth_count=5)
        self.assert_valid_envelope(content, "278")
        self.assertIn("005010X217", content)

    def test_contains_auth_segments(self):
        content = generate_278(auth_count=5)
        self.assertIn("UM*", content)
        self.assertIn("HCR*", content)


class TestSizeTargeting(unittest.TestCase):
    """Test that size targeting produces output within ±5% of target."""

    def _assert_within_tolerance(self, content: str, target: int, tolerance: float = 0.05):
        actual = len(content.encode())
        lo = target * (1 - tolerance)
        hi = target * (1 + tolerance)
        self.assertTrue(
            lo <= actual <= hi,
            f"Expected {target:,}±{tolerance:.0%} bytes, got {actual:,} "
            f"(range: {lo:,.0f}-{hi:,.0f})",
        )

    def test_837p_512k(self):
        content = generate_837p(target_bytes=512 * 1024)
        self._assert_within_tolerance(content, 512 * 1024)

    def test_837p_1mb(self):
        content = generate_837p(target_bytes=1024 * 1024)
        self._assert_within_tolerance(content, 1024 * 1024)

    def test_835_512k(self):
        content = generate_835(target_bytes=512 * 1024)
        self._assert_within_tolerance(content, 512 * 1024)

    def test_270_512k(self):
        content = generate_270(target_bytes=512 * 1024)
        self._assert_within_tolerance(content, 512 * 1024)

    def test_837i_512k(self):
        content = generate_837i(target_bytes=512 * 1024)
        self._assert_within_tolerance(content, 512 * 1024)

    def test_837d_512k(self):
        content = generate_837d(target_bytes=512 * 1024)
        self._assert_within_tolerance(content, 512 * 1024)


class TestMultiPartEnvelope(unittest.TestCase):
    """Test multi-part envelope has correct GS/GE counts."""

    def test_multi_gs_counts(self):
        random.seed(42)
        parts = []
        for _ in range(3):
            content = generate_837p(claim_count=3)
            segments = content.split("~")
            st_start = None
            se_end = None
            for j, seg in enumerate(segments):
                if seg.startswith("ST*"):
                    st_start = j
                if seg.startswith("SE*"):
                    se_end = j
            if st_start is not None and se_end is not None:
                tx = "~".join(segments[st_start:se_end + 1]) + "~"
                parts.append(tx)

        result = wrap_multi_gs(parts)
        segments = [s for s in result.split("~") if s.strip()]

        # Should have 3 GS and 3 GE segments
        gs_count = sum(1 for s in segments if s.startswith("GS*"))
        ge_count = sum(1 for s in segments if s.startswith("GE*"))
        self.assertEqual(gs_count, 3)
        self.assertEqual(ge_count, 3)

        # IEA should indicate 3 groups
        iea_seg = [s for s in segments if s.startswith("IEA*")][0]
        iea_count = iea_seg.split("*")[1]
        self.assertEqual(iea_count, "3")

        # ISA and IEA should exist exactly once each
        isa_count = sum(1 for s in segments if s.startswith("ISA*"))
        iea_count_seg = sum(1 for s in segments if s.startswith("IEA*"))
        self.assertEqual(isa_count, 1)
        self.assertEqual(iea_count_seg, 1)


class TestAdversarialFiles(unittest.TestCase):
    """Test adversarial files exist and have expected properties."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="adversarial_")
        cls.files = generate_adversarial_files(cls.tmpdir)

    def test_all_files_created(self):
        expected = [
            "truncated_mid_segment.x12", "missing_iea.x12",
            "missing_gs_ge.x12", "wrong_se_count.x12",
            "wrong_ge_count.x12", "wrong_iea_count.x12",
            "invalid_isa_version.x12", "invalid_gs_version.x12",
            "empty_file.x12", "json_as_x12.x12",
            "xml_as_x12.x12", "plaintext_as_x12.x12",
            "binary_noise.x12", "null_bytes.x12",
            "mixed_delimiters.x12", "utf8_bom.x12",
            "double_terminators.x12", "oversized_element.x12",
            "shell_injection.x12", "sql_injection.x12",
            "script_injection.x12", "path_traversal.x12",
            "oversized_field_1mb.x12", "eicar_embedded.x12",
        ]
        for fname in expected:
            self.assertIn(fname, self.files, f"Missing file: {fname}")
            filepath = os.path.join(self.tmpdir, fname)
            self.assertTrue(os.path.exists(filepath), f"File not on disk: {fname}")

    def test_empty_file_is_zero_bytes(self):
        content = self.files["empty_file.x12"]
        self.assertEqual(len(content), 0)
        filepath = os.path.join(self.tmpdir, "empty_file.x12")
        self.assertEqual(os.path.getsize(filepath), 0)

    def test_truncated_ends_mid_segment(self):
        content = self.files["truncated_mid_segment.x12"]
        self.assertIsInstance(content, str)
        # Should NOT end with ~ (segment terminator)
        self.assertFalse(content.endswith("~"), "Truncated file should not end cleanly")

    def test_missing_iea_has_no_iea(self):
        content = self.files["missing_iea.x12"]
        segments = [s for s in content.split("~") if s.strip()]
        seg_ids = [s.split("*")[0] for s in segments]
        self.assertNotIn("IEA", seg_ids)

    def test_wrong_se_count(self):
        content = self.files["wrong_se_count.x12"]
        se_segs = [s for s in content.split("~") if s.startswith("SE*")]
        self.assertTrue(len(se_segs) > 0)
        se_count = se_segs[0].split("*")[1]
        self.assertEqual(se_count, "999")

    def test_json_is_json(self):
        content = self.files["json_as_x12.x12"]
        self.assertTrue(content.startswith("{"))

    def test_xml_is_xml(self):
        content = self.files["xml_as_x12.x12"]
        self.assertTrue(content.startswith("<?xml"))

    def test_oversized_element(self):
        content = self.files["oversized_element.x12"]
        self.assertIn("A" * 1000, content)  # at least 1000 A's

    def test_shell_injection_content(self):
        content = self.files["shell_injection.x12"]
        self.assertIn("rm -rf", content)
        self.assertIn("curl evil.com", content)

    def test_sql_injection_content(self):
        content = self.files["sql_injection.x12"]
        self.assertIn("DROP TABLE", content)
        self.assertIn("OR 1=1", content)

    def test_script_injection_content(self):
        content = self.files["script_injection.x12"]
        self.assertIn("<script>", content)
        self.assertIn("javascript:", content)

    def test_binary_noise_is_bytes(self):
        content = self.files["binary_noise.x12"]
        self.assertIsInstance(content, bytes)

    def test_utf8_bom_is_bytes(self):
        content = self.files["utf8_bom.x12"]
        self.assertIsInstance(content, bytes)
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))

    def test_oversized_field_1mb(self):
        content = self.files["oversized_field_1mb.x12"]
        # Should contain at least 1MB of X's
        self.assertGreater(len(content), 1024 * 1024)

    def test_eicar_embedded(self):
        content = self.files["eicar_embedded.x12"]
        self.assertIn("EICAR-STANDARD-ANTIVIRUS-TEST-FILE", content)


if __name__ == "__main__":
    unittest.main()
