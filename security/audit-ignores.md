# Security audit ignore list

This document records each vulnerability advisory that the Triage CI
`pip-audit` job ignores via `--ignore-vuln`. **No advisory should appear in
the workflow without a corresponding entry here.**

When a fix becomes available, remove the ignore from `.github/workflows/ci.yml`
and delete the entry here.

## Currently ignored

### `PYSEC-2026-161` — starlette

- Package: `starlette`
- Currently pinned: see `api/requirements.txt`
- Advisory: PYSEC-2026-161 (fixed in `starlette>=1.0.1`)
- Reason for ignore:
  - FastAPI's published version range caps `starlette<0.49.0`
    (see fastapi/fastapi PR #14077, merged 2025-09-16 and shipped
    in FastAPI 0.116.2). The patched starlette release is not yet
    reachable via a compatible FastAPI version.
  - The Triage API does not expose the affected feature directly;
    the risk surface is the FastAPI HTTP layer, which is the only
    starlette consumer in this repo.
- Revisit when: FastAPI publishes a release whose `starlette` upper
  bound includes `>=1.0.1`. At that point bump `fastapi` and
  `starlette` together, drop the ignore from the workflow, and remove
  this entry.

### `GHSA-7f5h-v6xp-fcq8` — starlette

- Package: `starlette`
- Currently pinned: see `api/requirements.txt`
- Advisory: GHSA-7f5h-v6xp-fcq8 / CVE-2025-62727
  (fixed in `starlette>=0.49.1`)
- Reason for ignore:
  - Same constraint as `PYSEC-2026-161`: FastAPI 0.118 caps
    `starlette<0.49.0`, so `0.49.1` is unreachable until FastAPI
    raises its upper bound.
- Revisit when: FastAPI publishes a release that supports
  `starlette>=0.49.1`. Bump `fastapi` and `starlette` together and
  remove this entry.

## How to add an entry

1. Confirm the advisory cannot be remediated by upgrading the affected
   dependency (or its parent) within the project's compatibility window.
2. Add `--ignore-vuln <ADVISORY_ID>` to the relevant `pip-audit`
   invocation in `.github/workflows/ci.yml` with a one-line comment
   pointing at this file.
3. Add a new `### <ADVISORY_ID> — <package>` section above following the
   template of the entry already documented here. Include the advisory
   ID, affected package, current pin, fix version, justification, and a
   concrete "revisit when" trigger.
