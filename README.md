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

> ⚠️ This was originally a starter kit. The v0.3 line adds production-grade engines (validation, scrubbing, FHIR, supervised swarms) on top — see below.

## TurboHEDI v0.3 engines

These are pure-Python packages under `worker_py/` plus a thin HTTP surface in `api/turbo_routes.py` (mounted automatically at `/turbo`). Every engine is exercised by `bash scripts/run-tests.sh` (200+ tests, unittest, no network required).

- **`worker_py/validation/`** — WEDI **SNIP 1-7** validation engine covering
  837P/I/D, 835, 270/271, 276/277 and 278. Loop-aware parser, bundled code-set
  validators (POS, claim frequency, filing indicator, gender, CARC/RARC,
  claim-status; NPI Luhn; ICD-10/HCPCS/CPT format), and conformant TA1, 999
  and 277CA acknowledgment generators.
- **`worker_py/scrubbing/`** — CMS payment-edit engine: NCCI PTP unbundling,
  NCCI MUE units, NCD/LCD coverage, modifier validation, ICD-10 sequencing,
  age/gender demographics, duplicate detection, eligibility-on-DOS.
- **`worker_py/fhir/`** — native FHIR R4 with CARIN Blue Button and Da Vinci PAS
  profile references. Bidirectional X12 <-> FHIR mappers: 837 <-> Claim,
  835 -> ExplanationOfBenefit, 270/271 <-> CoverageEligibilityRequest/Response.
- **`worker_py/swarms/`** — supervised-swarm framework (parallel agents +
  supervisor verdict with confidence and audit trail) plus three workload-
  specific swarms: claim scrubbing, denial resolution / appeal generation, and
  FHIR-mapping audit. LLM transport is pluggable (Ollama by default, mocks for
  tests).
- **`worker_py/turbo_pipeline.py`** — one-call orchestrator that runs
  validate → scrub → (FHIR map) → (TA1/999/277CA) and returns a combined
  report.

### HTTP API surface (`/turbo/*`)

| Method & path | Purpose |
|---|---|
| `GET  /turbo/capability` | CapabilityStatement listing supported transactions, SNIP levels and IGs. |
| `POST /turbo/validate` | Run SNIP 1-7 validation over an X12 payload. |
| `POST /turbo/pipeline` | validate -> scrub -> (FHIR) -> (acks) in one call. |
| `POST /turbo/fhir/from-x12` | Convert an X12 837/835 into a FHIR Bundle. |
| `POST /turbo/fhir/Claim/$submit` | Accept a FHIR Claim, convert to 837 and validate. |

See [`docs/PATENT_EVIDENCE.md`](docs/PATENT_EVIDENCE.md) for the mapping
between the code and the patent strategy, and
[`docs/ROADMAP.md`](docs/ROADMAP.md) for the phased completion plan.

## Quick Start

1. **Prereqs**: Docker & Docker Compose installed.
2. Copy `.env.example` to `.env` and adjust if needed:
   ```bash
   cp .env.example .env
   - `RABBITMQ_URL` should match the credentials you configure for RabbitMQ (defaults map to the compose file).
   - `RMQ_QUEUE` / `RMQ_ACKS_QUEUE` let you rename the ingest and acknowledgement queues.
   - `TRIAGE_API_BASE` and `TRIAGE_SESSION_SECRET` drive the Go frontend's dynamic `config.js` and session signing.
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

## Enabling OAuth2/OIDC single sign-on

The frontend now expects an external identity provider (for example Keycloak) to handle
authentication via [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/). When the proxy is
running on your network, route the built-in `/oauth2/*` paths to it by setting
`TRIAGE_OAUTH2_PROXY_URL` before starting the `frontend_go` service:

```bash
TRIAGE_OAUTH2_PROXY_URL=https://oauth2-proxy.internal:4180 docker compose up frontend_go
```

If the upstream proxy uses a self-signed certificate, enable lenient TLS verification with
`TRIAGE_OAUTH2_PROXY_INSECURE_SKIP_VERIFY=true`. The flag only affects calls to the OAuth proxy; the
API reverse proxy continues to enforce normal certificate validation.

When the public OAuth URL differs from the network location the Go frontend can reach (for example
when the hostname only resolves inside your cluster), set `TRIAGE_OAUTH2_PROXY_INTERNAL_URL` to the
reachable origin. The frontend continues to advertise the external URL via `config.js`, while the
internal URL drives the reverse proxy connection from the container:

```bash
TRIAGE_OAUTH2_PROXY_URL=https://oauth2-proxy.example.com/oauth2 \
TRIAGE_OAUTH2_PROXY_INTERNAL_URL=http://rbac:4180/oauth2 \
docker compose up frontend_go
```

The helper endpoint at `/config.js` also publishes `window.TRIAGE_OAUTH2_START`, so you can point the
UI at a different login entrypoint if your deployment uses a non-standard path:

```bash
# Optional override if the proxy is mounted on a different prefix
TRIAGE_OAUTH2_START=/sso/start
```

Once configured, the login buttons on the claims portal and admin console redirect to the proxy
instead of returning a 404.

For the starter stack we ship a lightweight RBAC service that emulates the oauth2-proxy contract so
you can exercise the portal out-of-the-box. The `rbac` container listens on port `4180`, presents a
simple login form, authenticates users against the FastAPI backend, and issues signed session
cookies that the frontend validates on subsequent requests. Docker Compose automatically wires
`TRIAGE_OAUTH2_PROXY_URL` to `http://rbac:4180`, so `docker compose up --build` exposes both the
frontend (`8080/8443`) and the RBAC login endpoint (`4180`). Sign in with the bootstrap
administrator (`admin` / `3wm078uu`) or any user you add from the Admin → User Management screen,
and the portal/admin pages will render with the correct RBAC headers.

## Customer support assistant configuration

The web UI now ships with the “Trish” customer advocate, complete with helpful callouts and an in-app chat assistant. The chat widget can raise trouble tickets by generating unique request IDs and preparing `mailto:`/`sms:` links.

- Update the default contact points in [`frontend_go/public/static/js/support_config.js`](frontend_go/public/static/js/support_config.js) to wire in your production support mailbox or SMS gateway.
- The same configuration is reused across every page, so a single change covers the Claims Portal, Processed Files, Mapping Studio, Claim Entry, and the login/admin surfaces.
- Messages that include words like “error” or “trouble” automatically produce a ticket reference in the chat transcript so agents can track the conversation against your downstream systems. Trish now follows up to capture the severity (1–4) before logging each ticket.

## Canvas validation helpers

Triage’s visual mapper now ships with richer instrumentation so analysts can immediately understand why rows are highlighted:

- The XML-style wrapper row (`<transactionSet type="…">`) inspects the first `ST`/`GS` segments in the uploaded payload and updates itself to match the detected transaction variant (for example `837D` vs `837P`). This keeps the preview aligned with the real file type even when users mix dental, professional, or institutional claim templates.
- Any row that violates the lightweight X12 rules (invalid identifiers, unrecognised segment IDs, disallowed characters, or missing `~` terminators) gains a light-blue thought bubble. Clicking the bubble toggles a friendly panel featuring `trish-laptop.svg` and bullet points that spell out the exact violation so analysts can reconcile the raw X12 text with the highlights.
- Only one bubble is open at a time and re-clicking it closes the guidance, making it easy to step through each exception without losing your place in the canvas.

These helpers live in [`frontend_go/public/static/js/mapper.js`](frontend_go/public/static/js/mapper.js) with the styles in [`frontend_go/public/static/css/styles.css`](frontend_go/public/static/css/styles.css).

## Admin dashboard, tickets, and role wiring

- The admin portal renders active tickets raised by the chat assistant. Entries are stored in-browser under the `triageSupportTickets` key and surface the ID, submission timestamp, summary, and severity ranking.
- Stage users and assign application roles (view → update → create → admin) from the Admin → “User & role management” card. Accounts are written to the `app_users` table in PostgreSQL with PBKDF2-hashed credentials and per-portal access flags.
- A bootstrap administrator account is provisioned during API startup. By default the username is `admin` and the stored hash corresponds to the password `3wm078uu`. Override `TRIAGE_BOOTSTRAP_ADMIN_USER` and/or `TRIAGE_BOOTSTRAP_ADMIN_HASH` (a PBKDF2 string) to rotate these credentials before first run.
- OpenLDAP is bundled in the compose stack for directory-backed authentication. The container exposes `ldap://localhost:389` with the base DN `dc=example,dc=com` and an administrative bind account `cn=admin,dc=example,dc=com` (`3wm078uu`). On startup the API seeds a matching `uid=admin,ou=users,dc=example,dc=com` entry along with role groups under `ou=roles,dc=example,dc=com` so you can sign in immediately.
- Secure the coordination between the Go frontend and FastAPI backend by setting the same `TRIAGE_SHARED_SECRET` value for both services. The shared token gates `/auth/login` and `/admin/users` calls so only the frontend can manage identities.
- Runtime authorization is coordinated by [`static/js/auth_config.js`](frontend_go/public/static/js/auth_config.js) and [`static/js/authz.js`](frontend_go/public/static/js/authz.js). Pages mark privileged controls with `data-requires-role`, and the helper script disables them unless the signed-in user meets the threshold.
- Toggle future identity providers (LDAP/AD and OIDC) from the admin “Authentication wiring” section. The UI persists your switches to `triageAuthProviders`, ready for wiring into a real directory or SSO integration later.

### Directory configuration quick reference

- Compose brings up an `osixia/openldap` container with persistent volumes (`ldap_data`, `ldap_config`) so changes survive restarts.
- Environment overrides in [`.env`](.env) control how the API connects and bootstraps the directory. Set `TRIAGE_LDAP_*` variables to point at an external LDAP server or disable the integration entirely by switching `TRIAGE_LDAP_ENABLED` to `false`.
- Use `LDAP_HOST_PORT` if the host machine already consumes port 389; the container still listens on 389 internally so other services reach it via `ldap://ldap:389`.
- The stack now reuses the HTTPS certificate for LDAP. Override `LDAP_TLS_*` entries in [`.env`](.env) to supply a different certificate/key bundle or fall back to the auto-generated self-signed pair.
- Ensure the mapped certificate files remain writable from the container so the OpenLDAP entrypoint can adjust ownership and permissions during startup; otherwise the TLS bootstrap aborts early.
- The bootstrap administrator password is shared between PostgreSQL and LDAP (`3wm078uu` by default) to keep the sample experience consistent. Update both `TRIAGE_BOOTSTRAP_ADMIN_HASH` and `TRIAGE_LDAP_BOOTSTRAP_PASSWORD` when rotating secrets.

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

### X12 → CMS authenticity harness

A focused verification harness now lives at:

```bash
worker_py/tools_x12_cms_harness.py
```

It uses the licensed X222 research bundle under `/app/giles/docs/turbohedi/x222-005010/` to:
- parse 837 professional claim payloads
- verify high-value implementation-guide rules and control numbers
- project each `CLM` into CMS-friendly JSON for downstream mapping work

Run it with:

```bash
python3 worker_py/tools_x12_cms_harness.py samples/x12_837_large_valid.x12 --pretty
python3 -m unittest worker_py/test_x12_cms_harness.py
```

Current verified result on the bundled large sample:
- transaction: `837`
- implementation version: `005010X222A1`
- claims projected: `3200`
- validation errors: `0`

## Security Notes

- This sample accepts files and publishes raw bytes to the queue for simplicity.
- For production, store payloads in object storage and enqueue **pointers** (URLs/keys) plus checksums.
- Add size limits, authn/z (e.g., OAuth2/Keycloak), and audit logging.

## License

MIT
