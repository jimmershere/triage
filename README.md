# TurboEDI Starter (Fast Ingest + Minimal Parsing)

A fast, pragmatic starter kit for building an **EDI ingestion and parsing pipeline** with **Python 3.10**, **FastAPI**, **RabbitMQ**, and **PostgreSQL**. It includes:

- **/api**: FastAPI service to ingest files (POST `/ingest`) and push to RabbitMQ.
- **/worker_py**: Python consumer that:
  - Detects **X12 837** vs **EDIFACT ORDERS**
  - Minimally parses and inserts metadata into Postgres
  - Generates a simple acknowledgment record (e.g., "999-like" for X12, "CONTRL-like" for EDIFACT) in the `acks` table
- **/db/init.sql**: Minimal relational schema
- **/samples**: Example X12 837 and EDIFACT ORDERS files
- **/bench**: Quick local benchmark harness

> ⚠️ This is a starter kit for rapid iteration, not a full validator. Swap in **bots** (install separately) / **PyX12** / production mappers as you grow.

## Quick Start

1. **Prereqs**: Docker & Docker Compose installed.
2. Copy `.env.example` to `.env` and adjust if needed:
   ```bash
   cp .env.example .env
   - `RABBITMQ_URL` should match the credentials you configure for RabbitMQ (defaults map to the compose file).
   - `RMQ_QUEUE` / `RMQ_ACKS_QUEUE` let you rename the ingest and acknowledgement queues.
   - `HEDI_API_BASE` and `HEDI_SESSION_SECRET` drive the Go frontend's dynamic `config.js` and session signing.
   ```
3. **Boot services**:
   ```bash
   docker compose up --build
   ```
   - Postgres: `localhost:5432` (db: `edi`, user: `edi`, pass: `edi`)
   - RabbitMQ mgmt UI: http://localhost:15672 (guest/guest)
   - API: http://localhost:8000/docs
4. **Ingest a file** (replace path as needed):
   ```bash
   curl -X POST "http://localhost:8000/ingest"      -F "file=@samples/x12_837_small.txt"
   # curl -X POST "http://localhost:8000/ingest"      -F "file=@samples/x12_837_large_valid.x12"  # ~500 KB multi-claim sample
   ```
5. **See results**:
   - Check `imports`, `claims`, `order_lines`, and `acks` tables in Postgres.
   - RabbitMQ queues: `ingest` (uploads) and `acks` (generated acknowledgments).

## Customer support assistant configuration

The web UI now ships with the “Trish” customer advocate, complete with helpful callouts and an in-app chat assistant. The chat widget can raise trouble tickets by generating unique request IDs and preparing `mailto:`/`sms:` links.

- Update the default contact points in [`frontend_go/public/static/js/support_config.js`](frontend_go/public/static/js/support_config.js) (and the mirrored file under `_container_public/static/js/`) to wire in your production support mailbox or SMS gateway.
- The same configuration is reused across every page, so a single change covers the Claims Portal, Processed Files, HEDI Mapping, Claim Entry, and the login/admin surfaces.
- Messages that include words like “error” or “trouble” automatically produce a ticket reference in the chat transcript so agents can track the conversation against your downstream systems.

## Applying upstream patches

Occasionally we share follow-up fixes as standalone patch files (for example `worker.patch`).
If `git apply` reports that a patch does not cleanly apply, use the following workflow
to merge it safely:

1. **Preview the patch**
   ```bash
   git apply --stat worker.patch
   git apply --check worker.patch
   ```
   The `--check` run performs a dry-run and points out any conflicting hunks without
   touching your working tree.

2. **Retry with a 3-way merge**
   ```bash
   git apply --3way worker.patch
   ```
   Git will attempt to merge the patch against your current files even when the context
   has drifted. If it still cannot reconcile a hunk, Git will leave `.rej` files next to
   the affected sources so you can inspect the conflicts manually.

3. **Manually resolve remaining rejects**
   Open each `.rej` alongside the target file, apply the intended changes, and remove the
   reject file once finished. You can also copy the updated source directly from the pull
   request preview if that is easier.

4. **Stage and commit**
   ```bash
   git add worker_py/worker.py
   git commit -m "Apply worker patch"
   ```
   Commit after you have reviewed the merged changes so that your history stays clean.

These steps provide a safe fallback whenever an upstream patch targets an older commit or
when local modifications cause context mismatches.

## Design Goals

- **Speed-first path**: small, composable services; switch parsers without changing I/O.
- **Extensible**: add real mappers/validators later, or a Rust/C++ parser microservice.
- **Observability**: simple, structured logs to evolve into metrics.

## Replace the Minimal Parsers

- X12: swap the `parse_x12_837` in `worker_py/worker.py` with **PyX12** or your mapping engine.
- EDIFACT: replace `parse_edifact_orders` with **bots** (install separately) mapping + validation and CONTRL generation.

### Enabling the optional `bots` translator

The worker auto-detects whether the [bots EDI translator](https://github.com/bots-edi/bots) is installed. When present it
activates the `bots-edifact` adapter so EDIFACT payloads are parsed and acknowledgements are generated with the library
instead of the fallback parser.

Install it in whatever environment builds/runs the worker:

```bash
# Local virtualenv or dev shell
pip install "bots==3.2.0"

# Container image (add after the existing requirements step)
RUN pip install --no-cache-dir bots==3.2.0
```

If you prefer to keep the base starter image unchanged, you can create a thin derivative Dockerfile, for example:

```Dockerfile
FROM turbohedi-02_worker_py
RUN pip install --no-cache-dir bots==3.2.0
```

Rebuild the worker after installing the dependency. When the service boots it logs whether the optional translator was
found so you can confirm the package is available.

### Diagnosing which translator is active

The worker registers every available translator at import time. The `simple-x12` entry shown in the logs is the
lightweight fallback shipped with TurboEDI; it activates when the richer [`pyx12`](https://github.com/azoner/pyx12) stack
is missing. To inspect the current environment run the translator helper locally or inside the worker container:

```bash
python -m worker_py.translators               # lists translators and which formats they handle
python -m worker_py.translators samples/837.edi
```

The command prints whether `pyx12-x12` is available, why it might be disabled, and which translator would process the
sample payload. Use `--json` for machine-readable diagnostics or `--log-level INFO` to surface import failures. Once
`pyx12` imports successfully you will see `Selected translator: pyx12-x12` and the worker logs will swap from
`simple-x12` to `pyx12-x12` when handling X12 claims.

## Security Notes

- This sample accepts files and publishes raw bytes to the queue for simplicity.
- For production, store payloads in object storage and enqueue **pointers** (URLs/keys) plus checksums.
- Add size limits, authn/z (e.g., OAuth2/Keycloak), and audit logging.

## License

MIT
