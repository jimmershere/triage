# Legacy bots-edi X12 Add-ons

This document is archival. It describes the older v0.2 `bots-edi` add-on bundle for 835, 270/271, and 276/277 workflows. The current Triage repository uses the native Python validation, scrubbing, FHIR, and routing modules under `worker_py/` plus `/turbo/*` API routes. Use the root `README.md` for the supported setup path.

## What this legacy bundle contained

- `bots` grammars for 835, 270, 271, 276, and 277 transactions.
- Lightweight mappings for 835, 271, and 277 database inserts.
- JSON-driven 270 and 276 builder examples.
- SQL DDL for legacy add-on tables and partner profiles.

## If you still need the legacy bots path

1. Install `bots` in the worker/runtime environment:

```bash
pip install "bots==3.2.0"
```

2. Merge any legacy `bots/config/routes_x12_addons.ini` content into your bots runtime configuration.

3. Apply the legacy SQL only if those files are present in your deployment package:

```bash
psql "$TRIAGE_PG_DSN" -f db/triage_x12_addons.sql
psql "$TRIAGE_PG_DSN" -f db/triage_partner_profiles.sql
```

For Docker Compose users, the host PostgreSQL port is usually `15432`, so a local DSN looks like:

```bash
export TRIAGE_PG_DSN='postgresql://edi:edi@localhost:15432/edi'
```

## Legacy queue names

- Inbound: `x12.835.in`, `x12.271.in`, `x12.277.in`
- Outbound build: `x12.270.out`, `x12.276.out`

## Example builder payloads

270 minimum payload:

```json
{"target_st":"270","partner_key":"default","subscriber_id":"S12345","dos":"20250120"}
```

276 payload:

```json
{"target_st":"276","partner_key":"default","subscriber_id":"S12345","claim_id":"C-001","dos":"20250120"}
```

## Current recommendation

Prefer the native engines documented in the root README unless you specifically maintain a bots-based deployment. The native path is covered by `bash scripts/run-tests.sh` and the FastAPI `/turbo/*` routes.
