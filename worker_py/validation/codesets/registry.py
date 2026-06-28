"""Hot-loadable, effective-dated external code-set registry (Workstream 2).

External code sets (CARC/RARC, ICD-10-CM/PCS, taxonomy, ...) are published on
wildly different cadences and each value is valid only within an effective
window keyed to the claim's *service / discharge date*. This registry models
that explicitly:

* :class:`CodesetValue`   — one code with its ``valid_from`` / ``valid_to``
  window and active/deactivated status.
* :class:`CodesetVersion` — a dated snapshot of a code set (version label,
  source-effective / publication / implementation dates, ingest timestamp,
  checksum, and the value table).
* :class:`CodesetRegistry`— holds every version of every code set and resolves
  ``is_valid(code, codeset, as_of_date, transaction_type)`` by selecting the
  version whose effective window contains ``as_of_date``.

The registry is *advisory / validation-only* — it answers "is this code valid,
where and when" and never rewrites a code on a claim. An in-process LRU cache
keyed by ``(codeset, version, code, as_of)`` is invalidated whenever a new
version is registered (the cache generation is bumped), so a hot reload takes
effect without a redeploy.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    # Accept both CCYY-MM-DD and X12 D8 (CCYYMMDD).
    if len(text) == 8 and text.isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    return date.fromisoformat(text[:10])


@dataclass
class CodesetValue:
    """One code value within a code-set version, with its effective window."""

    code: str
    description: str = ""
    valid_from: date | None = None
    valid_to: date | None = None
    status: str = "active"  # "active" | "deactivated"

    def is_active_on(self, as_of: date | None) -> bool:
        """True when this value's effective window contains ``as_of``.

        A deactivated code remains valid for service dates on/before its
        ``valid_to`` (deactivated-but-historically-valid) — only dates strictly
        after the window are rejected.
        """
        if as_of is None:
            return self.status != "deactivated"
        if self.valid_from and as_of < self.valid_from:
            return False
        if self.valid_to and as_of > self.valid_to:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "description": self.description,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "status": self.status,
        }


@dataclass
class CodesetVersion:
    """A dated snapshot of one external code set."""

    codeset: str
    version_label: str
    source_effective_date: date | None = None
    publication_date: date | None = None
    implementation_date: date | None = None
    ingest_ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    checksum: str = ""
    complete: bool = True
    values: dict[str, CodesetValue] = field(default_factory=dict)

    @staticmethod
    def _norm(code: str | None) -> str:
        return (code or "").strip().upper()

    def get(self, code: str | None) -> CodesetValue | None:
        return self.values.get(self._norm(code))

    def is_valid(self, code: str | None, as_of: date | None) -> bool:
        value = self.get(code)
        if value is None:
            return False
        return value.is_active_on(as_of)

    def to_dict(self) -> dict[str, Any]:
        return {
            "codeset": self.codeset,
            "version_label": self.version_label,
            "source_effective_date": (
                self.source_effective_date.isoformat()
                if self.source_effective_date
                else None
            ),
            "publication_date": (
                self.publication_date.isoformat() if self.publication_date else None
            ),
            "implementation_date": (
                self.implementation_date.isoformat() if self.implementation_date else None
            ),
            "ingest_ts": self.ingest_ts.isoformat(),
            "checksum": self.checksum,
            "complete": self.complete,
            "value_count": len(self.values),
        }


@dataclass
class ResolveResult:
    """Outcome of resolving one code against the registry."""

    valid: bool
    codeset: str
    known_codeset: bool
    version_label: str | None = None
    status: str | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _build_values(raw_values: Any) -> tuple[dict[str, CodesetValue], bool]:
    """Normalize a value table from either a list[dict] or a {code: desc} map."""
    values: dict[str, CodesetValue] = {}
    if isinstance(raw_values, dict):
        for code, desc in raw_values.items():
            norm = CodesetVersion._norm(code)
            values[norm] = CodesetValue(code=norm, description=str(desc))
        return values, True
    for entry in raw_values or []:
        code = CodesetVersion._norm(entry.get("code"))
        if not code:
            continue
        values[code] = CodesetValue(
            code=code,
            description=str(entry.get("description", "")),
            valid_from=_as_date(entry.get("valid_from")),
            valid_to=_as_date(entry.get("valid_to")),
            status=str(entry.get("status", "active")),
        )
    return values, False


def make_version(
    *,
    codeset: str,
    version_label: str,
    values: Any,
    source_effective_date: Any = None,
    publication_date: Any = None,
    implementation_date: Any = None,
    checksum: str = "",
    complete: bool | None = None,
) -> CodesetVersion:
    """Construct a :class:`CodesetVersion` from loosely-typed inputs."""
    parsed, from_map = _build_values(values)
    return CodesetVersion(
        codeset=codeset,
        version_label=version_label,
        source_effective_date=_as_date(source_effective_date),
        publication_date=_as_date(publication_date),
        implementation_date=_as_date(implementation_date),
        checksum=checksum,
        complete=from_map if complete is None else complete,
        values=parsed,
    )


class CodesetRegistry:
    """Thread-safe registry of effective-dated code-set versions."""

    def __init__(self, *, cache_size: int = 4096) -> None:
        self._versions: dict[str, list[CodesetVersion]] = {}
        self._lock = threading.RLock()
        self._generation = 0
        self._cache: "OrderedDict[tuple, bool]" = OrderedDict()
        self._cache_size = cache_size

    # -- registration / hot reload -----------------------------------------

    def register(self, version: CodesetVersion) -> None:
        """Register (or replace) a code-set version and invalidate the cache."""
        with self._lock:
            key = version.codeset.strip().lower()
            bucket = self._versions.setdefault(key, [])
            bucket[:] = [v for v in bucket if v.version_label != version.version_label]
            bucket.append(version)
            bucket.sort(
                key=lambda v: (v.source_effective_date or date.min, v.version_label)
            )
            self._generation += 1
            self._cache.clear()

    @property
    def generation(self) -> int:
        return self._generation

    def codesets(self) -> list[str]:
        with self._lock:
            return sorted(self._versions)

    def versions(self, codeset: str) -> list[CodesetVersion]:
        with self._lock:
            return list(self._versions.get(codeset.strip().lower(), []))

    def has(self, codeset: str) -> bool:
        with self._lock:
            return bool(self._versions.get(codeset.strip().lower()))

    # -- resolution --------------------------------------------------------

    def resolve_version(
        self, codeset: str, as_of: date | None
    ) -> CodesetVersion | None:
        """Select the version whose effective window contains ``as_of``.

        Picks the latest version with ``source_effective_date <= as_of``; if
        every version is dated after ``as_of`` (or ``as_of`` is unknown) the
        earliest / only version is used.
        """
        bucket = self.versions(codeset)
        if not bucket:
            return None
        if as_of is None:
            return bucket[-1]
        applicable = [
            v for v in bucket if v.source_effective_date and v.source_effective_date <= as_of
        ]
        if applicable:
            return applicable[-1]
        # Versions without an effective date act as an always-applicable fallback.
        undated = [v for v in bucket if v.source_effective_date is None]
        if undated:
            return undated[-1]
        return bucket[0]

    def resolve(
        self,
        code: str | None,
        codeset: str,
        as_of: date | None = None,
        transaction_type: str | None = None,
    ) -> ResolveResult:
        as_of = _as_date(as_of) if not isinstance(as_of, date) else as_of
        version = self.resolve_version(codeset, as_of)
        if version is None:
            return ResolveResult(
                valid=True,
                codeset=codeset,
                known_codeset=False,
                reason="code set not loaded; deferring to format validation",
            )
        value = version.get(code)
        if value is None:
            return ResolveResult(
                valid=False,
                codeset=codeset,
                known_codeset=True,
                version_label=version.version_label,
                reason=f"code not present in {codeset} version {version.version_label}",
            )
        active = value.is_active_on(as_of)
        if active:
            reason = "valid"
        elif value.valid_from and as_of and as_of < value.valid_from:
            reason = (
                f"code not yet effective (effective {value.valid_from.isoformat()})"
            )
        else:
            reason = "code deactivated for the service date"
        return ResolveResult(
            valid=active,
            codeset=codeset,
            known_codeset=True,
            version_label=version.version_label,
            status=value.status,
            reason=reason,
        )

    def is_valid(
        self,
        code: str | None,
        codeset: str,
        as_of_date: date | None = None,
        transaction_type: str | None = None,
    ) -> bool:
        """Cached membership check honouring the effective-dated window.

        When the code set is not loaded this returns ``True`` so callers fall
        back to structural/format validation rather than false-rejecting.
        """
        as_of = _as_date(as_of_date) if not isinstance(as_of_date, date) else as_of_date
        norm = (code or "").strip().upper()
        cache_key = (self._generation, codeset.strip().lower(), norm,
                     as_of.isoformat() if as_of else "", transaction_type or "")
        with self._lock:
            hit = self._cache.get(cache_key)
            if hit is not None:
                self._cache.move_to_end(cache_key)
                return hit
        result = self.resolve(code, codeset, as_of, transaction_type).valid
        with self._lock:
            self._cache[cache_key] = result
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return result

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "generation": self._generation,
                "codesets": {
                    name: [v.to_dict() for v in bucket]
                    for name, bucket in sorted(self._versions.items())
                },
            }


# ---------------------------------------------------------------------------
# Process-global active registry (consumed by SNIP type 5 and CMS scrubbing)
# ---------------------------------------------------------------------------

_ACTIVE_REGISTRY: Optional[CodesetRegistry] = None
_ACTIVE_LOCK = threading.Lock()


def get_active_registry() -> CodesetRegistry | None:
    """Return the process-wide active registry, or ``None`` if none is set."""
    return _ACTIVE_REGISTRY


def set_active_registry(registry: CodesetRegistry | None) -> None:
    """Install (or clear) the process-wide active registry."""
    global _ACTIVE_REGISTRY
    with _ACTIVE_LOCK:
        _ACTIVE_REGISTRY = registry
