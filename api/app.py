import os, base64, json, uuid, logging, zipfile
from contextlib import contextmanager
from io import BytesIO
from typing import List, Optional

import pika
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Query
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


@app.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    uploaded_by: Optional[str] = Form(default=None),
    trading_partner_id: Optional[str] = Form(default=None),
):
    import_id: Optional[int] = None
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty file")

        job_uuid = uuid.uuid4()
        filename = file.filename or "upload.dat"
        size = len(content)

        with get_db() as conn:
            with conn.cursor() as cur:
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
            ch.basic_publish(
                exchange="",
                routing_key=RMQ_QUEUE,
                body=payload_bytes,
                properties=pika.BasicProperties(delivery_mode=2),
            )
        finally:
            connection.close()

        logger.info(
            "Enqueued job %s (%s bytes) for %s [uploaded_by=%s partner=%s]",
            job_uuid,
            size,
            filename,
            uploaded_by,
            trading_partner_id,
        )
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
