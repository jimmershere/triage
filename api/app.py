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
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("api")

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
RMQ_QUEUE = os.getenv("RMQ_QUEUE", "edi_files")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://edi:edi@postgres:5432/edi")
ALLOWED_ORIGINS_RAW = os.getenv("HEDI_CORS_ORIGINS", "*")
ALLOWED_ORIGINS = [o.strip() for o in ALLOWED_ORIGINS_RAW.split(",") if o.strip()]
SHARED_SECRET = os.getenv("HEDI_SHARED_SECRET", "change-me")
PASSWORD_ITERATIONS = int(os.getenv("HEDI_PASSWORD_ITERATIONS", "180000"))
PASSWORD_SCHEME = "pbkdf2_sha256"
MIN_PASSWORD_LENGTH = int(os.getenv("HEDI_MIN_PASSWORD_LENGTH", "8"))
BOOTSTRAP_ADMIN_USER = os.getenv("HEDI_BOOTSTRAP_ADMIN_USER", "admin").strip()
BOOTSTRAP_ADMIN_HASH = os.getenv(
    "HEDI_BOOTSTRAP_ADMIN_HASH",
    "pbkdf2_sha256$180000$j3pt+dTfwUgP6VwHPPDNKA==$zojEXizGhZe8fEeCD655ThS8PIfIyz3jwgwWlUkS5hA=",
).strip()

app = FastAPI(title="TurboEDI Ingest API", version="0.2.0")

if not ALLOWED_ORIGINS:
    ALLOWED_ORIGINS = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if "*" in ALLOWED_ORIGINS else ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

db_pool: Optional[pool.SimpleConnectionPool] = None

@app.on_event("startup")
def run_startup_migrations() -> None:
    try:
        with get_db() as conn:
            ensure_core_ingest_tables(conn)
            ensure_import_core_columns(conn)
            ensure_import_job_ids(conn)
            ensure_import_uploaded_by(conn)
            ensure_app_users(conn)
            ensure_x12_addon_tables(conn)
    except Exception:
        logger.exception("Failed to run startup migrations")
        raise


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


def is_safe_username(value: str) -> bool:
    if not value or len(value) > 96:
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_@")
    return all(ch in allowed for ch in value)


def is_valid_role(role: str) -> bool:
    return role in {"view", "update", "create", "admin"}


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
    role = (row.get("role") or "").strip().lower()
    if role not in {"view", "update", "create", "admin"}:
        role = "view"
    allow_portal = bool(row.get("allow_portal", False))
    allow_admin = bool(row.get("allow_admin", False))
    if role == "admin":
        # Admin accounts should always retain full administrative and portal privileges
        # even if the stored flags drift. This guards the bootstrap admin as well as any
        # other "admin"-role users from being locked out of required features.
        allow_portal = True
        allow_admin = True
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
        "allow_admin": allow_admin,
        "created_at": _serialize_timestamp(row.get("created_at")),
        "updated_at": _serialize_timestamp(row.get("updated_at")),
    }


def require_secret(header_value: Optional[str] = Header(None, alias="X-HEDI-SECRET")):
    if not SHARED_SECRET:
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


def ensure_app_users(conn) -> None:
    logger.info("Ensuring app_users table exists")
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS app_users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('view','update','create','admin')),
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

        cur.execute(
            """
            SELECT 1
              FROM pg_constraint
             WHERE conname = 'app_users_role_check'
               AND conrelid = 'app_users'::regclass
            """
        )
        if cur.fetchone() is None:
            logger.info("Adding app_users_role_check constraint")
            cur.execute(
                "ALTER TABLE app_users ADD CONSTRAINT app_users_role_check CHECK (role IN ('view','update','create','admin'))"
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
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT username, role, allow_portal, allow_admin, created_at, updated_at
              FROM app_users
             WHERE username = %s
            """,
            (BOOTSTRAP_ADMIN_USER,),
        )
        record = cur.fetchone()
        if record:
            updates = []
            params: list[object] = []
            current_role = (record.get("role") or "").strip()
            if current_role != "admin":
                updates.append("role = %s")
                params.append("admin")
            if not record.get("allow_portal", False):
                updates.append("allow_portal = %s")
                params.append(True)
            if not record.get("allow_admin", False):
                updates.append("allow_admin = %s")
                params.append(True)
            if updates:
                if "updated_at" in record:
                    updates.append("updated_at = NOW()")
                cur.execute(
                    f"""
                    UPDATE app_users
                       SET {', '.join(updates)}
                     WHERE username = %s
                    """,
                    params + [BOOTSTRAP_ADMIN_USER],
                )
                logger.info(
                    "Elevated bootstrap admin account %s to full portal/admin access",
                    BOOTSTRAP_ADMIN_USER,
                )
                conn.commit()
            return
        logger.info("Seeding bootstrap admin account %s", BOOTSTRAP_ADMIN_USER)
        cur.execute(
            """
            INSERT INTO app_users (username, password_hash, role, allow_portal, allow_admin)
            VALUES (%s, %s, 'admin', TRUE, TRUE)
            """,
            (BOOTSTRAP_ADMIN_USER, BOOTSTRAP_ADMIN_HASH),
        )
    conn.commit()


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


def get_channel():
    params = pika.URLParameters(RABBITMQ_URL)
    connection = pika.BlockingConnection(params)
    ch = connection.channel()
    ch.queue_declare(queue=RMQ_QUEUE, durable=True)
    return connection, ch


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
    allow_admin: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class UserList(BaseModel):
    users: List[UserOut]


class UserCreate(BaseModel):
    username: str
    password: str
    role: str
    allow_portal: bool = True
    allow_admin: bool = False


class UserUpdate(BaseModel):
    password: Optional[str] = None
    role: str
    allow_portal: bool
    allow_admin: bool


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


class JobDetail(JobSummary):
    acknowledgements: List[Acknowledgement]


@app.post("/auth/login")
def api_login(payload: LoginRequest, _: None = Depends(require_secret)):
    username = (payload.username or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="username required")
    if not payload.password:
        raise HTTPException(status_code=400, detail="password required")
    with get_db() as conn:
        record = fetch_app_user(conn, username)
        if not record or not verify_password(payload.password, record.get("password_hash", "")):
            raise HTTPException(status_code=401, detail="invalid credentials")
        user = UserOut(**row_to_user(record))
        return LoginResponse(ok=True, user=user).model_dump()


@app.get("/auth/users/{username}")
def api_get_user(username: str, _: None = Depends(require_secret)):
    cleaned = (username or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="username required")
    with get_db() as conn:
        record = fetch_app_user(conn, cleaned)
        if not record:
            raise HTTPException(status_code=404, detail="user not found")
        return {"user": UserOut(**row_to_user(record)).model_dump()}


def list_users_impl() -> dict:
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
    users = [UserOut(**row_to_user(row)).model_dump() for row in rows]
    return {"users": users}


@app.get("/admin/users")
def api_list_users(_: None = Depends(require_secret)):
    return list_users_impl()


def create_user_impl(payload: UserCreate) -> dict:
    username = payload.username.strip()
    if not is_safe_username(username):
        raise HTTPException(status_code=400, detail="invalid username")
    role = payload.role.strip().lower()
    if not is_valid_role(role):
        raise HTTPException(status_code=400, detail="invalid role")
    password = payload.password.strip()
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail="password too short")
    allow_portal = bool(payload.allow_portal)
    allow_admin = bool(payload.allow_admin)
    if role == "admin":
        allow_portal = True
        allow_admin = True
    if not allow_portal and not allow_admin:
        raise HTTPException(status_code=400, detail="grant portal or admin access")
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
    return {"user": UserOut(**row_to_user(row)).model_dump()}


@app.post("/admin/users")
def api_create_user(payload: UserCreate, _: None = Depends(require_secret)):
    return create_user_impl(payload)


def update_user_impl(username: str, payload: UserUpdate) -> dict:
    cleaned = (username or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="username required")
    role = payload.role.strip().lower()
    if not is_valid_role(role):
        raise HTTPException(status_code=400, detail="invalid role")
    allow_portal = bool(payload.allow_portal)
    allow_admin = bool(payload.allow_admin)
    if role == "admin":
        allow_portal = True
        allow_admin = True
    if not allow_portal and not allow_admin:
        raise HTTPException(status_code=400, detail="grant portal or admin access")
    new_hash = None
    if payload.password is not None:
        pwd = payload.password.strip()
        if pwd and len(pwd) < MIN_PASSWORD_LENGTH:
            raise HTTPException(status_code=400, detail="password too short")
        if pwd:
            new_hash = hash_password(pwd)
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
    return {"user": UserOut(**row_to_user(row)).model_dump()}


@app.put("/admin/users/{username}")
def api_update_user(username: str, payload: UserUpdate, _: None = Depends(require_secret)):
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
    return {"ok": True}


@app.delete("/admin/users/{username}")
def api_delete_user(username: str, _: None = Depends(require_secret)):
    return delete_user_impl(username)


@app.get("/admin/api/users")
def api_list_users_admin(_: None = Depends(require_secret)):
    return list_users_impl()


@app.post("/admin/api/users")
def api_create_user_admin(payload: UserCreate, _: None = Depends(require_secret)):
    return create_user_impl(payload)


@app.put("/admin/api/users/{username}")
def api_update_user_admin(username: str, payload: UserUpdate, _: None = Depends(require_secret)):
    return update_user_impl(username, payload)


@app.delete("/admin/api/users/{username}")
def api_delete_user_admin(username: str, _: None = Depends(require_secret)):
    return delete_user_impl(username)


@app.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    uploaded_by: Optional[str] = Form(default=None),
    trading_partner_id: Optional[str] = Form(default=None),
):
    import_id: Optional[int] = None
    start_time = time.perf_counter()
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty file")

        job_uuid = uuid.uuid4()
        filename = file.filename or "upload.dat"
        size = len(content)

        logger.info(
            "Received upload filename=%s size=%s uploaded_by=%s partner=%s",
            filename,
            size,
            uploaded_by,
            trading_partner_id,
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
                        psycopg2.Binary(content),
                        "queued",
                    ),
                )
                import_id = cur.fetchone()[0]
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
                properties=pika.BasicProperties(delivery_mode=2),
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
):
    if not uploaded_by and not trading_partner_id:
        raise HTTPException(status_code=400, detail="Provide uploaded_by or trading_partner_id to search")

    query = [
        "SELECT i.job_id, i.filename, i.file_type, i.byte_size, i.status, i.created_at, i.processed_at,",
        "       i.uploaded_by, i.trading_partner_id, i.claims_count, i.order_lines_count,",
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
                claims_count=row.get("claims_count"),
                order_lines_count=row.get("order_lines_count"),
                ack_count=int(row.get("ack_count") or 0),
            )
        )
    return results


@app.get("/jobs/{job_id}", response_model=JobDetail)
async def job_detail(job_id: str):
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
        claims_count=base.get("claims_count"),
        order_lines_count=base.get("order_lines_count"),
        ack_count=int(ack_count_val) if ack_count_val is not None else len(acks),
        acknowledgements=acks,
    )
    return detail


@app.get("/jobs/{job_id}/acks", response_model=List[AcknowledgementSummary])
async def list_job_acks(job_id: str):
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
async def download_original(job_id: str):
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
    buffer = BytesIO(bytes(data))
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(buffer, media_type="application/octet-stream", headers=headers)


@app.get("/jobs/{job_id}/acks/{ack_id}/download")
async def download_ack(job_id: str, ack_id: int):
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
async def download_ack_archive(job_id: str):
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


@app.on_event("shutdown")
def close_pool():
    global db_pool
    if db_pool is not None:
        logger.info("Closing PostgreSQL connection pool")
        db_pool.closeall()
