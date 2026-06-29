import os, base64, json, uuid, logging, zipfile, time, secrets, hashlib, datetime
from contextlib import contextmanager
from io import BytesIO
from typing import List, Optional

import pika
import psycopg2
from psycopg2 import pool, errors
from psycopg2.extras import RealDictCursor
from fastapi import (
    FastAPI,
    UploadFile,
    File,
    HTTPException,
    Form,
    Query,
    Depends,
    Header,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from . import ldap_utils
try:
    from .security_deps import require_role, rate_limit
except ImportError:  # pragma: no cover - api image flat layout
    from security_deps import require_role, rate_limit  # type: ignore

# Server-side authorization + rate limiting. Role enforcement is opt-in via
# TRIAGE_ENFORCE_ROLES / TRIAGE_ENV=production (the frontend_go proxy injects
# X-TRIAGE-ROLE); rate limits are per-process and env-overridable.
_RL_LOGIN = rate_limit("login", limit=10, window_seconds=60)
_RL_INGEST = rate_limit("ingest", limit=60, window_seconds=60)
_ROLE_ADMIN = require_role("administrator")
_ROLE_SUBMIT = require_role("submit")

from claimtrace.audit import (
    CORRELATION_HEADER,
    bind_correlation_id,
    configure_structured_logging,
    get_correlation_id,
    log_event,
    new_correlation_id,
    reset_correlation_id,
)

try:
    from security import phi_crypto
except ModuleNotFoundError:  # api image bundles worker_py as a package
    from worker_py.security import phi_crypto
phi_crypto.assert_phi_ready()  # fail fast if PHI encryption required but no key
load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
# Workstream 3: standardize PHI-safe structured JSON logging with a correlation
# id propagated across api -> worker -> claimtrace.
configure_structured_logging("triage-api", level=LOG_LEVEL)
logger = logging.getLogger("api")

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
RMQ_QUEUE = os.getenv("RMQ_QUEUE", "edi_files")
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://edi:edi@postgres:5432/edi?sslmode=require"
)
ALLOWED_ORIGINS_RAW = os.getenv("TRIAGE_CORS_ORIGINS", "*")
ALLOWED_ORIGINS = [o.strip() for o in ALLOWED_ORIGINS_RAW.split(",") if o.strip()]
SHARED_SECRET = os.getenv("TRIAGE_SHARED_SECRET", "").strip()
# Secrets hygiene: refuse to run open in production rather than silently allowing
# unauthenticated access to protected routes.
REQUIRE_SECRETS = (
    os.getenv("TRIAGE_REQUIRE_SECRETS", "").strip().lower() in {"1", "true", "yes", "on"}
    or os.getenv("TRIAGE_ENV", "").strip().lower() in {"prod", "production"}
)
if not SHARED_SECRET:
    if REQUIRE_SECRETS:
        raise RuntimeError(
            "TRIAGE_SHARED_SECRET must be set when TRIAGE_REQUIRE_SECRETS is true "
            "or TRIAGE_ENV=production; refusing to start with protected routes open."
        )
    logger.warning("TRIAGE_SHARED_SECRET is not set; protected API routes are OPEN (dev only)")
PASSWORD_ITERATIONS = int(os.getenv("TRIAGE_PASSWORD_ITERATIONS", "180000"))
PASSWORD_SCHEME = "pbkdf2_sha256"
MIN_PASSWORD_LENGTH = int(os.getenv("TRIAGE_MIN_PASSWORD_LENGTH", "8"))
BOOTSTRAP_ADMIN_USER = os.getenv("TRIAGE_BOOTSTRAP_ADMIN_USER", "admin").strip()
BOOTSTRAP_ADMIN_HASH = os.getenv(
    "TRIAGE_BOOTSTRAP_ADMIN_HASH",
    "pbkdf2_sha256$180000$j3pt+dTfwUgP6VwHPPDNKA==$zojEXizGhZe8fEeCD655ThS8PIfIyz3jwgwWlUkS5hA=",
).strip()

ROLE_VIEW = "view"
ROLE_SUBMIT = "submit"
ROLE_ADMINISTRATOR = "administrator"
ROLE_ALIASES = {
    "admin": ROLE_ADMINISTRATOR,
    "administrator": ROLE_ADMINISTRATOR,
    "create": ROLE_SUBMIT,
    "update": ROLE_SUBMIT,
    "submitter": ROLE_SUBMIT,
    "editor": ROLE_SUBMIT,
    "submit": ROLE_SUBMIT,
    ROLE_VIEW: ROLE_VIEW,
}
VALID_ROLES = {ROLE_VIEW, ROLE_SUBMIT, ROLE_ADMINISTRATOR}
ROLE_HIERARCHY = [ROLE_VIEW, ROLE_SUBMIT, ROLE_ADMINISTRATOR]

LDAP_ENABLED = os.getenv("TRIAGE_LDAP_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LDAP_URI = os.getenv("TRIAGE_LDAP_URI", "ldap://ldap:389").strip()
LDAP_BASE_DN = os.getenv("TRIAGE_LDAP_BASE_DN", "dc=example,dc=com").strip()
LDAP_ROOT_CN = os.getenv("TRIAGE_LDAP_ROOT_CN", "ou=TRIAGE").strip()
LDAP_USERS_OU = os.getenv("TRIAGE_LDAP_USERS_OU", "ou=users").strip()
LDAP_ROLES_OU = os.getenv("TRIAGE_LDAP_ROLES_OU", "ou=roles").strip()
LDAP_TRADING_OU = os.getenv("TRIAGE_LDAP_TRADING_OU", "ou=trading-partners").strip()
LDAP_BIND_DN = os.getenv("TRIAGE_LDAP_BIND_DN", "").strip()
LDAP_BIND_PASSWORD = os.getenv("TRIAGE_LDAP_BIND_PASSWORD", "").strip()
LDAP_TIMEOUT = int(os.getenv("TRIAGE_LDAP_TIMEOUT", "10"))
LDAP_ADMIN_DN = os.getenv("TRIAGE_LDAP_ADMIN_DN", "").strip()
LDAP_ADMIN_USERNAME = os.getenv("TRIAGE_LDAP_ADMIN_USERNAME", BOOTSTRAP_ADMIN_USER).strip()
DEFAULT_LDAP_BOOTSTRAP_HASH = "{SSHA}X7IBzbN9pqFRQwwPu37o7OppFD69OTUK"
LDAP_BOOTSTRAP_PASSWORD_HASH = os.getenv(
    "TRIAGE_LDAP_BOOTSTRAP_PASSWORD_HASH", DEFAULT_LDAP_BOOTSTRAP_HASH
).strip()
LDAP_BOOTSTRAP_PASSWORD = os.getenv("TRIAGE_LDAP_BOOTSTRAP_PASSWORD", "").strip()
LDAP_BOOTSTRAP_USERNAME = os.getenv(
    "TRIAGE_LDAP_BOOTSTRAP_USERNAME", BOOTSTRAP_ADMIN_USER or "admin"
).strip()

ldap_manager: Optional[ldap_utils.LDAPManager] = None

app = FastAPI(title="Triage Ingest API", version="0.2.0")

# Turbo engines: SNIP 1-7 validation, CMS scrubbing, FHIR, supervised swarms.
try:
    from .turbo_routes import register as register_turbo_routes
except ImportError:  # tests / direct script invocation without package context
    from turbo_routes import register as register_turbo_routes  # type: ignore
register_turbo_routes(app)

try:
    from .loadtest_routes import register as register_loadtest_routes
except ImportError:
    from loadtest_routes import register as register_loadtest_routes  # type: ignore
register_loadtest_routes(app)

try:
    from .claimtrace_routes import register as register_claimtrace_routes
    from . import claimtrace_service
except ImportError:  # tests / direct script invocation without package context
    from claimtrace_routes import register as register_claimtrace_routes  # type: ignore
    import claimtrace_service  # type: ignore
register_claimtrace_routes(app, lambda: get_db())

# Normalized trading-partner management (Workstream 6).
try:
    from .partners_routes import register as register_partners_routes
    from . import partners_service
except ImportError:  # tests / direct script invocation without package context
    from partners_routes import register as register_partners_routes  # type: ignore
    import partners_service  # type: ignore
register_partners_routes(app, lambda: get_db())

# Workstream 5 — 835 restore: immutable storage, re-delivery, reconstruction,
# reversal. Workstream 4 — operational helpdesk: case management over rejections.
try:
    from .era_routes import register as register_era_routes
    from . import era_service
    from .helpdesk_routes import register as register_helpdesk_routes
    from . import helpdesk_service
except ImportError:  # tests / direct script invocation without package context
    from era_routes import register as register_era_routes  # type: ignore
    import era_service  # type: ignore
    from helpdesk_routes import register as register_helpdesk_routes  # type: ignore
    import helpdesk_service  # type: ignore
register_era_routes(app, lambda: get_db(), lambda message: publish_era_job(message))
register_helpdesk_routes(app, lambda: get_db())

# Workstream 1: advisory mapping-suggestion service (human-approval queue).
try:
    from .mapping_routes import register as register_mapping_routes, ensure_mapping_tables
except ImportError:  # tests / direct script invocation without package context
    from mapping_routes import register as register_mapping_routes, ensure_mapping_tables  # type: ignore
register_mapping_routes(app, lambda: get_db())

if not ALLOWED_ORIGINS:
    ALLOWED_ORIGINS = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if "*" in ALLOWED_ORIGINS else ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def correlation_id_middleware(request, call_next):
    """Bind a correlation id for the request and echo it on the response.

    The id is taken from the inbound ``X-Correlation-ID`` header when present
    (so an upstream caller / the worker can continue an existing lineage) and
    otherwise generated. Only the request *path* is logged — never the query
    string — to keep PHI out of the audit stream.
    """
    correlation_id = request.headers.get(CORRELATION_HEADER) or new_correlation_id()
    token = bind_correlation_id(correlation_id)
    try:
        log_event(
            logger,
            "http_request",
            method=request.method,
            path=request.url.path,
        )
        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = correlation_id
        log_event(
            logger,
            "http_response",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
        )
        return response
    except Exception:
        logger.exception("Unhandled error while processing request")
        raise
    finally:
        reset_correlation_id(token)

db_pool: Optional[pool.SimpleConnectionPool] = None

@app.on_event("startup")
def run_startup_migrations() -> None:
    try:
        with get_db() as conn:
            ensure_core_ingest_tables(conn)
            ensure_import_core_columns(conn)
            ensure_import_job_ids(conn)
            ensure_import_uploaded_by(conn)
            ensure_validation_columns(conn)
            ensure_app_users(conn)
            ensure_x12_addon_tables(conn)
            ensure_partner_configs_table(conn)
            partners_service.ensure_partner_tables(conn)
            ensure_mapping_tables(conn)
            if claimtrace_service.claimtrace_enabled():
                claimtrace_service.ensure_claimtrace_tables(conn)
                if era_service.era_enabled():
                    era_service.ensure_era_tables(conn)
                if helpdesk_service.helpdesk_enabled():
                    helpdesk_service.ensure_helpdesk_tables(conn)
            ensure_ldap_bootstrap(conn)
    except Exception:
        logger.exception("Failed to run startup migrations")
        raise
    _bootstrap_codeset_registry()


def _bootstrap_codeset_registry() -> None:
    """Load the effective-dated code-set registry (Workstream 2).

    Seeds the in-process registry from bundled versions, persists them to the
    codeset_* tables when a DB is reachable, then activates a DB-backed registry
    so SNIP type 5 + CMS scrubbing resolve codes by service date. Best-effort:
    failures here never block API startup (validation falls back to format
    checks when no registry is active).
    """
    try:
        from validation.codesets.db import (
            ensure_codeset_tables,
            load_active_registry,
            persist_version,
        )
        from validation.codesets.loader_service import load_seed_versions
        from validation.codesets.registry import set_active_registry

        seeded = load_seed_versions()
        set_active_registry(seeded)
        try:
            with get_db() as conn:
                ensure_codeset_tables(conn)
                for codeset in seeded.codesets():
                    for version in seeded.versions(codeset):
                        persist_version(conn, version)
                set_active_registry(load_active_registry(conn))
        except Exception:
            logger.warning(
                "Code-set DB persistence unavailable; using bundled seed registry",
                exc_info=True,
            )
    except Exception:
        logger.exception("Failed to bootstrap code-set registry")


def get_pool() -> pool.SimpleConnectionPool:
    global db_pool
    if db_pool is None:
        logger.info("Creating PostgreSQL connection pool")
        db_pool = pool.SimpleConnectionPool(1, 10, dsn=DATABASE_URL)
    return db_pool


@contextmanager
def get_db():
    conn = get_pool().getconn()
    try:
        yield conn
    finally:
        get_pool().putconn(conn)


def get_ldap_manager() -> Optional[ldap_utils.LDAPManager]:
    global ldap_manager
    if not LDAP_ENABLED:
        return None
    if ldap_manager is not None:
        return ldap_manager
    config = ldap_utils.LDAPConfig(
        uri=LDAP_URI,
        base_dn=LDAP_BASE_DN,
        root_cn=LDAP_ROOT_CN,
        users_ou=LDAP_USERS_OU,
        roles_ou=LDAP_ROLES_OU,
        trading_partners_ou=LDAP_TRADING_OU,
        bind_dn=LDAP_BIND_DN,
        bind_password=LDAP_BIND_PASSWORD,
        timeout=LDAP_TIMEOUT,
        bootstrap_username=LDAP_BOOTSTRAP_USERNAME or BOOTSTRAP_ADMIN_USER,
        bootstrap_password_hash=LDAP_BOOTSTRAP_PASSWORD_HASH or DEFAULT_LDAP_BOOTSTRAP_HASH,
        bootstrap_password_plain=LDAP_BOOTSTRAP_PASSWORD or None,
        admin_dn=LDAP_ADMIN_DN or None,
        admin_username=LDAP_ADMIN_USERNAME or None,
    )
    try:
        ldap_manager = ldap_utils.LDAPManager(
            config,
            role_hierarchy=ROLE_HIERARCHY,
            role_aliases=ROLE_ALIASES,
            administrator_role=ROLE_ADMINISTRATOR,
        )
    except ldap_utils.LDAPUnavailableError:
        logger.warning("LDAP integration requested but ldap3 is not installed")
        ldap_manager = None
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Failed to initialize LDAP manager")
        ldap_manager = None
    return ldap_manager


def is_safe_username(value: str) -> bool:
    if not value or len(value) > 96:
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_@")
    return all(ch in allowed for ch in value)


def normalize_role(role: Optional[str]) -> str:
    if not role:
        return ROLE_VIEW
    cleaned = role.strip().lower()
    return ROLE_ALIASES.get(cleaned, cleaned if cleaned in VALID_ROLES else ROLE_VIEW)


def is_valid_role(role: str) -> bool:
    if not role:
        return False
    cleaned = role.strip().lower()
    return cleaned in VALID_ROLES or cleaned in ROLE_ALIASES


def role_allows_admin(role: str) -> bool:
    return normalize_role(role) == ROLE_ADMINISTRATOR


def role_allows_portal(role: str) -> bool:
    normalized = normalize_role(role)
    return normalized in ROLE_HIERARCHY


def role_allows_submit(role: str) -> bool:
    normalized = normalize_role(role)
    return normalized in (ROLE_SUBMIT, ROLE_ADMINISTRATOR)


def hash_password(password: str) -> str:
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError("password too short")
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"{PASSWORD_SCHEME}${PASSWORD_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(derived).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iter_s, salt_b64, hash_b64 = encoded.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(iter_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except Exception:
        return False
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return secrets.compare_digest(derived, expected)


def row_to_user(row) -> Optional[dict]:
    if not row:
        return None
    role = normalize_role(row.get("role"))
    allow_admin = role_allows_admin(role)
    allow_portal = role_allows_portal(role)
    allow_submit = role_allows_submit(role)
    if role_allows_admin(role):
        # Administrator accounts should always retain full administrative and portal
        # privileges even if the stored flags drift. This guards the bootstrap admin as
        # well as any other elevated users from being locked out of required features.
        allow_portal = True
        allow_admin = True
        allow_submit = True
    def _serialize_timestamp(value):
        if value is None:
            return None
        if isinstance(value, (datetime.datetime, datetime.date)):
            # Ensure timezone-aware datetimes retain their offset and naive values
            # are treated as UTC to avoid ambiguity for the API consumers.
            if isinstance(value, datetime.datetime) and value.tzinfo is None:
                value = value.replace(tzinfo=datetime.timezone.utc)
            return value.isoformat()
        return str(value)

    return {
        "username": row.get("username"),
        "role": role,
        "allow_portal": allow_portal,
        "allow_submit": allow_submit,
        "allow_admin": allow_admin,
        "created_at": _serialize_timestamp(row.get("created_at")),
        "updated_at": _serialize_timestamp(row.get("updated_at")),
    }


def require_secret(header_value: Optional[str] = Header(None, alias="X-TRIAGE-SECRET")):
    if not SHARED_SECRET:
        if REQUIRE_SECRETS:
            raise HTTPException(status_code=503, detail="server misconfigured: shared secret unset")
        return
    if header_value is None or not secrets.compare_digest(header_value, SHARED_SECRET):
        raise HTTPException(status_code=401, detail="unauthorized")


def ensure_import_job_ids(conn) -> None:
    """Backfill and enforce the job_id column on imports for older databases."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'imports' AND column_name = 'job_id'
            )
            """
        )
        has_column = cur.fetchone()[0]
        if not has_column:
            logger.info("Adding job_id column to imports table")
            cur.execute("ALTER TABLE imports ADD COLUMN job_id UUID")

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM imports WHERE job_id IS NULL")
        missing = [row[0] for row in cur.fetchall()]
        for import_id in missing:
            generated = str(uuid.uuid4())
            logger.debug("Backfilling job_id %s for import %s", generated, import_id)
            cur.execute(
                "UPDATE imports SET job_id = %s WHERE id = %s",
                (generated, import_id),
            )

    with conn.cursor() as cur:
        cur.execute("ALTER TABLE imports ALTER COLUMN job_id SET NOT NULL")
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_imports_job_id ON imports(job_id)"
        )

    conn.commit()


def ensure_core_ingest_tables(conn) -> None:
    """Create the imports and related tables if they do not already exist."""

    logger.info("Ensuring core ingest tables exist")
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS imports (
                id SERIAL PRIMARY KEY,
                job_id UUID NOT NULL UNIQUE,
                filename TEXT NOT NULL,
                file_type TEXT NOT NULL DEFAULT 'unknown',
                byte_size INTEGER NOT NULL,
                uploaded_by TEXT,
                trading_partner_id TEXT,
                original_content BYTEA,
                status TEXT NOT NULL DEFAULT 'queued',
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                processed_at TIMESTAMP,
                claims_count INTEGER,
                order_lines_count INTEGER
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS claims (
                id SERIAL PRIMARY KEY,
                import_id INTEGER REFERENCES imports(id) ON DELETE CASCADE,
                claim_id TEXT,
                amount NUMERIC(12,2),
                raw_claim TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS order_lines (
                id SERIAL PRIMARY KEY,
                import_id INTEGER REFERENCES imports(id) ON DELETE CASCADE,
                line_no INTEGER,
                item_id TEXT,
                qty NUMERIC(12,3),
                price NUMERIC(12,2),
                raw_line TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS acks (
                id SERIAL PRIMARY KEY,
                import_id INTEGER REFERENCES imports(id) ON DELETE CASCADE,
                ack_type TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                content TEXT NOT NULL
            )
            """
        )

    conn.commit()


def _fetch_app_user_columns(conn) -> dict[str, dict[str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable
              FROM information_schema.columns
             WHERE table_name = 'app_users'
            """
        )
        return {
            row[0]: {"data_type": row[1], "is_nullable": row[2]} for row in cur.fetchall()
        }


def _normalize_app_user_roles(conn) -> int:
    updated = 0
    with conn.cursor() as cur:
        cur.execute("SELECT username, role, allow_portal, allow_admin FROM app_users")
        rows = cur.fetchall()

    if not rows:
        return 0

    with conn.cursor() as cur:
        for username, role, portal, admin in rows:
            normalized = normalize_role(role)
            if normalized not in VALID_ROLES:
                normalized = ROLE_VIEW
            desired_portal = role_allows_portal(normalized)
            desired_admin = role_allows_admin(normalized)
            if (
                role != normalized
                or bool(portal) != desired_portal
                or bool(admin) != desired_admin
            ):
                cur.execute(
                    """
                    UPDATE app_users
                       SET role = %s,
                           allow_portal = %s,
                           allow_admin = %s
                     WHERE username = %s
                    """,
                    (normalized, desired_portal, desired_admin, username),
                )
                updated += 1
    return updated


def ensure_app_users(conn) -> None:
    logger.info("Ensuring app_users table exists")
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS app_users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('view','submit','administrator')),
                allow_portal BOOLEAN NOT NULL DEFAULT TRUE,
                allow_admin BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS app_users_role_idx ON app_users (role)"
        )
    conn.commit()

    columns = _fetch_app_user_columns(conn)

    with conn.cursor() as cur:
        if "allow_portal" not in columns:
            logger.info("Adding allow_portal column to app_users table")
            cur.execute(
                "ALTER TABLE app_users ADD COLUMN allow_portal BOOLEAN NOT NULL DEFAULT TRUE"
            )
        else:
            cur.execute("ALTER TABLE app_users ALTER COLUMN allow_portal SET DEFAULT TRUE")
            cur.execute("UPDATE app_users SET allow_portal = TRUE WHERE allow_portal IS NULL")
            cur.execute("ALTER TABLE app_users ALTER COLUMN allow_portal SET NOT NULL")

        if "allow_admin" not in columns:
            logger.info("Adding allow_admin column to app_users table")
            cur.execute(
                "ALTER TABLE app_users ADD COLUMN allow_admin BOOLEAN NOT NULL DEFAULT FALSE"
            )
        else:
            cur.execute("ALTER TABLE app_users ALTER COLUMN allow_admin SET DEFAULT FALSE")
            cur.execute("UPDATE app_users SET allow_admin = FALSE WHERE allow_admin IS NULL")
            cur.execute("ALTER TABLE app_users ALTER COLUMN allow_admin SET NOT NULL")

        if "created_at" not in columns:
            logger.info("Adding created_at column to app_users table")
            cur.execute(
                "ALTER TABLE app_users ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            )
        else:
            cur.execute("ALTER TABLE app_users ALTER COLUMN created_at SET DEFAULT NOW()")
            cur.execute(
                "UPDATE app_users SET created_at = NOW() WHERE created_at IS NULL"
            )
            cur.execute("ALTER TABLE app_users ALTER COLUMN created_at SET NOT NULL")

        if "updated_at" not in columns:
            logger.info("Adding updated_at column to app_users table")
            cur.execute(
                "ALTER TABLE app_users ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            )
        else:
            cur.execute("ALTER TABLE app_users ALTER COLUMN updated_at SET DEFAULT NOW()")
            cur.execute(
                "UPDATE app_users SET updated_at = NOW() WHERE updated_at IS NULL"
            )
            cur.execute("ALTER TABLE app_users ALTER COLUMN updated_at SET NOT NULL")

        logger.info("Refreshing app_users role constraint")
        cur.execute("ALTER TABLE app_users DROP CONSTRAINT IF EXISTS app_users_role_check")

    normalized = _normalize_app_user_roles(conn)
    if normalized:
        logger.info("Normalized %s app_users role values", normalized)

    with conn.cursor() as cur:
        cur.execute(
            "ALTER TABLE app_users ADD CONSTRAINT app_users_role_check CHECK (role IN ('view','submit','administrator'))"
        )
    conn.commit()

    ensure_bootstrap_admin(conn)


def ensure_x12_addon_tables(conn) -> None:
    logger.info("Ensuring X12 add-on tables exist")
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS era_835_header (
                st_control TEXT PRIMARY KEY,
                bpr_method TEXT,
                bpr_amount NUMERIC,
                trn_trace TEXT,
                payer_name TEXT,
                payer_id TEXT,
                payee_name TEXT,
                payee_id TEXT,
                chk_date DATE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS era_835_clp (
                st_control TEXT,
                claim_id TEXT,
                status TEXT,
                total_charge NUMERIC,
                paid NUMERIC,
                patient_resp NUMERIC,
                payer_ctrl TEXT,
                facility TEXT,
                claim_freq TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS era_835_cas (
                st_control TEXT,
                claim_id TEXT,
                adj_group TEXT,
                adj_reason TEXT,
                amount NUMERIC,
                quantity INT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS era_835_plb (
                st_control TEXT,
                provider_id TEXT,
                fiscal_date DATE,
                adj_qual TEXT,
                ref_id TEXT,
                amount NUMERIC
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS eligibility_271 (
                st_control TEXT,
                subscriber_id TEXT,
                payer_id TEXT,
                eb_code TEXT,
                service_type TEXT,
                coverage_plan TEXT,
                network TEXT,
                description TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS claim_status_277 (
                st_control TEXT,
                subscriber_id TEXT,
                payer_claim_ctrl TEXT,
                status_info TEXT,
                status_date DATE,
                amount NUMERIC,
                quantity INT
            )
            """
        )
    conn.commit()


def ensure_bootstrap_admin(conn) -> None:
    if not BOOTSTRAP_ADMIN_USER or not BOOTSTRAP_ADMIN_HASH:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE app_users
               SET role = 'administrator',
                   allow_portal = TRUE,
                   allow_admin = TRUE,
                   updated_at = NOW()
             WHERE username = %s
               AND (
                    role <> 'administrator'
                 OR allow_portal IS DISTINCT FROM TRUE
                 OR allow_admin IS DISTINCT FROM TRUE
                 OR updated_at IS NULL
               )
            """,
            (BOOTSTRAP_ADMIN_USER,),
        )
        if cur.rowcount:
            logger.info(
                "Elevated bootstrap admin account %s to full portal/admin access",
                BOOTSTRAP_ADMIN_USER,
            )
            conn.commit()
            return

        cur.execute(
            "SELECT 1 FROM app_users WHERE username = %s",
            (BOOTSTRAP_ADMIN_USER,),
        )
        if cur.fetchone():
            logger.debug(
                "Bootstrap admin account %s already has full privileges", BOOTSTRAP_ADMIN_USER
            )
            return

    with conn.cursor() as cur:
        logger.info("Seeding bootstrap admin account %s", BOOTSTRAP_ADMIN_USER)
        cur.execute(
            """
            INSERT INTO app_users (username, password_hash, role, allow_portal, allow_admin)
            VALUES (%s, %s, 'administrator', TRUE, TRUE)
            """,
            (BOOTSTRAP_ADMIN_USER, BOOTSTRAP_ADMIN_HASH),
        )
    conn.commit()


def fetch_trading_partner_ids(conn) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT trading_partner_id
              FROM imports
             WHERE trading_partner_id IS NOT NULL
               AND TRIM(trading_partner_id) <> ''
             ORDER BY trading_partner_id
            """
        )
        return [row[0] for row in cur.fetchall()]


def ensure_partner_configs_table(conn) -> None:
    """Create the partner_configs table if it does not already exist."""
    logger.info("Ensuring partner_configs table exists")
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS partner_configs (
                id SERIAL PRIMARY KEY,
                trading_partner_id TEXT UNIQUE NOT NULL,
                name TEXT,
                contact_email TEXT,
                default_transaction_types JSONB DEFAULT '[]',
                auto_ack BOOLEAN DEFAULT FALSE,
                notes TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
            """
        )
    conn.commit()


def ensure_ldap_bootstrap(conn) -> None:
    manager = get_ldap_manager()
    if not manager:
        return
    try:
        partner_ids = fetch_trading_partner_ids(conn)
    except Exception:
        logger.exception("Failed to load trading partner IDs for LDAP bootstrap")
        partner_ids = []
    try:
        manager.bootstrap(partner_ids)
    except ldap_utils.LDAPUnavailableError:
        logger.warning("LDAP manager unavailable during bootstrap")
    except Exception:
        logger.exception("Failed to bootstrap LDAP directory")


def ldap_sync_user(
    username: str,
    role: str,
    password: Optional[str],
    *,
    allow_portal: bool,
    allow_admin: bool,
) -> Optional[dict]:
    manager = get_ldap_manager()
    if not manager:
        return None
    try:
        return manager.sync_user(
            username,
            role,
            password,
            portal=allow_portal,
            admin=allow_admin,
        )
    except ldap_utils.LDAPUnavailableError:
        return None
    except Exception:
        logger.exception("Failed to synchronize user %s with LDAP", username)
        return None


def ldap_delete_user(username: str) -> None:
    manager = get_ldap_manager()
    if not manager:
        return
    try:
        manager.delete_user(username)
    except ldap_utils.LDAPUnavailableError:
        return
    except Exception:
        logger.exception("Failed to delete LDAP entry for %s", username)


def ensure_import_core_columns(conn) -> None:
    """Ensure legacy imports tables have the columns expected by the app."""

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = 'imports'
            """
        )
        existing = {row[0] for row in cur.fetchall()}

    with conn.cursor() as cur:
        if "original_content" not in existing:
            logger.info("Adding original_content column to imports table")
            cur.execute("ALTER TABLE imports ADD COLUMN original_content BYTEA")
        if "status" not in existing:
            logger.info("Adding status column to imports table")
            cur.execute("ALTER TABLE imports ADD COLUMN status TEXT")
        if "file_type" not in existing:
            logger.info("Adding file_type column to imports table")
            cur.execute("ALTER TABLE imports ADD COLUMN file_type TEXT")
        if "created_at" not in existing:
            logger.info("Adding created_at column to imports table")
            cur.execute(
                "ALTER TABLE imports ADD COLUMN created_at TIMESTAMP NOT NULL DEFAULT NOW()"
            )
        if "processed_at" not in existing:
            logger.info("Adding processed_at column to imports table")
            cur.execute("ALTER TABLE imports ADD COLUMN processed_at TIMESTAMP")
        if "claims_count" not in existing:
            logger.info("Adding claims_count column to imports table")
            cur.execute("ALTER TABLE imports ADD COLUMN claims_count INTEGER")
        if "order_lines_count" not in existing:
            logger.info("Adding order_lines_count column to imports table")
            cur.execute("ALTER TABLE imports ADD COLUMN order_lines_count INTEGER")

    with conn.cursor() as cur:
        cur.execute("UPDATE imports SET status = 'queued' WHERE status IS NULL")
        cur.execute("ALTER TABLE imports ALTER COLUMN status SET DEFAULT 'queued'")
        cur.execute("ALTER TABLE imports ALTER COLUMN status SET NOT NULL")
        cur.execute("UPDATE imports SET file_type = 'unknown' WHERE file_type IS NULL")
        cur.execute("ALTER TABLE imports ALTER COLUMN file_type SET DEFAULT 'unknown'")
        cur.execute("ALTER TABLE imports ALTER COLUMN file_type SET NOT NULL")
        cur.execute("UPDATE imports SET created_at = NOW() WHERE created_at IS NULL")
        cur.execute("ALTER TABLE imports ALTER COLUMN created_at SET DEFAULT NOW()")
        cur.execute("ALTER TABLE imports ALTER COLUMN created_at SET NOT NULL")

    conn.commit()


def fetch_app_user(conn, username: str):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT username, password_hash, role, allow_portal, allow_admin, created_at, updated_at
              FROM app_users
             WHERE username = %s
            """,
            (username,),
        )
        return cur.fetchone()


def ensure_import_uploaded_by(conn) -> None:
    """Add the uploaded_by/trading_partner_id columns for older databases."""
    added = False
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'imports'
              AND column_name IN ('uploaded_by', 'trading_partner_id')
            """
        )
        existing = {row[0] for row in cur.fetchall()}

    with conn.cursor() as cur:
        if "uploaded_by" not in existing:
            logger.info("Adding uploaded_by column to imports table")
            cur.execute("ALTER TABLE imports ADD COLUMN uploaded_by TEXT")
            added = True
        if "trading_partner_id" not in existing:
            logger.info("Adding trading_partner_id column to imports table")
            cur.execute("ALTER TABLE imports ADD COLUMN trading_partner_id TEXT")
            added = True

    if added:
        conn.commit()


def ensure_validation_columns(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name
              FROM information_schema.columns
             WHERE table_name IN ('imports', 'claims')
               AND column_name IN (
                   'validation_status',
                   'validation_report_json',
                   'cms_projection_json',
                   'claim_status_code'
               )
            """
        )
        existing = {(row[0], row[1]) for row in cur.fetchall()}

    with conn.cursor() as cur:
        if ('imports', 'validation_status') not in existing:
            logger.info("Adding validation_status column to imports table")
            cur.execute("ALTER TABLE imports ADD COLUMN validation_status TEXT")
        if ('imports', 'validation_report_json') not in existing:
            logger.info("Adding validation_report_json column to imports table")
            cur.execute("ALTER TABLE imports ADD COLUMN validation_report_json JSONB")
        if ('claims', 'cms_projection_json') not in existing:
            logger.info("Adding cms_projection_json column to claims table")
            cur.execute("ALTER TABLE claims ADD COLUMN cms_projection_json JSONB")
        if ('claims', 'claim_status_code') not in existing:
            logger.info("Adding claim_status_code column to claims table")
            cur.execute("ALTER TABLE claims ADD COLUMN claim_status_code TEXT")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id SERIAL PRIMARY KEY,
                import_id INTEGER REFERENCES imports(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                detail_json JSONB,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )

    with conn.cursor() as cur:
        cur.execute("UPDATE imports SET validation_status = 'pending' WHERE validation_status IS NULL")
        cur.execute("ALTER TABLE imports ALTER COLUMN validation_status SET DEFAULT 'pending'")
        cur.execute("ALTER TABLE imports ALTER COLUMN validation_status SET NOT NULL")

    conn.commit()


def get_channel():
    params = pika.URLParameters(RABBITMQ_URL)
    connection = pika.BlockingConnection(params)
    ch = connection.channel()
    ch.queue_declare(queue=RMQ_QUEUE, durable=True)
    return connection, ch


ERA_JOBS_QUEUE = os.getenv("RMQ_ERA_QUEUE", "era_jobs")


def publish_era_job(message: dict) -> None:
    """Publish an ``era.redeliver`` / ``era.reconstruct`` job to RabbitMQ.

    Used by the ``/era/jobs`` endpoint so re-delivery and reconstruction can be
    processed asynchronously by the ERA worker consumer.
    """
    params = pika.URLParameters(RABBITMQ_URL)
    connection = pika.BlockingConnection(params)
    try:
        ch = connection.channel()
        ch.queue_declare(queue=ERA_JOBS_QUEUE, durable=True)
        ch.basic_publish(
            exchange="",
            routing_key=ERA_JOBS_QUEUE,
            body=json.dumps(message).encode("utf-8"),
            properties=pika.BasicProperties(delivery_mode=2),
        )
    finally:
        try:
            connection.close()
        except Exception:
            pass


def sanitize_filename_component(value: str, fallback: str = "file") -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in (value or ""))
    cleaned = cleaned.strip("._")
    return cleaned or fallback


def ack_file_extension(ack_type: str) -> str:
    upper = (ack_type or "").upper()
    if upper == "999":
        return ".999"
    if upper == "277CA":
        return ".277"
    if "CONTRL" in upper:
        return ".contrl"
    return ".txt"


def ack_download_filename(job_id: str, ack_type: str, ack_id: int) -> str:
    safe_type = sanitize_filename_component(ack_type or "ack", "ack")
    base = f"{job_id}_{safe_type}_{ack_id}"
    return f"{base}{ack_file_extension(ack_type or '')}"


class UserOut(BaseModel):
    username: str
    role: str
    allow_portal: bool
    allow_submit: bool
    allow_admin: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class UserList(BaseModel):
    users: List[UserOut]


class UserCreate(BaseModel):
    username: str
    password: str
    role: str


class UserUpdate(BaseModel):
    password: Optional[str] = None
    role: str


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    ok: bool
    user: Optional[UserOut] = None
    error: Optional[str] = None


class JobSummary(BaseModel):
    job_id: uuid.UUID
    filename: str
    file_type: str
    byte_size: int
    status: str
    created_at: str
    processed_at: Optional[str]
    uploaded_by: Optional[str]
    trading_partner_id: Optional[str]
    validation_status: Optional[str] = None
    claims_count: Optional[int]
    order_lines_count: Optional[int]
    ack_count: int


class AcknowledgementSummary(BaseModel):
    id: int
    ack_type: str
    created_at: Optional[str]
    size_bytes: Optional[int]


class Acknowledgement(AcknowledgementSummary):
    content: Optional[str]


class ValidationIssue(BaseModel):
    severity: Optional[str] = None
    code: Optional[str] = None
    message: Optional[str] = None
    segment_id: Optional[str] = None
    claim_id: Optional[str] = None
    loop_id: Optional[str] = None
    position: Optional[str] = None


class ClaimArtifact(BaseModel):
    claim_id: Optional[str] = None
    amount: Optional[str] = None
    raw_claim: Optional[str] = None
    claim_status_code: Optional[str] = None
    cms_projection_json: Optional[dict | list] = None


class AuditEventRecord(BaseModel):
    id: int
    event_type: str
    created_at: Optional[str]
    detail_json: Optional[dict | list] = None


class JobDetail(JobSummary):
    acknowledgements: List[Acknowledgement]
    validation_report_json: List[ValidationIssue] = []
    claims: List[ClaimArtifact] = []
    audit_events: List[AuditEventRecord] = []
    raw_payload_text: Optional[str] = None


class OpsPartnerException(BaseModel):
    trading_partner_id: str
    invalid_imports: int
    pending_imports: int


class OpsSummary(BaseModel):
    imports_today: int
    processing_now: int
    validation_failures_24h: int
    avg_processing_seconds_24h: Optional[float]
    recent_imports: List[JobSummary]
    partner_exceptions: List[OpsPartnerException]


# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self):
        self.active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        data = json.dumps(message)
        dead = set()
        for connection in self.active_connections:
            try:
                await connection.send_text(data)
            except Exception:
                dead.add(connection)
        for connection in dead:
            self.active_connections.discard(connection)


ws_manager = ConnectionManager()


async def broadcast_job_update(
    job_id: str,
    status: str,
    validation_status: Optional[str] = None,
    claims_count: Optional[int] = None,
    filename: Optional[str] = None,
):
    await ws_manager.broadcast(
        {
            "type": "job_update",
            "job_id": job_id,
            "status": status,
            "validation_status": validation_status,
            "claims_count": claims_count,
            "filename": filename,
        }
    )


@app.post("/auth/login")
def api_login(payload: LoginRequest, _: None = Depends(require_secret), _rl: None = Depends(_RL_LOGIN)):
    username = (payload.username or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="username required")
    if not payload.password:
        raise HTTPException(status_code=400, detail="password required")
    manager = get_ldap_manager()
    if manager:
        try:
            profile = manager.authenticate(username, payload.password)
        except Exception:
            logger.exception("LDAP authentication failed for %s", username)
            profile = None
        if profile:
            user = UserOut(**profile)
            return LoginResponse(ok=True, user=user).model_dump()
    with get_db() as conn:
        record = fetch_app_user(conn, username)
        if not record or not verify_password(payload.password, record.get("password_hash", "")):
            raise HTTPException(status_code=401, detail="invalid credentials")
        user_dict = row_to_user(record)
        synced = ldap_sync_user(
            username,
            user_dict["role"],
            payload.password,
            allow_portal=user_dict["allow_portal"],
            allow_admin=user_dict["allow_admin"],
        )
        if synced:
            user_dict = synced
        user = UserOut(**user_dict)
        return LoginResponse(ok=True, user=user).model_dump()


@app.get("/auth/users/{username}")
def api_get_user(username: str, _: None = Depends(require_secret)):
    cleaned = (username or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="username required")
    manager = get_ldap_manager()
    if manager:
        try:
            profile = manager.fetch_user(cleaned)
        except Exception:
            logger.exception("Failed to fetch LDAP profile for %s", cleaned)
            profile = None
        if profile:
            return {"user": UserOut(**profile).model_dump()}
    with get_db() as conn:
        record = fetch_app_user(conn, cleaned)
        if not record:
            raise HTTPException(status_code=404, detail="user not found")
        return {"user": UserOut(**row_to_user(record)).model_dump()}


def list_users_impl() -> dict:
    ldap_users: dict[str, dict] = {}
    manager = get_ldap_manager()
    if manager:
        try:
            for profile in manager.list_users():
                username = profile.get("username")
                if not username:
                    continue
                normalized_role = normalize_role(profile.get("role"))
                allow_admin = role_allows_admin(normalized_role) or bool(
                    profile.get("allow_admin", False)
                )
                allow_portal = role_allows_portal(normalized_role)
                ldap_users[username] = {
                    "username": username,
                    "role": normalized_role,
                    "allow_portal": allow_portal,
                    "allow_submit": role_allows_submit(normalized_role),
                    "allow_admin": allow_admin,
                    "created_at": profile.get("created_at"),
                    "updated_at": profile.get("updated_at"),
                }
        except Exception:
            logger.exception("Failed to enumerate LDAP users")
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT username, role, allow_portal, allow_admin, created_at, updated_at
                 FROM app_users
                 ORDER BY username
                """
            )
            rows = cur.fetchall()
    combined: dict[str, dict] = {}
    for row in rows:
        user = UserOut(**row_to_user(row)).model_dump()
        combined[user["username"]] = user
    for username, profile in ldap_users.items():
        combined[username] = UserOut(**profile).model_dump()
    users = [combined[key] for key in sorted(combined.keys())]
    return {"users": users}


@app.get("/admin/users")
def api_list_users(_: None = Depends(require_secret)):
    return list_users_impl()


def create_user_impl(payload: UserCreate) -> dict:
    username = payload.username.strip()
    if not is_safe_username(username):
        raise HTTPException(status_code=400, detail="invalid username")
    role_input = payload.role.strip().lower()
    if not is_valid_role(role_input):
        raise HTTPException(status_code=400, detail="invalid role")
    role = normalize_role(role_input)
    password = payload.password.strip()
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail="password too short")
    allow_admin = role_allows_admin(role)
    allow_portal = role_allows_portal(role)
    hashed = hash_password(password)
    with get_db() as conn:
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO app_users (username, password_hash, role, allow_portal, allow_admin)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING username, role, allow_portal, allow_admin, created_at, updated_at
                    """,
                    (username, hashed, role, allow_portal, allow_admin),
                )
                row = cur.fetchone()
            conn.commit()
        except errors.UniqueViolation:
            conn.rollback()
            raise HTTPException(status_code=409, detail="user already exists")
    profile = row_to_user(row)
    synced = ldap_sync_user(
        username,
        profile["role"],
        password,
        allow_portal=profile["allow_portal"],
        allow_admin=profile["allow_admin"],
    )
    if synced:
        profile = synced
    return {"user": UserOut(**profile).model_dump()}


@app.post("/admin/users")
def api_create_user(payload: UserCreate, _: None = Depends(require_secret), _role: None = Depends(_ROLE_ADMIN)):
    return create_user_impl(payload)


def update_user_impl(username: str, payload: UserUpdate) -> dict:
    cleaned = (username or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="username required")
    role_input = payload.role.strip().lower()
    if not is_valid_role(role_input):
        raise HTTPException(status_code=400, detail="invalid role")
    role = normalize_role(role_input)
    allow_admin = role_allows_admin(role)
    allow_portal = role_allows_portal(role)
    new_hash = None
    password_plain: Optional[str] = None
    if payload.password is not None:
        pwd = payload.password.strip()
        if pwd and len(pwd) < MIN_PASSWORD_LENGTH:
            raise HTTPException(status_code=400, detail="password too short")
        if pwd:
            new_hash = hash_password(pwd)
            password_plain = pwd
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            params = [role, allow_portal, allow_admin]
            assignments = ["role = %s", "allow_portal = %s", "allow_admin = %s", "updated_at = NOW()"]
            if new_hash:
                assignments.insert(0, "password_hash = %s")
                params.insert(0, new_hash)
            params.append(cleaned)
            cur.execute(
                f"""
                UPDATE app_users
                   SET {', '.join(assignments)}
                 WHERE username = %s
             RETURNING username, role, allow_portal, allow_admin, created_at, updated_at
                """,
                params,
            )
            row = cur.fetchone()
        if not row:
            conn.rollback()
            raise HTTPException(status_code=404, detail="user not found")
        conn.commit()
    profile = row_to_user(row)
    synced = ldap_sync_user(
        cleaned,
        profile["role"],
        password_plain,
        allow_portal=profile["allow_portal"],
        allow_admin=profile["allow_admin"],
    )
    if synced:
        profile = synced
    return {"user": UserOut(**profile).model_dump()}


@app.put("/admin/users/{username}")
def api_update_user(username: str, payload: UserUpdate, _: None = Depends(require_secret), _role: None = Depends(_ROLE_ADMIN)):
    return update_user_impl(username, payload)


def delete_user_impl(username: str) -> dict:
    cleaned = (username or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="username required")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM app_users WHERE username = %s RETURNING username", (cleaned,))
            deleted = cur.fetchone()
        if not deleted:
            conn.rollback()
            raise HTTPException(status_code=404, detail="user not found")
        conn.commit()
    ldap_delete_user(cleaned)
    return {"ok": True}


@app.delete("/admin/users/{username}")
def api_delete_user(username: str, _: None = Depends(require_secret), _role: None = Depends(_ROLE_ADMIN)):
    return delete_user_impl(username)


@app.get("/admin/api/users")
def api_list_users_admin(_: None = Depends(require_secret)):
    return list_users_impl()


@app.post("/admin/api/users")
def api_create_user_admin(payload: UserCreate, _: None = Depends(require_secret), _role: None = Depends(_ROLE_ADMIN)):
    return create_user_impl(payload)


@app.put("/admin/api/users/{username}")
def api_update_user_admin(username: str, payload: UserUpdate, _: None = Depends(require_secret), _role: None = Depends(_ROLE_ADMIN)):
    return update_user_impl(username, payload)


@app.delete("/admin/api/users/{username}")
def api_delete_user_admin(username: str, _: None = Depends(require_secret), _role: None = Depends(_ROLE_ADMIN)):
    return delete_user_impl(username)


@app.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    uploaded_by: Optional[str] = Form(default=None),
    trading_partner_id: Optional[str] = Form(default=None),
    _: None = Depends(require_secret),
    _rl: None = Depends(_RL_INGEST),
    _role: None = Depends(_ROLE_SUBMIT),
):
    import_id: Optional[int] = None
    start_time = time.perf_counter()
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty file")
        max_upload_mb = int(os.getenv('TRIAGE_MAX_UPLOAD_MB', '50'))
        if len(content) > max_upload_mb * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"File too large (max {max_upload_mb}MB)")

        job_uuid = uuid.uuid4()
        filename = file.filename or "upload.dat"
        size = len(content)

        # The Claimtrace lineage trace id equals the job id, so bind the request
        # correlation id to the job id: every downstream event (worker, RMQ ack,
        # claimtrace journal) shares this id and can be reconstructed end-to-end.
        correlation_id = str(job_uuid)
        bind_correlation_id(correlation_id)

        log_event(
            logger,
            "ingest_received",
            job_id=str(job_uuid),
            byte_size=size,
            uploaded_by=uploaded_by,
            trading_partner_id=trading_partner_id,
        )

        with get_db() as conn:
            with conn.cursor() as cur:
                db_start = time.perf_counter()
                cur.execute(
                    """
                    INSERT INTO imports (job_id, filename, byte_size, uploaded_by, trading_partner_id, original_content, status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (
                        str(job_uuid),
                        filename,
                        size,
                        uploaded_by,
                        trading_partner_id,
                        psycopg2.Binary(phi_crypto.seal_bytes(content)),
                        "queued",
                    ),
                )
                import_id = cur.fetchone()[0]
            if claimtrace_service.claimtrace_enabled():
                claimtrace_service.record_ingested_file(
                    conn,
                    import_id=import_id,
                    job_id=str(job_uuid),
                    filename=filename,
                    content=content,
                    uploaded_by=uploaded_by,
                    trading_partner_id=trading_partner_id,
                )
            conn.commit()

        logger.info(
            "Persisted import record id=%s for job %s in %.3fs",
            import_id,
            job_uuid,
            time.perf_counter() - db_start,
        )

        payload = {
            "job_id": str(job_uuid),
            "import_id": import_id,
            "filename": filename,
            "size": size,
            "uploaded_by": uploaded_by,
            "trading_partner_id": trading_partner_id,
            # Propagate the correlation id across the RabbitMQ hop so the worker
            # continues the same lineage rather than starting a fresh one.
            "correlation_id": correlation_id,
            "data_b64": base64.b64encode(content).decode("ascii"),
        }
        payload_bytes = json.dumps(payload).encode("utf-8")

        connection, ch = get_channel()
        try:
            publish_start = time.perf_counter()
            ch.basic_publish(
                exchange="",
                routing_key=RMQ_QUEUE,
                body=payload_bytes,
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    correlation_id=correlation_id,
                    headers={CORRELATION_HEADER: correlation_id},
                ),
            )
        finally:
            connection.close()

        logger.info(
            "Published job %s to queue %s in %.3fs",
            job_uuid,
            RMQ_QUEUE,
            time.perf_counter() - publish_start,
        )

        logger.info(
            "Enqueued job %s (%s bytes) for %s [uploaded_by=%s partner=%s]",
            job_uuid,
            size,
            filename,
            uploaded_by,
            trading_partner_id,
        )

        total_duration = time.perf_counter() - start_time
        logger.info("Completed ingest for job %s in %.3fs", job_uuid, total_duration)

        try:
            import asyncio
            asyncio.create_task(
                ws_manager.broadcast(
                    {
                        "type": "new_job",
                        "job_id": str(job_uuid),
                        "status": "queued",
                        "validation_status": "pending",
                        "claims_count": None,
                        "filename": filename,
                    }
                )
            )
        except Exception:
            logger.debug("WebSocket broadcast skipped (no event loop or no clients)")

        return {"job_id": str(job_uuid), "import_id": import_id, "queued_bytes": size, "filename": filename}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to enqueue file")
        if import_id is not None:
            try:
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE imports SET status='error', processed_at=NOW() WHERE id=%s",
                            (import_id,),
                        )
                    conn.commit()
            except Exception:
                logger.exception("Failed to update import status after enqueue error")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/jobs", response_model=List[JobSummary])
async def list_jobs(
    uploaded_by: Optional[str] = Query(default=None),
    trading_partner_id: Optional[str] = Query(default=None),
    limit: int = Query(default=25, ge=1, le=200),
    _: None = Depends(require_secret),
):
    if not uploaded_by and not trading_partner_id:
        raise HTTPException(status_code=400, detail="Provide uploaded_by or trading_partner_id to search")

    query = [
        "SELECT i.job_id, i.filename, i.file_type, i.byte_size, i.status, i.created_at, i.processed_at,",
        "       i.uploaded_by, i.trading_partner_id, i.validation_status, i.claims_count, i.order_lines_count,",
        "       COALESCE((SELECT COUNT(*) FROM acks a WHERE a.import_id = i.id), 0) AS ack_count",
        "FROM imports i WHERE 1=1",
    ]
    params: List[object] = []
    if uploaded_by:
        query.append("AND uploaded_by = %s")
        params.append(uploaded_by)
    if trading_partner_id:
        query.append("AND trading_partner_id = %s")
        params.append(trading_partner_id)
    query.append("ORDER BY created_at DESC LIMIT %s")
    params.append(limit)

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(" ".join(query), params)
            rows = cur.fetchall()

    results: List[JobSummary] = []
    for row in rows:
        results.append(
            JobSummary(
                job_id=row["job_id"],
                filename=row["filename"],
                file_type=row["file_type"],
                byte_size=row["byte_size"],
                status=row["status"],
                created_at=row["created_at"].isoformat() if row["created_at"] else None,
                processed_at=row["processed_at"].isoformat() if row["processed_at"] else None,
                uploaded_by=row.get("uploaded_by"),
                trading_partner_id=row.get("trading_partner_id"),
                validation_status=row.get("validation_status"),
                claims_count=row.get("claims_count"),
                order_lines_count=row.get("order_lines_count"),
                ack_count=int(row.get("ack_count") or 0),
            )
        )
    return results


@app.get("/jobs/{job_id}", response_model=JobDetail)
async def job_detail(job_id: str, _: None = Depends(require_secret)):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT i.*, 
                       COALESCE((SELECT COUNT(*) FROM acks a2 WHERE a2.import_id = i.id), 0) AS ack_count,
                       a.id AS ack_id,
                       a.ack_type,
                       a.content,
                       a.created_at AS ack_created_at,
                       OCTET_LENGTH(a.content) AS ack_size
                FROM imports i
                LEFT JOIN acks a ON a.import_id = i.id
                WHERE i.job_id = %s
                ORDER BY a.created_at DESC NULLS LAST
                """,
                (job_id,),
            )
            rows = cur.fetchall()

            if not rows:
                raise HTTPException(status_code=404, detail="Job not found")

            base = rows[0]
            import_id = base["id"]
            cur.execute(
                """
                SELECT claim_id, amount, raw_claim, claim_status_code, cms_projection_json
                  FROM claims
                 WHERE import_id = %s
                 ORDER BY id ASC
                 LIMIT 50
                """,
                (import_id,),
            )
            claim_rows = cur.fetchall()
            cur.execute(
                """
                SELECT id, event_type, detail_json, created_at
                  FROM audit_events
                 WHERE import_id = %s
                 ORDER BY created_at DESC, id DESC
                 LIMIT 100
                """,
                (import_id,),
            )
            audit_rows = cur.fetchall()

    acks: List[Acknowledgement] = []
    for row in rows:
        if row.get("ack_id"):
            acks.append(
                Acknowledgement(
                    id=row["ack_id"],
                    ack_type=row["ack_type"],
                    created_at=row["ack_created_at"].isoformat() if row["ack_created_at"] else None,
                    size_bytes=int(row.get("ack_size")) if row.get("ack_size") is not None else None,
                    content=row.get("content"),
                )
            )

    claims = [
        ClaimArtifact(
            claim_id=row.get("claim_id"),
            amount=str(row.get("amount")) if row.get("amount") is not None else None,
            raw_claim=phi_crypto.open_text(row.get("raw_claim")),
            claim_status_code=row.get("claim_status_code"),
            cms_projection_json=row.get("cms_projection_json"),
        )
        for row in claim_rows
    ]
    audit_events = [
        AuditEventRecord(
            id=int(row["id"]),
            event_type=row["event_type"],
            created_at=row["created_at"].isoformat() if row.get("created_at") else None,
            detail_json=row.get("detail_json"),
        )
        for row in audit_rows
    ]
    validation_issues = [
        ValidationIssue(**item)
        for item in (base.get("validation_report_json") or [])
        if isinstance(item, dict)
    ]
    raw_payload_text = None
    _ob = base.get("original_content")
    raw_bytes = phi_crypto.open_bytes(bytes(_ob)) if _ob is not None else None
    if raw_bytes is not None:
        try:
            raw_payload_text = bytes(raw_bytes).decode("utf-8", errors="replace")[:25000]
        except Exception:
            raw_payload_text = None

    ack_count_val = base.get("ack_count")
    detail = JobDetail(
        job_id=base["job_id"],
        filename=base["filename"],
        file_type=base["file_type"],
        byte_size=base["byte_size"],
        status=base["status"],
        created_at=base["created_at"].isoformat() if base["created_at"] else None,
        processed_at=base["processed_at"].isoformat() if base["processed_at"] else None,
        uploaded_by=base.get("uploaded_by"),
        trading_partner_id=base.get("trading_partner_id"),
        validation_status=base.get("validation_status"),
        claims_count=base.get("claims_count"),
        order_lines_count=base.get("order_lines_count"),
        ack_count=int(ack_count_val) if ack_count_val is not None else len(acks),
        acknowledgements=acks,
        validation_report_json=validation_issues,
        claims=claims,
        audit_events=audit_events,
        raw_payload_text=raw_payload_text,
    )
    return detail


@app.get("/ops/summary", response_model=OpsSummary)
async def ops_summary(_: None = Depends(require_secret)):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN created_at >= date_trunc('day', NOW()) THEN 1 ELSE 0 END), 0) AS imports_today,
                    COALESCE(SUM(CASE WHEN status IN ('queued', 'processing') THEN 1 ELSE 0 END), 0) AS processing_now,
                    COALESCE(SUM(CASE WHEN validation_status IN ('invalid', 'error') AND created_at >= NOW() - INTERVAL '24 hours' THEN 1 ELSE 0 END), 0) AS validation_failures_24h,
                    AVG(CASE WHEN processed_at IS NOT NULL THEN EXTRACT(EPOCH FROM (processed_at - created_at)) END) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours') AS avg_processing_seconds_24h
                FROM imports
                """
            )
            summary_row = cur.fetchone() or {}
            cur.execute(
                """
                SELECT i.job_id, i.filename, i.file_type, i.byte_size, i.status, i.created_at, i.processed_at,
                       i.uploaded_by, i.trading_partner_id, i.validation_status, i.claims_count, i.order_lines_count,
                       COALESCE((SELECT COUNT(*) FROM acks a WHERE a.import_id = i.id), 0) AS ack_count
                  FROM imports i
                 ORDER BY i.created_at DESC
                 LIMIT 8
                """
            )
            recent_rows = cur.fetchall()
            cur.execute(
                """
                SELECT COALESCE(NULLIF(trading_partner_id, ''), 'Unassigned') AS trading_partner_id,
                       SUM(CASE WHEN validation_status IN ('invalid', 'error') THEN 1 ELSE 0 END) AS invalid_imports,
                       SUM(CASE WHEN status IN ('queued', 'processing') THEN 1 ELSE 0 END) AS pending_imports
                  FROM imports
                 GROUP BY COALESCE(NULLIF(trading_partner_id, ''), 'Unassigned')
                 HAVING SUM(CASE WHEN validation_status IN ('invalid', 'error') THEN 1 ELSE 0 END) > 0
                     OR SUM(CASE WHEN status IN ('queued', 'processing') THEN 1 ELSE 0 END) > 0
                 ORDER BY invalid_imports DESC, pending_imports DESC, trading_partner_id ASC
                 LIMIT 6
                """
            )
            partner_rows = cur.fetchall()

    recent_imports = [
        JobSummary(
            job_id=row["job_id"],
            filename=row["filename"],
            file_type=row["file_type"],
            byte_size=row["byte_size"],
            status=row["status"],
            created_at=row["created_at"].isoformat() if row["created_at"] else None,
            processed_at=row["processed_at"].isoformat() if row["processed_at"] else None,
            uploaded_by=row.get("uploaded_by"),
            trading_partner_id=row.get("trading_partner_id"),
            validation_status=row.get("validation_status"),
            claims_count=row.get("claims_count"),
            order_lines_count=row.get("order_lines_count"),
            ack_count=int(row.get("ack_count") or 0),
        )
        for row in recent_rows
    ]
    partner_exceptions = [
        OpsPartnerException(
            trading_partner_id=row["trading_partner_id"],
            invalid_imports=int(row.get("invalid_imports") or 0),
            pending_imports=int(row.get("pending_imports") or 0),
        )
        for row in partner_rows
    ]
    avg_seconds = summary_row.get("avg_processing_seconds_24h")
    return OpsSummary(
        imports_today=int(summary_row.get("imports_today") or 0),
        processing_now=int(summary_row.get("processing_now") or 0),
        validation_failures_24h=int(summary_row.get("validation_failures_24h") or 0),
        avg_processing_seconds_24h=float(avg_seconds) if avg_seconds is not None else None,
        recent_imports=recent_imports,
        partner_exceptions=partner_exceptions,
    )


@app.get("/jobs/{job_id}/acks", response_model=List[AcknowledgementSummary])
async def list_job_acks(job_id: str, _: None = Depends(require_secret)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM imports WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Job not found")
            import_id = row[0]

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, ack_type, created_at, OCTET_LENGTH(content) AS size_bytes
                  FROM acks
                 WHERE import_id = %s
                 ORDER BY created_at ASC
                """,
                (import_id,),
            )
            rows = cur.fetchall()

    summaries: List[AcknowledgementSummary] = []
    for row in rows:
        summaries.append(
            AcknowledgementSummary(
                id=row["id"],
                ack_type=row["ack_type"],
                created_at=row["created_at"].isoformat() if row["created_at"] else None,
                size_bytes=int(row.get("size_bytes")) if row.get("size_bytes") is not None else None,
            )
        )
    return summaries


@app.get("/jobs/{job_id}/download")
async def download_original(job_id: str, _: None = Depends(require_secret)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT filename, original_content FROM imports WHERE job_id = %s",
                (job_id,),
            )
            row = cur.fetchone()

    if not row or row[1] is None:
        raise HTTPException(status_code=404, detail="Original file not stored")

    filename, data = row[0], row[1]
    buffer = BytesIO(phi_crypto.open_bytes(bytes(data)))
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(buffer, media_type="application/octet-stream", headers=headers)


@app.get("/jobs/{job_id}/acks/{ack_id}/download")
async def download_ack(job_id: str, ack_id: int, _: None = Depends(require_secret)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.ack_type, a.content
                  FROM acks a
                  JOIN imports i ON a.import_id = i.id
                 WHERE i.job_id = %s AND a.id = %s
                """,
                (job_id, ack_id),
            )
            row = cur.fetchone()

    if not row or row[1] is None:
        raise HTTPException(status_code=404, detail="Acknowledgement not found")

    ack_type, content = row
    buffer = BytesIO((content or "").encode("utf-8"))
    filename = ack_download_filename(job_id, ack_type, ack_id)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(buffer, media_type="text/plain", headers=headers)


@app.get("/jobs/{job_id}/acks.zip")
async def download_ack_archive(job_id: str, _: None = Depends(require_secret)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM imports WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Job not found")
            import_id = row[0]

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, ack_type, content
                  FROM acks
                 WHERE import_id = %s
                 ORDER BY created_at ASC
                """,
                (import_id,),
            )
            rows = cur.fetchall()

    wrote_any = False
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for row in rows:
            content = row.get("content")
            if content is None:
                continue
            ack_id = int(row.get("id")) if row.get("id") is not None else 0
            filename = ack_download_filename(job_id, row.get("ack_type"), ack_id)
            archive.writestr(filename, content)
            wrote_any = True

    if not wrote_any:
        raise HTTPException(status_code=404, detail="No acknowledgement content available")

    buffer.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="{job_id}_acks.zip"'}
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)


# ---------------------------------------------------------------------------
# Command Center API — routing, tier execution, exception workqueue
# ---------------------------------------------------------------------------

class RoutingDecisionSummary(BaseModel):
    routing_decision_id: Optional[str] = None
    file_id: Optional[str] = None
    tier: int = 0
    tier_label: str = "deterministic_fast_path"
    score_total: float = 0.0
    gate_triggers: Optional[list] = None
    explanation: Optional[str] = None
    decided_at: Optional[str] = None
    decision_duration_ms: Optional[float] = None

class TierExecutionSummary(BaseModel):
    tier: int = 0
    status: str = "pending"
    processing_ms: Optional[float] = None
    flags: Optional[list] = None
    recommendations: Optional[list] = None
    ai_analyses: Optional[list] = None
    swarm_task_count: int = 0
    supervisor_verdict: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[str] = None

class JobRoutingDetail(BaseModel):
    job_id: str
    filename: Optional[str] = None
    status: Optional[str] = None
    routing: Optional[RoutingDecisionSummary] = None
    tier_execution: Optional[TierExecutionSummary] = None

class TierDistribution(BaseModel):
    tier: int
    tier_label: str
    count: int
    pct: float

class ExceptionQueueItem(BaseModel):
    job_id: str
    filename: Optional[str] = None
    status: str
    tier: int
    tier_label: str
    score_total: float
    flags: Optional[list] = None
    supervisor_verdict: Optional[str] = None
    claims_count: Optional[int] = None
    total_claim_dollars: Optional[float] = None
    created_at: Optional[str] = None
    trading_partner_id: Optional[str] = None

class CommandCenterSummary(BaseModel):
    imports_today: int = 0
    processing_now: int = 0
    validation_failures_24h: int = 0
    avg_processing_seconds_24h: Optional[float] = None
    files_needing_action: int = 0
    dollars_at_risk: float = 0.0
    tier_distribution: List[TierDistribution] = []
    exception_queue: List[ExceptionQueueItem] = []
    recent_imports: List[JobSummary] = []
    partner_exceptions: List[OpsPartnerException] = []


@app.get("/ops/command-center", response_model=CommandCenterSummary)
async def command_center(_: None = Depends(require_secret)):
    """Exception Command Center — hero metrics, $ at risk, workqueues."""
    tier_labels = {0: "deterministic_fast_path", 1: "assisted_review", 2: "supervised_swarm", 3: "human_exception"}
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Core metrics
            cur.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN i.created_at >= date_trunc('day', NOW()) THEN 1 ELSE 0 END), 0) AS imports_today,
                    COALESCE(SUM(CASE WHEN i.status IN ('queued', 'processing') THEN 1 ELSE 0 END), 0) AS processing_now,
                    COALESCE(SUM(CASE WHEN i.validation_status IN ('invalid', 'error') AND i.created_at >= NOW() - INTERVAL '24 hours' THEN 1 ELSE 0 END), 0) AS validation_failures_24h,
                    AVG(CASE WHEN i.processed_at IS NOT NULL THEN EXTRACT(EPOCH FROM (i.processed_at - i.created_at)) END)
                        FILTER (WHERE i.created_at >= NOW() - INTERVAL '24 hours') AS avg_processing_seconds_24h
                FROM imports i
                """
            )
            metrics = cur.fetchone() or {}

            # Tier distribution
            cur.execute(
                """
                SELECT r.tier, COUNT(*) AS cnt
                FROM routing_decisions r
                JOIN imports i ON r.import_id = i.id
                WHERE i.created_at >= NOW() - INTERVAL '24 hours'
                GROUP BY r.tier
                ORDER BY r.tier
                """
            )
            tier_rows = cur.fetchall()
            total_routed = sum(r["cnt"] for r in tier_rows) or 1
            tier_dist = [
                TierDistribution(
                    tier=r["tier"],
                    tier_label=tier_labels.get(r["tier"], f"tier_{r['tier']}"),
                    count=r["cnt"],
                    pct=round(r["cnt"] / total_routed * 100, 1),
                )
                for r in tier_rows
            ]

            # Exception queue: files needing action (parked, needs_review, flagged)
            cur.execute(
                """
                SELECT i.job_id, i.filename, i.status, i.claims_count,
                       i.trading_partner_id, i.created_at,
                       r.tier, r.tier_label, r.score_total,
                       t.flags, t.supervisor_verdict,
                       COALESCE((SELECT SUM(c.amount) FROM claims c WHERE c.import_id = i.id), 0) AS total_claim_dollars
                FROM imports i
                JOIN routing_decisions r ON r.import_id = i.id
                LEFT JOIN tier_executions t ON t.import_id = i.id
                WHERE i.status IN ('parked', 'queued', 'processing')
                   OR t.status IN ('parked', 'needs_review')
                   OR i.validation_status IN ('invalid', 'error')
                ORDER BY COALESCE((SELECT SUM(c.amount) FROM claims c WHERE c.import_id = i.id), 0) DESC,
                         i.created_at DESC
                LIMIT 20
                """
            )
            exception_rows = cur.fetchall()

            dollars_at_risk = 0.0
            exception_queue = []
            for row in exception_rows:
                dollars = float(row.get("total_claim_dollars") or 0)
                dollars_at_risk += dollars
                flags_raw = row.get("flags")
                if isinstance(flags_raw, str):
                    try:
                        flags_raw = json.loads(flags_raw)
                    except Exception:
                        flags_raw = []
                exception_queue.append(
                    ExceptionQueueItem(
                        job_id=row["job_id"],
                        filename=row.get("filename"),
                        status=row["status"],
                        tier=row["tier"],
                        tier_label=row["tier_label"],
                        score_total=float(row.get("score_total") or 0),
                        flags=flags_raw,
                        supervisor_verdict=row.get("supervisor_verdict"),
                        claims_count=row.get("claims_count"),
                        total_claim_dollars=dollars,
                        created_at=row["created_at"].isoformat() if row.get("created_at") else None,
                        trading_partner_id=row.get("trading_partner_id"),
                    )
                )

            # Recent imports (same as ops/summary)
            cur.execute(
                """
                SELECT i.job_id, i.filename, i.file_type, i.byte_size, i.status, i.created_at, i.processed_at,
                       i.uploaded_by, i.trading_partner_id, i.validation_status, i.claims_count, i.order_lines_count,
                       COALESCE((SELECT COUNT(*) FROM acks a WHERE a.import_id = i.id), 0) AS ack_count
                FROM imports i
                ORDER BY i.created_at DESC
                LIMIT 10
                """
            )
            recent_rows = cur.fetchall()

            # Partner exceptions
            cur.execute(
                """
                SELECT COALESCE(NULLIF(trading_partner_id, ''), 'Unassigned') AS trading_partner_id,
                       SUM(CASE WHEN validation_status IN ('invalid', 'error') THEN 1 ELSE 0 END) AS invalid_imports,
                       SUM(CASE WHEN status IN ('queued', 'processing') THEN 1 ELSE 0 END) AS pending_imports
                FROM imports
                GROUP BY COALESCE(NULLIF(trading_partner_id, ''), 'Unassigned')
                HAVING SUM(CASE WHEN validation_status IN ('invalid', 'error') THEN 1 ELSE 0 END) > 0
                    OR SUM(CASE WHEN status IN ('queued', 'processing') THEN 1 ELSE 0 END) > 0
                ORDER BY invalid_imports DESC
                LIMIT 6
                """
            )
            partner_rows = cur.fetchall()

    recent_imports = [
        JobSummary(
            job_id=row["job_id"], filename=row["filename"], file_type=row["file_type"],
            byte_size=row["byte_size"], status=row["status"],
            created_at=row["created_at"].isoformat() if row["created_at"] else None,
            processed_at=row["processed_at"].isoformat() if row["processed_at"] else None,
            uploaded_by=row.get("uploaded_by"), trading_partner_id=row.get("trading_partner_id"),
            validation_status=row.get("validation_status"), claims_count=row.get("claims_count"),
            order_lines_count=row.get("order_lines_count"),
            ack_count=int(row.get("ack_count") or 0),
        )
        for row in recent_rows
    ]
    partner_exceptions = [
        OpsPartnerException(
            trading_partner_id=row["trading_partner_id"],
            invalid_imports=int(row.get("invalid_imports") or 0),
            pending_imports=int(row.get("pending_imports") or 0),
        )
        for row in partner_rows
    ]
    avg_s = metrics.get("avg_processing_seconds_24h")

    return CommandCenterSummary(
        imports_today=int(metrics.get("imports_today") or 0),
        processing_now=int(metrics.get("processing_now") or 0),
        validation_failures_24h=int(metrics.get("validation_failures_24h") or 0),
        avg_processing_seconds_24h=float(avg_s) if avg_s is not None else None,
        files_needing_action=len(exception_queue),
        dollars_at_risk=round(dollars_at_risk, 2),
        tier_distribution=tier_dist,
        exception_queue=exception_queue,
        recent_imports=recent_imports,
        partner_exceptions=partner_exceptions,
    )


@app.get("/jobs/{job_id}/routing", response_model=JobRoutingDetail)
async def job_routing_detail(job_id: str, _: None = Depends(require_secret)):
    """Get routing decision and tier execution details for a specific job."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, job_id, filename, status FROM imports WHERE job_id = %s", (job_id,))
            imp = cur.fetchone()
            if not imp:
                raise HTTPException(status_code=404, detail="Job not found")

            import_id = imp["id"]

            cur.execute(
                """
                SELECT routing_decision_id, file_id, tier, tier_label, score_total::float,
                       gate_triggers, explanation, decided_at, decision_duration_ms::float
                FROM routing_decisions WHERE import_id = %s ORDER BY id DESC LIMIT 1
                """,
                (import_id,),
            )
            rd = cur.fetchone()

            cur.execute(
                """
                SELECT tier, status, processing_ms::float, flags, recommendations,
                       ai_analyses, swarm_task_count, supervisor_verdict, error, created_at
                FROM tier_executions WHERE import_id = %s ORDER BY id DESC LIMIT 1
                """,
                (import_id,),
            )
            te = cur.fetchone()

    routing = None
    if rd:
        gt = rd.get("gate_triggers")
        if isinstance(gt, str):
            try: gt = json.loads(gt)
            except Exception: gt = []
        routing = RoutingDecisionSummary(
            routing_decision_id=rd.get("routing_decision_id"),
            file_id=rd.get("file_id"),
            tier=rd["tier"], tier_label=rd["tier_label"],
            score_total=rd["score_total"],
            gate_triggers=gt,
            explanation=rd.get("explanation"),
            decided_at=rd["decided_at"].isoformat() if rd.get("decided_at") else None,
            decision_duration_ms=rd.get("decision_duration_ms"),
        )

    tier_exec = None
    if te:
        for field in ("flags", "recommendations", "ai_analyses"):
            v = te.get(field)
            if isinstance(v, str):
                try: te[field] = json.loads(v)
                except Exception: te[field] = []
        tier_exec = TierExecutionSummary(
            tier=te["tier"], status=te["status"],
            processing_ms=te.get("processing_ms"),
            flags=te.get("flags"), recommendations=te.get("recommendations"),
            ai_analyses=te.get("ai_analyses"),
            swarm_task_count=te.get("swarm_task_count") or 0,
            supervisor_verdict=te.get("supervisor_verdict"),
            error=te.get("error"),
            created_at=te["created_at"].isoformat() if te.get("created_at") else None,
        )

    return JobRoutingDetail(
        job_id=imp["job_id"], filename=imp.get("filename"),
        status=imp.get("status"), routing=routing, tier_execution=tier_exec,
    )


# ---------------------------------------------------------------------------
# Partner configuration CRUD
# ---------------------------------------------------------------------------

class PartnerConfig(BaseModel):
    trading_partner_id: str
    name: Optional[str] = None
    contact_email: Optional[str] = None
    default_transaction_types: List[str] = []
    auto_ack: bool = False
    notes: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


def _row_to_partner_config(row) -> dict:
    dtypes = row.get("default_transaction_types")
    if isinstance(dtypes, str):
        try:
            dtypes = json.loads(dtypes)
        except Exception:
            dtypes = []
    if dtypes is None:
        dtypes = []
    return {
        "trading_partner_id": row["trading_partner_id"],
        "name": row.get("name"),
        "contact_email": row.get("contact_email"),
        "default_transaction_types": dtypes,
        "auto_ack": bool(row.get("auto_ack", False)),
        "notes": row.get("notes"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


@app.get("/partners")
async def list_partners(_: None = Depends(require_secret)):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT trading_partner_id, name, contact_email, default_transaction_types,
                       auto_ack, notes, created_at, updated_at
                  FROM partner_configs
                 ORDER BY trading_partner_id
                """
            )
            rows = cur.fetchall()
    return {"partners": [_row_to_partner_config(row) for row in rows]}


@app.get("/partners/{partner_id}")
async def get_partner(partner_id: str, _: None = Depends(require_secret)):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT trading_partner_id, name, contact_email, default_transaction_types,
                       auto_ack, notes, created_at, updated_at
                  FROM partner_configs
                 WHERE trading_partner_id = %s
                """,
                (partner_id,),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Partner not found")
    return {"partner": _row_to_partner_config(row)}


@app.post("/partners", status_code=201)
async def create_partner(payload: PartnerConfig, _: None = Depends(require_secret), _role: None = Depends(_ROLE_SUBMIT)):
    with get_db() as conn:
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO partner_configs
                        (trading_partner_id, name, contact_email, default_transaction_types, auto_ack, notes)
                    VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                    RETURNING trading_partner_id, name, contact_email, default_transaction_types,
                              auto_ack, notes, created_at, updated_at
                    """,
                    (
                        payload.trading_partner_id,
                        payload.name,
                        payload.contact_email,
                        json.dumps(payload.default_transaction_types),
                        payload.auto_ack,
                        payload.notes,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        except errors.UniqueViolation:
            conn.rollback()
            raise HTTPException(status_code=409, detail="Partner already exists")
    return {"partner": _row_to_partner_config(row)}


@app.put("/partners/{partner_id}")
async def update_partner(
    partner_id: str, payload: PartnerConfig, _: None = Depends(require_secret), _role: None = Depends(_ROLE_SUBMIT)
):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE partner_configs
                   SET name = %s,
                       contact_email = %s,
                       default_transaction_types = %s::jsonb,
                       auto_ack = %s,
                       notes = %s,
                       updated_at = NOW()
                 WHERE trading_partner_id = %s
                RETURNING trading_partner_id, name, contact_email, default_transaction_types,
                          auto_ack, notes, created_at, updated_at
                """,
                (
                    payload.name,
                    payload.contact_email,
                    json.dumps(payload.default_transaction_types),
                    payload.auto_ack,
                    payload.notes,
                    partner_id,
                ),
            )
            row = cur.fetchone()
        if not row:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Partner not found")
        conn.commit()
    return {"partner": _row_to_partner_config(row)}


@app.delete("/partners/{partner_id}")
async def delete_partner(partner_id: str, _: None = Depends(require_secret), _role: None = Depends(_ROLE_SUBMIT)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM partner_configs WHERE trading_partner_id = %s RETURNING trading_partner_id",
                (partner_id,),
            )
            deleted = cur.fetchone()
        if not deleted:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Partner not found")
        conn.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Alerts endpoint
# ---------------------------------------------------------------------------

class AlertItem(BaseModel):
    id: str
    severity: str  # "critical" | "warning" | "info"
    title: str
    message: str
    metric: str
    threshold: float
    current_value: float
    created_at: str


@app.get("/ops/alerts")
async def ops_alerts(_: None = Depends(require_secret)):
    """Return dynamically generated active alerts based on current system state."""
    alerts: List[dict] = []
    now_iso = datetime.datetime.utcnow().isoformat() + "Z"

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Files parked > 1 hour
            cur.execute(
                """
                SELECT COUNT(*) AS cnt
                  FROM imports
                 WHERE status IN ('parked', 'queued', 'processing')
                   AND created_at < NOW() - INTERVAL '1 hour'
                """
            )
            parked_row = cur.fetchone() or {}
            parked_count = int(parked_row.get("cnt") or 0)

            # Dollars at risk (parked/queued/processing)
            cur.execute(
                """
                SELECT COALESCE(SUM(c.amount), 0) AS total
                  FROM claims c
                  JOIN imports i ON c.import_id = i.id
                 WHERE i.status IN ('parked', 'queued', 'processing')
                """
            )
            dollars_row = cur.fetchone() or {}
            dollars_at_risk = float(dollars_row.get("total") or 0)

            # Validation failure rate in last hour
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN validation_status IN ('invalid', 'error') THEN 1 ELSE 0 END) AS failures
                  FROM imports
                 WHERE created_at >= NOW() - INTERVAL '1 hour'
                """
            )
            vf_row = cur.fetchone() or {}
            vf_total = int(vf_row.get("total") or 0)
            vf_failures = int(vf_row.get("failures") or 0)
            vf_rate = (vf_failures / vf_total * 100) if vf_total > 0 else 0.0

            # Average processing time last hour
            cur.execute(
                """
                SELECT AVG(EXTRACT(EPOCH FROM (processed_at - created_at))) AS avg_s
                  FROM imports
                 WHERE processed_at IS NOT NULL
                   AND created_at >= NOW() - INTERVAL '1 hour'
                """
            )
            avg_row = cur.fetchone() or {}
            avg_proc = float(avg_row.get("avg_s") or 0)

            # New partners (first submission in last 24h)
            cur.execute(
                """
                SELECT trading_partner_id
                  FROM imports
                 WHERE trading_partner_id IS NOT NULL
                   AND TRIM(trading_partner_id) <> ''
                   AND created_at >= NOW() - INTERVAL '24 hours'
                GROUP BY trading_partner_id
                HAVING MIN(created_at) >= NOW() - INTERVAL '24 hours'
                   AND COUNT(*) = 1
                   AND NOT EXISTS (
                       SELECT 1 FROM imports i2
                        WHERE i2.trading_partner_id = imports.trading_partner_id
                          AND i2.created_at < NOW() - INTERVAL '24 hours'
                   )
                """
            )
            new_partner_rows = cur.fetchall()

            # Tier distribution shift (tier 2+ > 30% in last hour vs prior hour)
            cur.execute(
                """
                SELECT
                    SUM(CASE WHEN r.tier >= 2 AND i.created_at >= NOW() - INTERVAL '1 hour' THEN 1 ELSE 0 END)::float
                        / NULLIF(SUM(CASE WHEN i.created_at >= NOW() - INTERVAL '1 hour' THEN 1 ELSE 0 END), 0) AS recent_high_tier_pct,
                    SUM(CASE WHEN r.tier >= 2 AND i.created_at BETWEEN NOW() - INTERVAL '2 hours' AND NOW() - INTERVAL '1 hour' THEN 1 ELSE 0 END)::float
                        / NULLIF(SUM(CASE WHEN i.created_at BETWEEN NOW() - INTERVAL '2 hours' AND NOW() - INTERVAL '1 hour' THEN 1 ELSE 0 END), 0) AS prior_high_tier_pct
                FROM imports i
                LEFT JOIN routing_decisions r ON r.import_id = i.id
                WHERE i.created_at >= NOW() - INTERVAL '2 hours'
                """
            )
            tier_shift_row = cur.fetchone() or {}
            recent_pct = float(tier_shift_row.get("recent_high_tier_pct") or 0) * 100
            prior_pct = float(tier_shift_row.get("prior_high_tier_pct") or 0) * 100

    # --- Critical alerts ---
    if parked_count > 0:
        alerts.append(
            AlertItem(
                id=f"parked_files_{parked_count}",
                severity="critical",
                title="Files stalled in queue",
                message=f"{parked_count} file(s) have been queued or processing for over 1 hour without completion.",
                metric="parked_files_over_1h",
                threshold=0,
                current_value=float(parked_count),
                created_at=now_iso,
            ).model_dump()
        )

    if dollars_at_risk > 1_000_000:
        alerts.append(
            AlertItem(
                id="dollars_at_risk_critical",
                severity="critical",
                title="High dollar exposure in queue",
                message=f"${dollars_at_risk:,.2f} in claims are currently in unprocessed or parked files.",
                metric="dollars_at_risk",
                threshold=1_000_000.0,
                current_value=round(dollars_at_risk, 2),
                created_at=now_iso,
            ).model_dump()
        )

    # --- Warning alerts ---
    if vf_rate > 20.0:
        alerts.append(
            AlertItem(
                id="validation_failure_rate_warning",
                severity="warning",
                title="Elevated validation failure rate",
                message=f"Validation failure rate is {vf_rate:.1f}% over the last hour ({vf_failures}/{vf_total} files).",
                metric="validation_failure_rate_pct_1h",
                threshold=20.0,
                current_value=round(vf_rate, 2),
                created_at=now_iso,
            ).model_dump()
        )

    if avg_proc > 30.0:
        alerts.append(
            AlertItem(
                id="avg_processing_time_warning",
                severity="warning",
                title="Slow average processing time",
                message=f"Average processing time over the last hour is {avg_proc:.1f}s (threshold: 30s).",
                metric="avg_processing_seconds_1h",
                threshold=30.0,
                current_value=round(avg_proc, 2),
                created_at=now_iso,
            ).model_dump()
        )

    # --- Info alerts ---
    for row in new_partner_rows:
        pid = row.get("trading_partner_id", "unknown")
        alerts.append(
            AlertItem(
                id=f"new_partner_{pid}",
                severity="info",
                title="New trading partner first submission",
                message=f"Trading partner '{pid}' submitted their first EDI file in the last 24 hours.",
                metric="new_partner_submission",
                threshold=0.0,
                current_value=1.0,
                created_at=now_iso,
            ).model_dump()
        )

    if prior_pct > 0 and recent_pct > prior_pct * 1.5 and recent_pct > 10:
        alerts.append(
            AlertItem(
                id="tier_distribution_shift_info",
                severity="info",
                title="Tier distribution shift detected",
                message=f"High-complexity files (tier 2+) rose from {prior_pct:.1f}% to {recent_pct:.1f}% of submissions in the last hour.",
                metric="high_tier_pct_1h",
                threshold=round(prior_pct, 2),
                current_value=round(recent_pct, 2),
                created_at=now_iso,
            ).model_dump()
        )

    return {"alerts": alerts, "count": len(alerts)}


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws/jobs")
async def websocket_jobs(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep the connection alive; client may send pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


@app.on_event("shutdown")
def close_pool():
    global db_pool
    if db_pool is not None:
        logger.info("Closing PostgreSQL connection pool")
        db_pool.closeall()
