# Changelog

All notable, repo-wide changes are recorded here. Entries are grouped by
date (`YYYY-MM`) and ordered most-recent first. Implementation detail
sits in the linked commits and the files referenced; this changelog is a
human index.

## 2026-06 — CI/CD bootstrap + security baseline

### Added
- GitHub Actions workflow at `.github/workflows/ci.yml` with six jobs:
  `Lint & syntax`, `Python tests (3.11)`, `Python tests (3.12)`,
  `Go tests`, `Security scans`, and `Anti-hallucination checks`.
- Anti-hallucination helper scripts under `scripts/ci/`:
  `check-no-conflict-markers.sh`, `check-placeholders.sh`,
  `check-imports.py`, `check-doc-paths.py`.
- Dev-only Python pins in `requirements-dev.txt` (`pytest`, `pip-audit`).
- Gitleaks fingerprint allow-list at `.gitleaksignore` with per-finding
  justifications.
- Security audit-ignore register at `security/audit-ignores.md`.
- Branch protection on `main`: all six jobs are now required status
  checks (strict mode); force-pushes and deletions disabled.

### Changed
- `api/requirements.txt` and `worker_py/requirements.txt`:
  `python-dotenv` → `1.2.2`, `python-multipart` → `0.0.31`,
  `fastapi` → `0.118.2`, explicit `starlette==0.47.2` pin.
- `frontend_go/go.mod` and `rbac_proxy/go.mod`: toolchain pinned to
  `go1.25.11` (was `go1.23.0`).
- `tests/test_claimtrace.py`: `ClaimtraceWebsiteTests` now resolves
  frontend assets via a repo-relative `REPO_ROOT` instead of
  hard-coded `/app/triage/...` paths.
- `README.md`: removed a duplicated Docker Compose Quick Start block
  introduced during the Claimtrace merge.

### Removed
- Orphan top-level `pyx12` submodule gitlink in the tree. The real
  `pyx12` source lives at `worker_py/pyx12/`; the root gitlink had no
  `.gitmodules` URL and caused `actions/checkout@v4` to fail with
  `fatal: No url found for submodule path 'pyx12' in .gitmodules`.

### Security
- 6 Python CVEs fixed via the dependency bumps above
  (`python-dotenv`, `python-multipart`, `fastapi` / `starlette`).
- 20 Go stdlib CVEs (crypto/x509, crypto/tls, net/http, net/url,
  encoding/asn1, encoding/pem, net/textproto, net/http/httputil, os,
  net) fixed by the toolchain bump to `go1.25.11`.
- 2 starlette advisories deferred with documented revisit triggers in
  `security/audit-ignores.md` (`PYSEC-2026-161`, `GHSA-7f5h-v6xp-fcq8`)
  because the fixes are not reachable within FastAPI's current
  starlette upper bound.

### Known follow-ups
- GitHub Advanced Security is not enabled on this private repo, so
  SARIF uploads from gitleaks and Trivy are tolerated as non-blocking.
  The underlying scans still gate the build on findings. No workflow
  change is needed once GHAS is licensed.
- `ruff` runs as `continue-on-error: true`; flip to a hard gate once a
  `ruff.toml` is adopted.
- `enforce_admins` on the branch-protection rule is intentionally off;
  flip on once emergency admin overrides are no longer expected.
