"""Lazy loader for the bundled CMS edit tables.

Edit tables live as JSON under ``data/``. They are curated, representative
subsets — production deployments should replace them with the full CMS files
(NCCI PTP/MUE quarterly releases, payer LCD policies, a live eligibility feed).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent / "data"
_CACHE: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def load_table(name: str) -> dict[str, Any]:
    """Load (and cache) a bundled edit table by name.

    A missing file yields an empty dict so an edit degrades to a no-op rather
    than crashing.
    """
    with _LOCK:
        if name in _CACHE:
            return _CACHE[name]
        path = _DATA_DIR / f"{name}.json"
        if not path.exists():
            _CACHE[name] = {}
        else:
            _CACHE[name] = json.loads(path.read_text(encoding="utf-8"))
        return _CACHE[name]


def available_tables() -> list[str]:
    if not _DATA_DIR.exists():
        return []
    return sorted(p.stem for p in _DATA_DIR.glob("*.json"))
