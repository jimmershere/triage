"""CLI helpers for inspecting worker translator availability."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from . import select_translator, translator_diagnostics


def _detect_format(text: str) -> str:
    sample = text.lstrip()
    head = sample[:3].upper()
    if head == "ISA":
        return "X12_837_or_other"
    if sample.startswith("UNB") or sample.startswith("UNH"):
        return "EDIFACT_ORDERS_or_other"
    return "UNKNOWN"


def _print_human(statuses: list[dict[str, Any]], detected_type: str, sample_path: Path | None) -> None:
    heading = f"Detected type: {detected_type}"
    if sample_path:
        heading += f" (from {sample_path})"
    print(heading)
    for info in statuses:
        name = info.get("name", "<unknown>")
        available = info.get("available")
        can_handle = info.get("can_handle")
        line = f"- {name}:"
        if available is not None:
            line += f" available={available}"
        if can_handle is not None:
            line += f" handles={can_handle}"
        error = info.get("error") or info.get("handles_error") or info.get("diagnostics_error")
        if error:
            line += f" error={error}"
        if info.get("fallback"):
            line += " (fallback)"
        print(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect TurboEDI worker translators")
    parser.add_argument("sample", nargs="?", type=Path, help="Optional file to inspect")
    parser.add_argument("--detected-type", dest="detected_type", help="Override detected file type")
    parser.add_argument("--json", action="store_true", help="Emit JSON diagnostics")
    parser.add_argument("--log-level", default="WARNING", help="Set logging level for diagnostics")
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))

    text = ""
    sample_path: Path | None = None
    if args.sample:
        sample_path = args.sample
        text = sample_path.read_text(encoding="utf-8", errors="ignore")

    detected_type = args.detected_type or _detect_format(text)
    statuses = translator_diagnostics(detected_type, text)

    if args.json:
        payload = {
            "detected_type": detected_type,
            "sample": str(sample_path) if sample_path else None,
            "translators": statuses,
        }
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _print_human(statuses, detected_type, sample_path)
        chosen = select_translator(detected_type, text)
        if chosen:
            print(f"Selected translator: {getattr(chosen, 'name', chosen.__class__.__name__)}")
        else:
            print("No translator matched the sample")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
