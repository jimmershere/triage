import os, base64, json, uuid, logging
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


def get_channel():
    params = pika.URLParameters(RABBITMQ_URL)
    connection = pika.BlockingConnection(params)
    ch = connection.channel()
    ch.queue_declare(queue=RMQ_QUEUE, durable=True)
    return connection, ch


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


class JobDetail(JobSummary):
    claims_count: Optional[int]
    order_lines_count: Optional[int]
    acknowledgements: List[dict]


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
        "SELECT job_id, filename, file_type, byte_size, status, created_at, processed_at, uploaded_by, trading_partner_id",
        "FROM imports WHERE 1=1",
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
            )
        )
    return results


@app.get("/jobs/{job_id}", response_model=JobDetail)
async def job_detail(job_id: str):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT i.*, a.ack_type, a.content, a.created_at AS ack_created_at
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
    acks = []
    for row in rows:
        if row.get("ack_type"):
            acks.append(
                {
                    "ack_type": row["ack_type"],
                    "content": row["content"],
                    "created_at": row["ack_created_at"].isoformat() if row["ack_created_at"] else None,
                }
            )

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
        acknowledgements=acks,
    )
    return detail


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


@app.on_event("shutdown")
def close_pool():
    global db_pool
    if db_pool is not None:
        logger.info("Closing PostgreSQL connection pool")
        db_pool.closeall()
