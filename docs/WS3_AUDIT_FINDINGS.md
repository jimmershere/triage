# Workstream 3 — API/Logging Audit Verification (Findings)

Scope: review the FastAPI `api/` service and the Python worker for HIPAA
§164.312(b) audit controls — PHI-safe logging, an end-to-end correlation id
linked to the Claimtrace lineage id, six-year retention, and RBAC on log/audit
access. Where gaps existed, a small, dependency-free structured-logging helper
plus correlation middleware/propagation were added. This note records what was
found, what changed, and what remains.

## Summary

| Area | Status | Notes |
| --- | --- | --- |
| PHI-safe logging | **Implemented** | Structured JSON logs scrub direct identifiers; only the request *path* (never query strings) is logged. |
| Correlation id (api→worker→claimtrace) | **Implemented** | Single id = job id = Claimtrace lineage trace id, propagated across the RabbitMQ hop. |
| Tamper-evident substrate | **Confirmed (pre-existing)** | Claimtrace append-only journal + Merkle proofs; DB triggers reject UPDATE/DELETE on `claimtrace_event`. |
| Six-year retention | **Confirmed + codified** | `AUDIT_RETENTION_YEARS = 6` is the single source of truth; no automated purge exists (correct — see gaps). |
| RBAC on log/audit access | **Confirmed (pre-existing)** | `frontend_go` reverse proxy injects `X-TRIAGE-SECRET` server-side, strips client cookies, and gates writes via OAuth/RBAC groups. Not modified. |

## What changed

* `claimtrace/audit/logging.py` (new) — PHI-safe structured JSON log formatter,
  redaction (`redact_text`), structured-field scrubbing (`scrub_fields`),
  non-reversible identifier hashing (`hash_identifier`), a correlation-id
  context var, and `configure_structured_logging`/`log_event` helpers. Standard
  library only, so the API service can import it without new dependencies. The
  retention window constant `AUDIT_RETENTION_YEARS = 6` lives here.
* `worker_py/audit_log.py` (new) — a self-contained mirror of the same contract
  for the worker, whose deployment image does not ship `claimtrace`. Both emit
  an identical JSON schema and use the same `X-Correlation-ID` header.
* `api/app.py` — installs structured logging at startup, adds an HTTP
  correlation-id middleware (reads/echoes `X-Correlation-ID`, logs request
  path + status only), and binds the correlation id to the job id on ingest so
  it equals the Claimtrace lineage trace id. The id is added to the RabbitMQ
  message body and AMQP properties/headers.
* `worker_py/worker.py` — installs structured logging, reads the correlation id
  from the message body/AMQP properties (falling back to the job id), binds it
  for the lifetime of message processing, emits structured receive/processed
  events, and re-propagates the id onto the published ack.

## PHI-safety review

Audit/log records carry **identifiers and hashes only**. Findings:

* **Query strings:** the API's read endpoints accept identifiers
  (`uploaded_by`, `trading_partner_id`, claim id / hash for Claimtrace search) —
  not clinical content. The correlation middleware logs `request.url.path`
  **without** the query string, eliminating the "PHI in query strings" risk
  even if a caller appends one.
* **Error messages:** free-text messages pass through `redact_text`, which masks
  SSN, email, phone, and 13–19 digit runs (PAN/long member ids) and bounds field
  length to cap accidental clinical-content leakage. Structured fields pass
  through `scrub_fields` (recursive).
* **Existing call sites:** the worker previously logged raw exception strings
  (e.g. harness errors). These now flow through the redacting formatter, so any
  identifier-shaped substring is masked before it reaches stdout.

## Correlation-id coverage

The correlation id equals the **job id**, which Claimtrace already uses as the
lineage `trace_id` (see `api/claimtrace_service.py`, `record_ingested_file`).
Coverage:

1. **API request** → middleware binds an id (inbound `X-Correlation-ID` or new).
2. **Ingest** → rebinds the id to the job id and stamps it on the RabbitMQ
   payload + AMQP properties.
3. **Worker** → reads it back off the message (body or AMQP header), binds it
   for processing, and re-stamps it on the published ack (next RMQ hop).
4. **Claimtrace journal** → events are keyed by the same job id / lineage id and
   stored in the append-only `claimtrace_event` table with Merkle batch proofs.

This closes the "correlation-id gaps across RabbitMQ hops" risk from the plan.

## Retention & RBAC status

* **Retention:** HIPAA requires audit logs be retained ≥ 6 years. The
  authoritative trail is the append-only Claimtrace journal; `migrations/001_journal.sql`
  installs triggers that reject `UPDATE`/`DELETE` on `claimtrace_event`, giving
  WORM-like immutability. `AUDIT_RETENTION_YEARS = 6` codifies the window for any
  future purge/retention tooling.
* **RBAC:** confirmed (and intentionally not modified). `frontend_go/main.go`
  injects `X-TRIAGE-SECRET` server-side on proxied API calls, deletes inbound
  client cookies before proxying, enforces OAuth/RBAC group checks
  (`frontend_go/rbac/rbac.go`: `RequireSubmitterForWrite`, `RequireAdmin`), and
  sets security headers (`X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Content-Security-Policy`).

## Remaining gaps / recommendations

* **Names in filenames.** Redaction masks structured identifier patterns but
  cannot reliably detect free-text patient names embedded in uploaded
  filenames. Recommend hashing or normalizing filenames before logging if
  partners are known to embed names.
* **No automated purge.** There is intentionally no job that deletes audit data
  at 6 years; deleting on a timer risks premature loss. Retention should be an
  operational/backup policy that *guarantees ≥ 6 years*, with purge gated behind
  explicit review. `AUDIT_RETENTION_YEARS` is provided as the single knob.
* **Log-sink RBAC.** Application-level RBAC is confirmed; if structured logs are
  shipped to an external aggregator, that sink must enforce equivalent access
  controls and ≥ 6-year retention.
* **Centralized adoption.** The formatter wraps all existing `logger.*` calls, so
  every line is now JSON + correlation id. Converting the highest-signal call
  sites to explicit `log_event(...)` calls (done for ingest/worker hops) can be
  extended incrementally to acks and rejection-report emission.
