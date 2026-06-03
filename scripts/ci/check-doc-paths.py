#!/usr/bin/env python3
"""Anti-hallucination check for documentation cross-references.

Scans ``README.md`` and every ``docs/*.md`` for inline-code references that look
like repo-relative paths (e.g. ``api/claimtrace_routes.py``, ``scripts/run_e2e.sh``)
and fails if any of them do not exist on disk.

AI-generated documentation frequently fabricates plausible-sounding file paths
that never existed. Verifying them at CI time keeps the README honest.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# File extensions we treat as evidence that an inline code span is referring to
# a real file. Anything else is more likely a module path, a sentence fragment,
# or a docs-only example, and we let it through.
KNOWN_EXTS = {
    ".py", ".sh", ".md", ".yml", ".yaml", ".toml", ".ini", ".cfg",
    ".json", ".sql", ".cypher", ".html", ".css", ".js", ".ts", ".tsx",
    ".go", ".mod", ".sum", ".conf", ".sample", ".example", ".x12",
    ".edi", ".txt", ".svg", ".png", ".jpg", ".jpeg", ".pdf", ".tpl",
    ".env", ".log", ".pem", ".crt", ".key", ".dockerfile",
}

# Paths that documentation legitimately refers to even though they are not
# present on every checkout (e.g. gitignored, licensed/optional bundles).
# Anything matched by ``git check-ignore`` is also auto-skipped at runtime.
DOC_ONLY_PATHS = {
    # Optional research bundle referenced in README's CMS authenticity
    # harness section; not redistributed in this repository.
    "docs/turbohedi/x222-005010/",
}


def _is_gitignored(rel: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", "--", rel],
            cwd=REPO_ROOT,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0

# Files we inspect for path-like inline code references.
DOC_FILES = [
    REPO_ROOT / "README.md",
    *(REPO_ROOT / "docs").glob("*.md"),
]

# Inline code spans: `something`. We only consider spans that look like
# repository-relative paths (contain a slash, no whitespace) and ignore URLs.
PATH_PATTERN = re.compile(r"`([^`\n]+)`")

# Allow-list of substrings inside an inline-code span that disqualify it from
# being treated as a repo-relative path (URLs, command examples, env values,
# directory placeholders, etc.).
DISQUALIFIERS = (
    "://",
    " ",
    "$",
    "{",
    "<",
    ">",
    "*",
    "@",
    "=",
    "${",
)


def _looks_like_repo_path(token: str) -> bool:
    if "/" not in token:
        return False
    if token.startswith(("/", "http", "ftp", "mailto:", "git@")):
        return False
    if any(bad in token for bad in DISQUALIFIERS):
        return False
    candidate = token.rstrip(",.;:)")
    # Treat trailing-slash references as directory paths.
    if candidate.endswith("/"):
        return True
    # Otherwise require a recognised file extension. This filters out dotted
    # module-style references such as ``swarms/scrubbing_swarm._AGENTS``
    # which contain a dot but no real extension.
    ext = Path(candidate).suffix.lower()
    return ext in KNOWN_EXTS


def main() -> int:
    missing: list[tuple[Path, str]] = []
    inspected = 0
    for doc in DOC_FILES:
        if not doc.is_file():
            continue
        text = doc.read_text(encoding="utf-8")
        for match in PATH_PATTERN.findall(text):
            if not _looks_like_repo_path(match):
                continue
            inspected += 1
            candidate = match.rstrip(",.;:)")
            if candidate in DOC_ONLY_PATHS:
                continue
            target = REPO_ROOT / candidate
            if target.exists():
                continue
            # Allow documentation to refer to paths that are intentionally
            # gitignored (generated fixtures, runtime artifacts, etc.).
            if _is_gitignored(candidate):
                continue
            missing.append((doc.relative_to(REPO_ROOT), candidate))

    print(f"[doc-paths] inspected={inspected} missing={len(missing)}")
    if missing:
        print("[doc-paths] referenced paths that do not exist:", file=sys.stderr)
        for doc, candidate in missing:
            print(f"  - {doc}: `{candidate}`", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
