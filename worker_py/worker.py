import base64
import decimal
import json
import logging
import os
import re
import time
import uuid
from contextlib import closing
from pathlib import Path
from contextlib import closing

import pika
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_batch

try:  # When running as part of the package
    from worker_py.translators import AckRecord, select_translator, TranslationOutcome
except ModuleNotFoundError:  # When executed from the worker directory directly
    from translators import AckRecord, select_translator, TranslationOutcome

import pika
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_batch

try:  # When running as part of the package
    from worker_py.translators import AckRecord, select_translator, TranslationOutcome
except ModuleNotFoundError:  # When executed from the worker directory directly
    from translators import AckRecord, select_translator, TranslationOutcome

import pika
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_batch

try:  # When running as part of the package
    from worker_py.translators import AckRecord, select_translator, TranslationOutcome
except ModuleNotFoundError:  # When executed from the worker directory directly
    from translators import AckRecord, select_translator, TranslationOutcome

import pika
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_batch

try:  # When running as part of the package
    from worker_py.translators import AckRecord, select_translator, TranslationOutcome
except ModuleNotFoundError:  # When executed from the worker directory directly
    from translators import AckRecord, select_translator, TranslationOutcome

load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("worker")

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://edi:edi@postgres:5432/edi")
ARCHIVE_DIR = os.getenv("ARCHIVE_DIR", "/archive")

def get_rmq_channel():
    """
    Connect to RabbitMQ using env vars and return (connection, channel).
    Retries until RabbitMQ is ready.
    """
    rmq_host  = os.environ.get("RMQ_HOST", "rabbitmq")
    rmq_port  = int(os.environ.get("RMQ_PORT", "5672"))
    rmq_user  = os.environ.get("RMQ_USER", "ediapp")
    rmq_pass  = os.environ.get("RMQ_PASS", "3wm078uu")
    rmq_vhost = os.environ.get("RMQ_VHOST", "/")
    rmq_queue = os.environ.get("RMQ_QUEUE", "edi_files")

    creds = pika.PlainCredentials(rmq_user, rmq_pass)

    params = pika.ConnectionParameters(
        host=rmq_host,
        port=rmq_port,
        virtual_host=rmq_vhost,
        credentials=creds,
        heartbeat=30,
        blocked_connection_timeout=300,
        # These two let pika retry the TCP connect step internally
        connection_attempts=12,
        retry_delay=5,
        client_properties={"connection_name": "hedi-worker"},
    )

    # Robust retry loop for when broker is still starting
    attempts = 0
    while True:
        attempts += 1
        try:
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            # Ensure queue exists & is durable
            channel.queue_declare(queue=rmq_queue, durable=True)
            # Publisher confirms are nice if you publish from this worker
            try:
                channel.confirm_delivery()
            except Exception:
                pass
            logging.info(
                "Connected to RabbitMQ %s:%s vhost=%s queue=%s as %s",
                rmq_host, rmq_port, rmq_vhost, rmq_queue, rmq_user
            )
            return connection, channel
        except Exception as e:
            wait = min(5 + attempts, 20)
            logging.warning("RabbitMQ not ready (%s). Retry in %ss...", repr(e), wait)
            time.sleep(wait)

def get_db():
    return psycopg2.connect(DATABASE_URL)


def ensure_core_ingest_tables(conn) -> None:
    logger.info("Ensuring core ingest tables exist (worker)")
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


def ensure_import_job_ids(conn) -> None:
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
        for import_id, in cur.fetchall():
            cur.execute(
                "UPDATE imports SET job_id = %s WHERE id = %s",
                (str(uuid.uuid4()), import_id),
            )

    with conn.cursor() as cur:
        cur.execute("ALTER TABLE imports ALTER COLUMN job_id SET NOT NULL")
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_imports_job_id ON imports(job_id)"
        )

    conn.commit()


def ensure_import_uploaded_by(conn) -> None:
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


def run_startup_migrations() -> None:
    with closing(get_db()) as conn:
        ensure_core_ingest_tables(conn)
        ensure_import_core_columns(conn)
        ensure_import_job_ids(conn)
        ensure_import_uploaded_by(conn)

def detect_format(text: str) -> str:
    head = text.strip()[:3].upper()
    if head == "ISA":
        return "X12_837_or_other"
    if text.strip().startswith("UNB") or text.strip().startswith("UNH"):
        return "EDIFACT_ORDERS_or_other"
    return "UNKNOWN"

def resolve_job_uuid(job_id: str | None) -> uuid.UUID:
    """Convert an optional job id to a UUID, generating a new value as needed."""
    try:
        if job_id:
            return uuid.UUID(str(job_id))
    except Exception:
        logger.debug("job_id %s was not a UUID; generating a new identifier", job_id)
    return uuid.uuid4()


def ensure_import_record(cur, payload: dict, filename: str, ftype: str, size: int, raw: bytes) -> tuple[int, uuid.UUID]:
    """Create or update the imports row attached to the incoming payload."""
    import_id = payload.get("import_id")
    uploaded_by = payload.get("uploaded_by")
    trading_partner_id = payload.get("trading_partner_id")

    if import_id is not None:
        cur.execute(
            """
            UPDATE imports
               SET file_type = %s,
                   byte_size = %s,
                   status = 'processing',
                   processed_at = NOW(),
                   filename = COALESCE(%s, filename),
                   uploaded_by = COALESCE(%s, uploaded_by),
                   trading_partner_id = COALESCE(%s, trading_partner_id)
             WHERE id = %s
        RETURNING id, job_id 
            """,
            (ftype, size, filename, uploaded_by, trading_partner_id, import_id),
        )
        row = cur.fetchone()
        if row:
            return row[0], uuid.UUID(str(row[1])) 
        logger.warning("Import id %s was not found; inserting a fresh row", import_id)

    job_uuid = resolve_job_uuid(payload.get("job_id"))
    cur.execute(
        """
        INSERT INTO imports (
            job_id,
            filename,
            file_type,
            byte_size,
            original_content,
            status,
            created_at,
            processed_at,
            uploaded_by,
            trading_partner_id
        )
        VALUES (%s,%s,%s,%s,%s,'processing',NOW(),NOW(),%s,%s)
       RETURNING id, job_id 
        """,
        (
            str(job_uuid),
            filename,
            ftype,
            size,
            psycopg2.Binary(raw),
            uploaded_by,
            trading_partner_id,
        ),
    )
    row = cur.fetchone()
    return row[0], uuid.UUID(str(row[1]))


def persist_ack(cur, import_id: int, ack_type: str, ack_content: str | None) -> None:
    cur.execute(
        "INSERT INTO acks (import_id, ack_type, content) VALUES (%s,%s,%s)",
        (import_id, ack_type, ack_content),
    )


def safe_component(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", value.strip())
    return cleaned or fallback


def ack_file_extension(ack_type: str) -> str:
    upper = ack_type.upper()
    if upper == "999":
        return ".999"
    if upper == "277CA":
        return ".277"
    if "CONTRL" in upper:
        return ".contrl"
    return ".txt"


def fanout_ack_files(uploaded_by: str | None, ack_type: str, ack_content: str | None, job_uuid: uuid.UUID, trading_partner_id: str | None) -> None:
    if not ack_content:
        return
    safe_login = safe_component(uploaded_by, "general")
    safe_partner = safe_component(trading_partner_id, "partner")
    timestamp = time.strftime("%Y%m%d%H%M%S")
    ext = ack_file_extension(ack_type)
    base_name = f"{timestamp}_{safe_partner}_{job_uuid.hex[:8]}_{ack_type.upper()}{ext}"
    mailbox_target = Path(ARCHIVE_DIR) / "mailboxes" / safe_login / base_name
    drop_target = Path(ARCHIVE_DIR) / "drop" / base_name
    for target in (mailbox_target, drop_target):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(ack_content, encoding="utf-8")
        except Exception:
            logger.exception("Failed to write acknowledgement file %s", target)


def process_payload(payload: dict):
    overall_start = time.perf_counter()
    job_hint = payload.get("job_id") or "pending"
    raw = base64.b64decode(payload["data_b64"])
    text = raw.decode("utf-8", errors="replace")
    ftype = detect_format(text)
    size = len(raw)
    filename = payload.get("filename", "upload.dat")

    decode_duration = time.perf_counter() - overall_start
    logger.info(
        "Worker received payload job_id=%s filename=%s size=%s format=%s (decode %.3fs)",
        job_hint,
        filename,
        size,
        ftype,
        decode_duration,
    )

    claims_count: int | None = None
    order_lines_count: int | None = None
    ack_records: list[AckRecord] = []
    job_uuid: uuid.UUID | None = None

    with get_db() as conn:
        with conn.cursor() as cur:
            ensure_start = time.perf_counter()
            import_id, job_uuid = ensure_import_record(
                cur, payload, filename, ftype, size, raw
            )
            logger.info(
                "Ensured import record id=%s job_id=%s in %.3fs",
                import_id,
                job_uuid,
                time.perf_counter() - ensure_start,
            )

            translator = select_translator(ftype, text)
            translation: TranslationOutcome | None = None
            if translator:
                logger.info("Using translator %s for job %s", translator.name, job_uuid)
                try:
                    translation = translator.translate(
                        text=text,
                        job_uuid=job_uuid,
                        trading_partner_id=payload.get("trading_partner_id"),
                        uploaded_by=payload.get("uploaded_by"),
                        filename=filename,
                    )
                    ftype = translation.file_type or ftype
                except Exception:
                    logger.exception("Translator %s failed; falling back", translator.name)

            if translation is None:
                translation = TranslationOutcome(
                    file_type=ftype,
                    acknowledgements=[AckRecord("NOTICE", "Translator unavailable")],
                )

            claims = list(translation.claims)
            if claims:
                parse_start = time.perf_counter()
                claim_rows = []
                for claim in claims:
                    amount = claim.get("amount")
                    if amount is not None:
                        try:
                            amount = decimal.Decimal(str(amount))
                        except Exception:
                            amount = None
                    claim_rows.append(
                        (
                            import_id,
                            claim.get("claim_id"),
                            amount,
                            claim.get("raw"),
                        )
                    )
                claims_count = len(claim_rows)
                execute_batch(
                    cur,
                    "INSERT INTO claims (import_id, claim_id, amount, raw_claim) VALUES (%s,%s,%s,%s)",
                    claim_rows,
                    page_size=500,
                )
                logger.info(
                    "Inserted %s claims for import %s in %.3fs",
                    claims_count,
                    import_id,
                    time.perf_counter() - parse_start,
                )

            lines = list(translation.order_lines)
            if lines:
                parse_start = time.perf_counter()
                line_rows = []
                for line in lines:
                    qty = line.get("qty")
                    if qty is not None:
                        try:
                            qty = decimal.Decimal(str(qty))
                        except Exception:
                            qty = None
                    price = line.get("price")
                    if price is not None:
                        try:
                            price = decimal.Decimal(str(price))
                        except Exception:
                            price = None
                    line_rows.append(
                        (
                            import_id,
                            line.get("line_no"),
                            line.get("item_id"),
                            qty,
                            price,
                            line.get("raw"),
                        )
                    )
                order_lines_count = len(line_rows)
                execute_batch(
                    cur,
                    "INSERT INTO order_lines (import_id, line_no, item_id, qty, price, raw_line) VALUES (%s,%s,%s,%s,%s,%s)",
                    line_rows,
                    page_size=500,
                )
                logger.info(
                    "Inserted %s order lines for import %s in %.3fs",
                    order_lines_count,
                    import_id,
                    time.perf_counter() - parse_start,
                )

            ack_records = list(translation.acknowledgements)
            if not ack_records:
                ack_records = [AckRecord("NOTICE", "No acknowledgements generated")]
            logger.info(
                "Generated acknowledgements %s for job %s",
                [ack.ack_type for ack in ack_records],
                job_uuid,
            )

            update_start = time.perf_counter()
            cur.execute(
                """
                UPDATE imports
                   SET claims_count = %s,
                       order_lines_count = %s,
                       status = 'processed',
                       processed_at = NOW()
                 WHERE id = %s
                """,
                (claims_count, order_lines_count, import_id),
            )
            logger.info(
                "Updated import %s status in %.3fs",
                import_id,
                time.perf_counter() - update_start,
            )
            persist_start = time.perf_counter()
            for ack in ack_records:
                persist_ack(cur, import_id, ack.ack_type, ack.content)
            logger.info(
                "Persisted %s acknowledgement rows for import %s in %.3fs",
                len(ack_records),
                import_id,
                time.perf_counter() - persist_start,
            )

        commit_start = time.perf_counter()
        conn.commit()
    logger.info(
        "Committed database work for job %s import %s in %.3fs",
        job_uuid,
        import_id,
        time.perf_counter() - commit_start,
    )

    if job_uuid is None:
        job_uuid = resolve_job_uuid(payload.get("job_id"))

    fanout_start = time.perf_counter()
    for ack in ack_records:
        fanout_ack_files(
            payload.get("uploaded_by"),
            ack.ack_type,
            ack.content,
            job_uuid,
            payload.get("trading_partner_id"),
        )
    if ack_records:
        logger.info(
            "Wrote acknowledgement files %s for job %s in %.3fs",
            [ack.ack_type for ack in ack_records],
            job_uuid,
            time.perf_counter() - fanout_start,
        )

    total_duration = time.perf_counter() - overall_start
    logger.info(
        "Finished processing job %s import %s in %.3fs",
        job_uuid,
        import_id,
        total_duration,
    )

    return ftype, size, import_id

def main():
    run_startup_migrations()

    conn, ch = get_rmq_channel()
    rmq_queue = os.environ.get("RMQ_QUEUE", "edi_files")
    acks_queue = os.environ.get("RMQ_ACKS_QUEUE", "acks")

    # Ensure both queues exist & are durable
    ch.queue_declare(queue=rmq_queue, durable=True)
    ch.queue_declare(queue=acks_queue, durable=True)

    logger.info("Worker connected. Consuming from %s, publishing acks to %s", rmq_queue, acks_queue)

    def cb(ch_, method, properties, body):
        try:
            payload = json.loads(body.decode("utf-8"))
            ftype, size, import_id = process_payload(payload)
            logger.info(
                "Processed %s bytes as %s for file %s (import id %s)",
                size,
                ftype,
                payload.get("filename"),
                import_id,
            )
            # publish a simple ack message
            ch_.basic_publish(
                exchange="",
                routing_key=acks_queue,
                body=json.dumps({
                    "job_id": payload.get("job_id"),
                    "filename": payload.get("filename"),
                    "file_type": ftype,
                    "import_id": import_id,
                }).encode("utf-8"),
                properties=pika.BasicProperties(delivery_mode=2),
            )
            ch_.basic_ack(delivery_tag=method.delivery_tag)
        except Exception:
            logger.exception("Failed to process message; rejecting")
            ch_.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    ch.basic_qos(prefetch_count=10)
    ch.basic_consume(queue=rmq_queue, on_message_callback=cb, auto_ack=False)

    try:
        ch.start_consuming()
    except KeyboardInterrupt:
        logger.info("Stopping...")
    finally:
        try:
            ch.close()
            conn.close()
        except:
            pass

if __name__ == "__main__":
    main()
