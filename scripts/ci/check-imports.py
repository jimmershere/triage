#!/usr/bin/env python3
"""Anti-hallucination import probe.

Walks every tracked ``*.py`` module in the Triage worker, API, and Claimtrace
packages and imports it. Any module that fails to import (because of a
fabricated dependency name, a missing local file, or a syntax error that
compileall missed) will cause this script to exit non-zero.

This complements ``python -m compileall`` (syntax-only) and ``pip-audit``
(supply-chain) by exercising the import graph the same way the running
service would on startup.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Packages whose modules should be importable as ``package.module`` once the
# repo root is on ``sys.path``. Keep this in lockstep with the actual top-level
# packages in the repository.
PACKAGES = ("api", "claimtrace")

# Directories whose ``test_*.py`` files import sibling modules using a bare
# name (no package prefix). ``worker_py`` follows that pattern.
SCRIPT_DIRS = ("worker_py",)

# Modules we deliberately skip because they require network services,
# heavyweight optional dependencies that are not installed by default in CI,
# or are test modules that should only be exercised by their own runner.
SKIP_MODULES = {
    # The bots translator is an optional third-party EDIFACT package; we
    # don't install it in CI by default, so importing the adapter shim would
    # raise even though the code is valid.
    "worker_py.translators.edifact_bots",
}

# Sub-package prefixes that the import probe should skip. Kept empty by
# default now that requirements-dev.txt installs pytest and the test
# fixtures import cleanly; add prefixes here only when a module legitimately
# cannot be imported outside of its runtime context.
SKIP_PREFIXES: tuple[str, ...] = ()


def _iter_python_files(base: Path) -> list[Path]:
    files: list[Path] = []
    for path in base.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if "/__pycache__/" in rel or rel.endswith("/__pycache__"):
            continue
        # Skip generated / vendored copies.
        if rel.startswith("worker_py/.pytest_cache/"):
            continue
        files.append(path)
    return files


def _module_name_for(path: Path, *, as_script: bool) -> str:
    rel = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(rel.parts)
    if as_script:
        # Drop the leading package directory and import using the script-style
        # name so we mimic the way worker.py is invoked.
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _attempt_import(module_name: str) -> tuple[bool, str]:
    if not module_name:
        return True, "skip:empty"
    if module_name in SKIP_MODULES:
        return True, "skip:explicit"
    if any(module_name.startswith(prefix) for prefix in SKIP_PREFIXES):
        return True, "skip:test-module"
    try:
        importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 - we want every failure surfaced
        return False, f"{type(exc).__name__}: {exc}"
    return True, "ok"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print every module attempted, not just failures.",
    )
    args = parser.parse_args(argv)

    sys.path.insert(0, str(REPO_ROOT))

    failures: list[tuple[str, str]] = []
    attempted = 0

    # 1) Package-qualified imports (api.app, claimtrace.merkle.tree, ...).
    for pkg in PACKAGES:
        base = REPO_ROOT / pkg
        if not base.is_dir():
            continue
        for path in _iter_python_files(base):
            module = _module_name_for(path, as_script=False)
            attempted += 1
            ok, detail = _attempt_import(module)
            if args.verbose or not ok:
                print(f"[import-probe] {module}: {detail}")
            if not ok:
                failures.append((module, detail))

    # 2) Script-style imports (worker_py is invoked via plain ``python file.py``
    # so test modules import siblings without the ``worker_py.`` prefix).
    for script_dir in SCRIPT_DIRS:
        base = REPO_ROOT / script_dir
        if not base.is_dir():
            continue
        # Put the script dir directly on sys.path so bare imports work.
        sys.path.insert(0, str(base))
        try:
            for path in _iter_python_files(base):
                if any(part.startswith(".") for part in path.relative_to(base).parts):
                    continue
                module = _module_name_for(path, as_script=True)
                if not module:
                    continue
                attempted += 1
                ok, detail = _attempt_import(module)
                if args.verbose or not ok:
                    print(f"[import-probe] {script_dir}/{module}: {detail}")
                if not ok:
                    failures.append((f"{script_dir}/{module}", detail))
        finally:
            sys.path.remove(str(base))

    print(f"[import-probe] attempted={attempted} failed={len(failures)}")
    if failures:
        print("[import-probe] failing modules:", file=sys.stderr)
        for name, detail in failures:
            print(f"  - {name}: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
