"""Code-set loader service (Workstream 2).

Ingests an updated code-set file, validates its checksum, writes it as a new
version into a :class:`CodesetRegistry`, and (optionally) publishes a
``codeset.reloaded`` event for cache invalidation — no redeploy required.

The first loaders target the highest-churn / SNIP type 5 core sets: CARC/RARC
and ICD-10-CM. Bundled seed versions live under ``data/versions/*.json`` so the
registry can be populated deterministically in tests and at startup.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Iterable

from .registry import (
    CodesetRegistry,
    CodesetVersion,
    make_version,
)

logger = logging.getLogger("validation.codesets.loader_service")

_DATA_DIR = Path(__file__).resolve().parent / "data"
_VERSIONS_DIR = _DATA_DIR / "versions"


class ChecksumMismatch(ValueError):
    """Raised when an ingested file's checksum does not match the expected one."""


def compute_checksum(values: Any) -> str:
    """Deterministic SHA-256 over a code-set value table.

    Accepts either a ``{code: description}`` map or a ``list[dict]`` of value
    records; the canonical form is sorted by code so byte-irrelevant ordering
    does not change the checksum.
    """
    if isinstance(values, dict):
        canonical = {str(k).strip().upper(): str(v) for k, v in values.items()}
        blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    else:
        rows = []
        for entry in values or []:
            rows.append(
                {
                    "code": str(entry.get("code", "")).strip().upper(),
                    "description": str(entry.get("description", "")),
                    "valid_from": entry.get("valid_from"),
                    "valid_to": entry.get("valid_to"),
                    "status": entry.get("status", "active"),
                }
            )
        rows.sort(key=lambda r: r["code"])
        blob = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def ingest_version(
    registry: CodesetRegistry,
    *,
    codeset: str,
    version_label: str,
    values: Any,
    source_effective_date: Any = None,
    publication_date: Any = None,
    implementation_date: Any = None,
    complete: bool | None = None,
    expected_checksum: str | None = None,
    publish: bool = False,
    rabbit_url: str | None = None,
) -> CodesetVersion:
    """Validate, register and (optionally) announce a new code-set version."""
    checksum = compute_checksum(values)
    if expected_checksum and expected_checksum.lower() != checksum.lower():
        raise ChecksumMismatch(
            f"checksum mismatch for {codeset}/{version_label}: "
            f"expected {expected_checksum}, computed {checksum}"
        )
    version = make_version(
        codeset=codeset,
        version_label=version_label,
        values=values,
        source_effective_date=source_effective_date,
        publication_date=publication_date,
        implementation_date=implementation_date,
        checksum=checksum,
        complete=complete,
    )
    registry.register(version)
    logger.info(
        "Ingested code-set %s version %s (%d values, checksum %s)",
        codeset, version_label, len(version.values), checksum[:12],
    )
    if publish:
        from .events import publish_codeset_reloaded

        publish_codeset_reloaded(
            codeset,
            version_label,
            checksum=checksum,
            generation=registry.generation,
            source_effective_date=(
                version.source_effective_date.isoformat()
                if version.source_effective_date
                else None
            ),
            url=rabbit_url,
        )
    return version


def ingest_version_file(
    registry: CodesetRegistry,
    path: str | Path,
    *,
    publish: bool = False,
    rabbit_url: str | None = None,
) -> CodesetVersion:
    """Ingest a single code-set version description from a JSON file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ingest_version(
        registry,
        codeset=data["codeset"],
        version_label=data["version_label"],
        values=data.get("values", []),
        source_effective_date=data.get("source_effective_date"),
        publication_date=data.get("publication_date"),
        implementation_date=data.get("implementation_date"),
        complete=data.get("complete"),
        expected_checksum=data.get("checksum"),
        publish=publish,
        rabbit_url=rabbit_url,
    )


def available_version_files() -> list[Path]:
    if not _VERSIONS_DIR.exists():
        return []
    return sorted(_VERSIONS_DIR.glob("*.json"))


def load_seed_versions(
    registry: CodesetRegistry | None = None,
    *,
    files: Iterable[Path] | None = None,
) -> CodesetRegistry:
    """Load every bundled seed version into ``registry`` (created if needed)."""
    registry = registry or CodesetRegistry()
    for path in files if files is not None else available_version_files():
        try:
            ingest_version_file(registry, path)
        except Exception:  # pragma: no cover - defensive against a bad seed file
            logger.exception("Failed to load seed code-set version %s", path)
    return registry
