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

### `PYSEC-2026-248`, `PYSEC-2026-249`, `GHSA-wqp7-x3pw-xc5r`, `GHSA-x746-7m8f-x49c` — UNVERIFIED, pending triage

- Package: **unverified.** These four advisories are passed to
  `--ignore-vuln` in `.github/workflows/ci.yml`'s `api/requirements.txt`
  audit but had no corresponding entry here, violating the invariant
  stated at the top of this file ("No advisory should appear in the
  workflow without a corresponding entry").
- Status: their affected package, fixed version, and whether they are
  remediable could not be verified in the offline build environment
  (`pip-audit` requires network access to the advisory database).
- Reason for ignore: **not yet established.** Recorded here to make this
  register complete and the suppression visible rather than silent.
- Revisit when: **immediately / before the next release.** Run
  `pip-audit -r api/requirements.txt` with network access, identify the
  package and fix version for each ID, then either (a) bump the affected
  dependency and remove the `--ignore-vuln` flag, or (b) replace this
  block with a proper per-advisory justification + concrete revisit
  trigger following the template below.

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
