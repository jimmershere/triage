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

### Running with Podman

Rootless Podman on Ubuntu 24.04+ defaults to the systemd/journald control plane.
When you log in through a non-interactive session (for example SSH without
`loginctl enable-linger`), builds fail with errors like:

```
sd-bus call: Interactive authentication required.: Permission denied
```

The bundled [`.env`](.env) now forces the lightweight `cgroupfs` and file-based
event logger backends so `podman-compose build` works without tweaking host
configuration. After copying `.env.example` to `.env`, run:

```bash
podman-compose build
podman-compose up
```

These environment variables are ignored by Docker but automatically picked up by
Podman, keeping the stack compatible across both container runtimes.

## Enabling OAuth2/OIDC single sign-on

The frontend now expects an external identity provider (for example Keycloak) to handle
authentication via [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/). When the proxy is
running on your network, route the built-in `/oauth2/*` paths to it by setting
`HEDI_OAUTH2_PROXY_URL` before starting the `frontend_go` service:

```bash
HEDI_OAUTH2_PROXY_URL=https://oauth2-proxy.internal:4180 docker compose up frontend_go
```

If the upstream proxy uses a self-signed certificate, enable lenient TLS verification with
`HEDI_OAUTH2_PROXY_INSECURE_SKIP_VERIFY=true`. The flag only affects calls to the OAuth proxy; the
API reverse proxy continues to enforce normal certificate validation.

When the public OAuth URL differs from the network location the Go frontend can reach (for example
when the hostname only resolves inside your cluster), set `HEDI_OAUTH2_PROXY_INTERNAL_URL` to the
reachable origin. The frontend continues to advertise the external URL via `config.js`, while the
internal URL drives the reverse proxy connection from the container:

```bash
HEDI_OAUTH2_PROXY_URL=https://oauth2-proxy.example.com/oauth2 \
HEDI_OAUTH2_PROXY_INTERNAL_URL=http://rbac:4180/oauth2 \
docker compose up frontend_go
```

The helper endpoint at `/config.js` also publishes `window.HEDI_OAUTH2_START`, so you can point the
UI at a different login entrypoint if your deployment uses a non-standard path:

```bash
# Optional override if the proxy is mounted on a different prefix
HEDI_OAUTH2_START=/sso/start
```

Once configured, the login buttons on the claims portal and admin console redirect to the proxy
instead of returning a 404.

For the starter stack we ship a lightweight RBAC service that emulates the oauth2-proxy contract so
you can exercise the portal out-of-the-box. The `rbac` container listens on port `4180`, presents a
simple login form, authenticates users against the FastAPI backend, and issues signed session
cookies that the frontend validates on subsequent requests. Docker Compose automatically wires
`HEDI_OAUTH2_PROXY_URL` to `http://rbac:4180`, so `docker compose up --build` exposes both the
frontend (`8080/8443`) and the RBAC login endpoint (`4180`). Sign in with the bootstrap
administrator (`admin` / `3wm078uu`) or any user you add from the Admin → User Management screen,
and the portal/admin pages will render with the correct RBAC headers.

## Customer support assistant configuration

The web UI now ships with the “Trish” customer advocate, complete with helpful callouts and an in-app chat assistant. The chat widget can raise trouble tickets by generating unique request IDs and preparing `mailto:`/`sms:` links.

- Update the default contact points in [`frontend_go/public/static/js/support_config.js`](frontend_go/public/static/js/support_config.js) (and the mirrored file under `_container_public/static/js/`) to wire in your production support mailbox or SMS gateway.
- The same configuration is reused across every page, so a single change covers the Claims Portal, Processed Files, HEDI Mapping, Claim Entry, and the login/admin surfaces.
- Messages that include words like “error” or “trouble” automatically produce a ticket reference in the chat transcript so agents can track the conversation against your downstream systems. Trish now follows up to capture the severity (1–4) before logging each ticket.

## Canvas validation helpers

HEDI’s visual mapper now ships with richer instrumentation so analysts can immediately understand why rows are highlighted:

- The XML-style wrapper row (`<transactionSet type="…">`) inspects the first `ST`/`GS` segments in the uploaded payload and updates itself to match the detected transaction variant (for example `837D` vs `837P`). This keeps the preview aligned with the real file type even when users mix dental, professional, or institutional claim templates.
- Any row that violates the lightweight X12 rules (invalid identifiers, unrecognised segment IDs, disallowed characters, or missing `~` terminators) gains a light-blue thought bubble. Clicking the bubble toggles a friendly panel featuring `trish-laptop.svg` and bullet points that spell out the exact violation so analysts can reconcile the raw X12 text with the highlights.
- Only one bubble is open at a time and re-clicking it closes the guidance, making it easy to step through each exception without losing your place in the canvas.

These helpers live in [`frontend_go/public/static/js/mapper.js`](frontend_go/public/static/js/mapper.js) (and the mirrored `_container_public` copy) with the styles in [`frontend_go/public/static/css/styles.css`](frontend_go/public/static/css/styles.css).

## Admin dashboard, tickets, and role wiring

- The admin portal renders active tickets raised by the chat assistant. Entries are stored in-browser under the `hediSupportTickets` key and surface the ID, submission timestamp, summary, and severity ranking.
- Stage users and assign application roles (view → update → create → admin) from the Admin → “User & role management” card. Accounts are written to the `app_users` table in PostgreSQL with PBKDF2-hashed credentials and per-portal access flags.
- A bootstrap administrator account is provisioned during API startup. By default the username is `admin` and the stored hash corresponds to the password `3wm078uu`. Override `HEDI_BOOTSTRAP_ADMIN_USER` and/or `HEDI_BOOTSTRAP_ADMIN_HASH` (a PBKDF2 string) to rotate these credentials before first run.
- OpenLDAP is bundled in the compose stack for directory-backed authentication. The container exposes `ldap://localhost:389` with the base DN `dc=example,dc=com` and an administrative bind account `cn=admin,dc=example,dc=com` (`3wm078uu`). On startup the API seeds a matching `uid=admin,ou=users,dc=example,dc=com` entry along with role groups under `ou=roles,dc=example,dc=com` so you can sign in immediately.
- Secure the coordination between the Go frontend and FastAPI backend by setting the same `HEDI_SHARED_SECRET` value for both services. The shared token gates `/auth/login` and `/admin/users` calls so only the frontend can manage identities.
- Runtime authorization is coordinated by [`static/js/auth_config.js`](frontend_go/public/static/js/auth_config.js) and [`static/js/authz.js`](frontend_go/public/static/js/authz.js). Pages mark privileged controls with `data-requires-role`, and the helper script disables them unless the signed-in user meets the threshold.
- Toggle future identity providers (LDAP/AD and OIDC) from the admin “Authentication wiring” section. The UI persists your switches to `hediAuthProviders`, ready for wiring into a real directory or SSO integration later.

### Directory configuration quick reference

- Compose brings up an `osixia/openldap` container with persistent volumes (`ldap_data`, `ldap_config`) so changes survive restarts.
- Environment overrides in [`.env`](.env) control how the API connects and bootstraps the directory. Set `HEDI_LDAP_*` variables to point at an external LDAP server or disable the integration entirely by switching `HEDI_LDAP_ENABLED` to `false`.
- Use `LDAP_HOST_PORT` if the host machine already consumes port 389; the container still listens on 389 internally so other services reach it via `ldap://ldap:389`.
- The stack now reuses the HTTPS certificate for LDAP. Override `LDAP_TLS_*` entries in [`.env`](.env) to supply a different certificate/key bundle or fall back to the auto-generated self-signed pair.
- Ensure the mapped certificate files remain writable from the container so the OpenLDAP entrypoint can adjust ownership and permissions during startup; otherwise the TLS bootstrap aborts early.
- The bootstrap administrator password is shared between PostgreSQL and LDAP (`3wm078uu` by default) to keep the sample experience consistent. Update both `HEDI_BOOTSTRAP_ADMIN_HASH` and `HEDI_LDAP_BOOTSTRAP_PASSWORD` when rotating secrets.

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
