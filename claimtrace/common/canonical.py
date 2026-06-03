"""Canonicalization helpers for stable claim identity and hashing."""

from __future__ import annotations

import datetime as _dt
import json
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, Mapping

try:  # pragma: no cover - exercised when optional dependency is available
    import orjson
except Exception:  # pragma: no cover - stdlib fallback is covered instead
    orjson = None  # type: ignore

from .hashing import sha256_hex


def money_to_cents(value: Any) -> int:
    """Normalize a money value to integer cents without binary-float drift."""

    if isinstance(value, int):
        return value
    decimal_value = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(decimal_value * 100)


def _normalize(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        return {
            str(key).strip().upper(): _normalize(value)
            for key, value in sorted(obj.items(), key=lambda item: str(item[0]).strip().upper())
        }
    if isinstance(obj, (list, tuple)):
        return [_normalize(value) for value in obj]
    if isinstance(obj, (_dt.datetime, _dt.date)):
        return obj.date().isoformat() if isinstance(obj, _dt.datetime) else obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            raise ValueError("canonical_json does not allow NaN or Infinity")
        return str(Decimal(str(obj)).normalize())
    if isinstance(obj, str):
        return obj.strip().upper()
    return obj


def canonical_json(obj: Any) -> bytes:
    """Return stable UTF-8 JSON bytes with sorted keys and normalized values."""

    normalized = _normalize(obj)
    if orjson is not None:  # pragma: no cover - depends on environment
        return orjson.dumps(normalized, option=orjson.OPT_SORT_KEYS)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def line_items_signature(lines: Iterable[Mapping[str, Any]]) -> str:
    """Hash line items sorted by procedure code, DOS, and units."""

    normalized_lines = []
    for line in lines:
        normalized_lines.append(
            {
                "proc_code": str(line.get("proc_code", "")).strip().upper(),
                "dos": _normalize(line.get("dos", "")),
                "units": int(line.get("units", 0)),
                "charge_amount_cents": (
                    money_to_cents(line["charge_amount"])
                    if "charge_amount" in line and "charge_amount_cents" not in line
                    else int(line.get("charge_amount_cents", 0))
                ),
            }
        )
    sorted_lines = sorted(
        normalized_lines,
        key=lambda item: (item["proc_code"], item["dos"], item["units"]),
    )
    return sha256_hex(canonical_json(sorted_lines))
