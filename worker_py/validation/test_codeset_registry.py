"""Tests for the effective-dated code-set registry + loader (Workstream 2)."""
import unittest
from datetime import date

from validation.codesets.loader_service import (
    ChecksumMismatch,
    compute_checksum,
    ingest_version,
    load_seed_versions,
)
from validation.codesets.registry import CodesetRegistry


class RegistryResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = load_seed_versions()

    def test_seed_versions_loaded(self) -> None:
        self.assertIn("icd10cm", self.reg.codesets())
        self.assertGreaterEqual(len(self.reg.versions("icd10cm")), 2)

    def test_resolve_version_by_service_date(self) -> None:
        # A 2025 spring service date resolves to FY2025 (effective 2024-10-01).
        v = self.reg.resolve_version("icd10cm", date(2025, 6, 1))
        self.assertEqual(v.version_label, "FY2025")
        # An autumn 2025 service date resolves to FY2026 (effective 2025-10-01).
        v2 = self.reg.resolve_version("icd10cm", date(2025, 11, 1))
        self.assertEqual(v2.version_label, "FY2026")

    def test_new_code_only_valid_after_effective_window(self) -> None:
        # E1100 is new in FY2026 — invalid for a FY2025 service date.
        self.assertFalse(self.reg.is_valid("E1100", "icd10cm", date(2025, 6, 1)))
        self.assertTrue(self.reg.is_valid("E1100", "icd10cm", date(2025, 11, 1)))

    def test_deactivated_code_remains_valid_historically(self) -> None:
        # Z01818 deactivated 2026-03-31: valid on/before, invalid strictly after.
        self.assertTrue(self.reg.is_valid("Z01818", "icd10cm", date(2026, 3, 1)))
        self.assertFalse(self.reg.is_valid("Z01818", "icd10cm", date(2026, 5, 1)))

    def test_stable_code_valid_across_versions(self) -> None:
        self.assertTrue(self.reg.is_valid("E119", "icd10cm", date(2025, 6, 1)))
        self.assertTrue(self.reg.is_valid("E119", "icd10cm", date(2026, 1, 1)))

    def test_unknown_codeset_defers_to_format_validation(self) -> None:
        # Not loaded -> True so callers fall back rather than false-reject.
        self.assertTrue(self.reg.is_valid("XYZ", "not_loaded", date(2026, 1, 1)))

    def test_resolve_reports_reason(self) -> None:
        res = self.reg.resolve("E1100", "icd10cm", date(2025, 6, 1))
        self.assertFalse(res.valid)
        self.assertTrue(res.known_codeset)


class LoaderServiceTests(unittest.TestCase):
    def test_checksum_is_order_independent(self) -> None:
        a = [{"code": "A1", "description": "x"}, {"code": "B2", "description": "y"}]
        b = [{"code": "B2", "description": "y"}, {"code": "A1", "description": "x"}]
        self.assertEqual(compute_checksum(a), compute_checksum(b))

    def test_checksum_mismatch_raises(self) -> None:
        reg = CodesetRegistry()
        with self.assertRaises(ChecksumMismatch):
            ingest_version(
                reg,
                codeset="carc",
                version_label="bad",
                values=[{"code": "1", "description": "Deductible"}],
                expected_checksum="deadbeef",
            )

    def test_ingest_bumps_generation_and_invalidates_cache(self) -> None:
        reg = CodesetRegistry()
        ingest_version(
            reg,
            codeset="carc",
            version_label="2025-01",
            values=[{"code": "1", "description": "Deductible"}],
            source_effective_date="2025-01-01",
        )
        gen1 = reg.generation
        # Prime the cache.
        self.assertTrue(reg.is_valid("1", "carc", date(2025, 6, 1)))
        ingest_version(
            reg,
            codeset="carc",
            version_label="2025-11",
            values=[
                {"code": "1", "description": "Deductible"},
                {"code": "253", "description": "Sequestration"},
            ],
            source_effective_date="2025-11-01",
        )
        self.assertGreater(reg.generation, gen1)
        self.assertTrue(reg.is_valid("253", "carc", date(2025, 12, 1)))


if __name__ == "__main__":
    unittest.main()
