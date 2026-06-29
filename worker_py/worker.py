import base64
import decimal
import json
import logging
import os
import re
import tempfile
import time
import uuid
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

import pika
import psycopg2
from dotenv import load_dotenv
try:
    from security import phi_crypto
except ModuleNotFoundError:  # api image bundles worker_py as a package
    from worker_py.security import phi_crypto
from psycopg2.extras import Json, execute_batch

try:  # When running as part of the package
    from worker_py.tools_x12_cms_harness import parse_x12, run_harness
    from worker_py.translators import AckRecord, select_translator, TranslationOutcome
    from worker_py.file_profiler import profile_file, enrich_with_harness
    from worker_py.complexity_scorer import score_file
    from worker_py.routing_engine import route_file, TIER_LABELS
    from worker_py.tier_executor import execute_tier, TierResult
    from worker_py.turbo_pipeline import run_pipeline as turbo_run_pipeline
    from worker_py import audit_log
except ModuleNotFoundError:  # When executed from the worker directory directly
    from tools_x12_cms_harness import parse_x12, run_harness
    from translators import AckRecord, select_translator, TranslationOutcome
    from file_profiler import profile_file, enrich_with_harness
    from complexity_scorer import score_file
    from routing_engine import route_file, TIER_LABELS
    from tier_executor import execute_tier, TierResult
    from turbo_pipeline import run_pipeline as turbo_run_pipeline
    import audit_log

load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
# Workstream 3: PHI-safe structured JSON logging with a correlation id that is
# continued from the API across the RabbitMQ hop (see audit_log).
audit_log.configure_structured_logging("triage-worker", level=LOG_LEVEL)
logger = logging.getLogger("worker")
CORRELATION_HEADER = audit_log.CORRELATION_HEADER

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://edi:edi@postgres:5432/edi?sslmode=require"
)
ARCHIVE_DIR = os.getenv("ARCHIVE_DIR", "/archive")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")
# Toggle the Phase 1-3 turbo pipeline (validation + scrubbing) on each import.
# Disabled defensively if a deployment hits an unexpected issue — the legacy
# harness + routing flow above is unaffected either way.
TURBO_PIPELINE_ENABLED = os.getenv("TURBO_PIPELINE_ENABLED", "1") not in ("0", "false", "no")

# --- Sharded swarm path for large multipart EDI payloads -------------------
# When a payload is at least TURBO_SWARM_MIN_BYTES, route the turbo
# validation/scrubbing through swarms.SwarmCoordinator so independent ST
# transactions (and claim batches) fan out across an engine pool instead of a
# single pass. Falls back to the single-pass pipeline for smaller files or on
# any error so ingest is never blocked.
#
# Defaults OFF: the swarm path is opt-in. The live deployment leaves it disabled
# (docker-compose.override.yml sets TURBO_SWARM_ENABLED=0 — "Swarm path left
# off") in favour of horizontal worker scaling, so the default-off here keeps
# behaviour identical while preserving the capability for explicit opt-in.
TURBO_SWARM_ENABLED = os.getenv("TURBO_SWARM_ENABLED", "0") not in ("0", "false", "no")
TURBO_SWARM_MIN_BYTES = int(os.getenv("TURBO_SWARM_MIN_BYTES", str(10 * 1024 * 1024)))
TURBO_SWARM_POOL = os.getenv("TURBO_SWARM_POOL", "process")
TURBO_SWARM_WORKERS = int(os.getenv("TURBO_SWARM_WORKERS", "8"))


def _run_turbo(text):
    """Run turbo validation/scrub; swarm large multipart payloads."""
    try:
        size = len(text.encode("utf-8", "ignore"))
    except Exception:
        size = len(text)
    if TURBO_SWARM_ENABLED and size >= TURBO_SWARM_MIN_BYTES:
        try:
            from swarms import EnginePool, SwarmCoordinator
            coord = SwarmCoordinator(
                thread_pool=EnginePool(mode=TURBO_SWARM_POOL, max_workers=TURBO_SWARM_WORKERS),
                process_pool=EnginePool(mode=TURBO_SWARM_POOL, max_workers=TURBO_SWARM_WORKERS),
            )
            res = coord.run(text, scrub=True, to_fhir=False, generate_acks=False)
            logger.info(
                "Turbo SWARM path: %d bytes shards=%s workers=%s pool=%s",
                size, getattr(res, "shard_count", "?"),
                getattr(res, "parallel_workers", "?"), TURBO_SWARM_POOL,
            )
            return res
        except Exception:
            logger.exception("Swarm path failed; falling back to single-pass turbo")
    return turbo_run_pipeline(text, scrub=True)


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
        heartbeat=600,
        blocked_connection_timeout=300,
        # These two let pika retry the TCP connect step internally
        connection_attempts=12,
        retry_delay=5,
        client_properties={"connection_name": "triage-worker"},
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
                validation_status TEXT NOT NULL DEFAULT 'pending',
                validation_report_json JSONB,
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
                raw_claim TEXT,
                cms_projection_json JSONB,
                claim_status_code TEXT
            )
            """
        )
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


def ensure_routing_tables(conn) -> None:
    """Create the routing_decisions and tier_executions tables."""
    logger.info("Ensuring routing + tier execution tables exist (worker)")
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS routing_decisions (
                id SERIAL PRIMARY KEY,
                import_id INTEGER REFERENCES imports(id) ON DELETE CASCADE,
                routing_decision_id TEXT NOT NULL,
                file_id TEXT NOT NULL,
                tier INTEGER NOT NULL DEFAULT 0,
                tier_label TEXT NOT NULL DEFAULT 'deterministic_fast_path',
                score_total NUMERIC(6,2) NOT NULL DEFAULT 0,
                gate_triggers JSONB,
                explanation TEXT,
                policy_version TEXT NOT NULL DEFAULT '1.0.0',
                scorer_version TEXT NOT NULL DEFAULT '1.0.0',
                file_profile_json JSONB,
                score_detail_json JSONB,
                decided_at TIMESTAMP NOT NULL DEFAULT NOW(),
                decision_duration_ms NUMERIC(8,2)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_routing_decisions_import_id ON routing_decisions(import_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_routing_decisions_tier ON routing_decisions(tier)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tier_executions (
                id SERIAL PRIMARY KEY,
                import_id INTEGER REFERENCES imports(id) ON DELETE CASCADE,
                routing_decision_id TEXT,
                tier INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                processing_ms NUMERIC(10,2),
                flags JSONB,
                recommendations JSONB,
                ai_analyses JSONB,
                swarm_task_count INTEGER DEFAULT 0,
                supervisor_verdict TEXT,
                error TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_tier_executions_import_id ON tier_executions(import_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_tier_executions_status ON tier_executions(status)"
        )
    conn.commit()


def run_startup_migrations() -> None:
    attempts = 0
    while True:
        attempts += 1
        try:
            with closing(get_db()) as conn:
                ensure_core_ingest_tables(conn)
                ensure_import_core_columns(conn)
                ensure_import_job_ids(conn)
                ensure_import_uploaded_by(conn)
                ensure_validation_columns(conn)
                ensure_routing_tables(conn)
            return
        except Exception as e:
            wait = min(3 + attempts * 2, 30)
            logger.warning("DB not ready for migrations (%s). Retry %d in %ds...", repr(e), attempts, wait)
            time.sleep(wait)


def is_x12_837(text: str) -> bool:
    try:
        segments = parse_x12(text)
    except Exception:
        return False
    return any(seg and seg[0].upper() == 'ST' and len(seg) > 1 and seg[1] == '837' for seg in segments)


def run_x12_harness(text: str, filename: str):
    suffix = Path(filename or 'upload.x12').suffix or '.x12'
    with tempfile.NamedTemporaryFile('w', suffix=suffix, delete=True, encoding='utf-8') as tmp:
        tmp.write(text)
        tmp.flush()
        return run_harness(Path(tmp.name))


def persist_audit_event(cur, import_id: int, event_type: str, detail: dict | list | None) -> None:
    cur.execute(
        "INSERT INTO audit_events (import_id, event_type, detail_json) VALUES (%s,%s,%s)",
        (import_id, event_type, Json(detail) if detail is not None else None),
    )

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
            psycopg2.Binary(phi_crypto.seal_bytes(raw)),
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
    validation_status = 'pending'
    validation_report_json = None
    harness_report = None

    # --- Complexity-Aware Routing: profile the file before processing ---
    profile_start = time.perf_counter()
    file_profile = profile_file(
        text,
        file_id=payload.get("job_id") or job_hint,
        partner_id=payload.get("trading_partner_id"),
        trading_partner_id=payload.get("trading_partner_id"),
        byte_size=size,
    )
    logger.info(
        "Profiled file %s: standard=%s, segments=%d, tx=%d (%.3fs)",
        file_profile.file_id,
        file_profile.document_standard,
        file_profile.segment_count,
        file_profile.transaction_count,
        time.perf_counter() - profile_start,
    )

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
            if ftype.startswith('X12') and is_x12_837(text):
                try:
                    harness_report = run_x12_harness(text, filename)
                    validation_status = 'valid' if harness_report.valid else 'invalid'
                    validation_report_json = [asdict(issue) for issue in harness_report.issues[:5]]
                    claims_count = harness_report.claim_count
                    persist_audit_event(
                        cur,
                        import_id,
                        'validation_completed',
                        {
                            'validation_status': validation_status,
                            'claim_count': harness_report.claim_count,
                            'issues_preview': validation_report_json,
                        },
                    )
                except Exception as exc:
                    logger.exception('X12 CMS harness failed for import %s', import_id)
                    validation_status = 'error'
                    validation_report_json = [
                        {
                            'severity': 'error',
                            'code': 'HARNESS_ERROR',
                            'message': str(exc),
                        }
                    ]
                    persist_audit_event(
                        cur,
                        import_id,
                        'validation_error',
                        {'message': str(exc)},
                    )

            # --- Phase 1-3 turbo pipeline: SNIP 1-7 validation + CMS scrubbing ---
            # Runs alongside the legacy 837 harness above and never blocks it.
            # Captured as audit events so the routing/tier flow below is unaffected.
            if TURBO_PIPELINE_ENABLED and ftype.startswith('X12'):
                try:
                    turbo_start = time.perf_counter()
                    turbo = _run_turbo(text)
                    persist_audit_event(
                        cur, import_id, 'turbo_validation',
                        {
                            'transaction_set': turbo.transaction_set,
                            'implementation_version': turbo.implementation_version,
                            'valid': turbo.valid,
                            'error_count': turbo.validation.get('error_count'),
                            'warning_count': turbo.validation.get('warning_count'),
                            'claim_count': turbo.validation.get('claim_count'),
                            'snip_summary': turbo.validation.get('snip_summary'),
                            'issues_preview': turbo.validation.get('issues', [])[:5],
                        },
                    )
                    if turbo.scrubbing is not None:
                        persist_audit_event(
                            cur, import_id, 'turbo_scrubbing',
                            {
                                'clean': turbo.scrubbing.get('clean'),
                                'finding_count': turbo.scrubbing.get('finding_count'),
                                'deny_count': turbo.scrubbing.get('deny_count'),
                                'review_count': turbo.scrubbing.get('review_count'),
                                'category_summary': turbo.scrubbing.get('category_summary'),
                                'findings_preview': turbo.scrubbing.get('findings', [])[:5],
                            },
                        )
                    logger.info(
                        "Turbo pipeline for import %s: txn=%s valid=%s scrub_clean=%s (%.3fs)",
                        import_id,
                        turbo.transaction_set,
                        turbo.valid,
                        turbo.scrubbing_clean,
                        time.perf_counter() - turbo_start,
                    )
                except Exception as exc:
                    logger.exception('Turbo pipeline failed for import %s', import_id)
                    persist_audit_event(
                        cur, import_id, 'turbo_pipeline_error',
                        {'message': str(exc)},
                    )

            # --- Complexity-Aware Routing: enrich, score, and route ---
            enrich_with_harness(file_profile, harness_report)
            complexity_score = score_file(file_profile)
            routing_decision = route_file(file_profile, complexity_score)

            logger.info(
                "Routing decision for job %s: tier=%d (%s), score=%.1f, gates=%s",
                job_uuid,
                routing_decision.tier,
                routing_decision.tier_label,
                routing_decision.score_total,
                routing_decision.gate_triggers or "none",
            )

            # Persist routing decision for audit trail
            cur.execute(
                """
                INSERT INTO routing_decisions (
                    import_id, routing_decision_id, file_id,
                    tier, tier_label, score_total,
                    gate_triggers, explanation,
                    policy_version, scorer_version,
                    file_profile_json, score_detail_json,
                    decided_at, decision_duration_ms
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    import_id,
                    routing_decision.routing_decision_id,
                    routing_decision.file_id,
                    routing_decision.tier,
                    routing_decision.tier_label,
                    routing_decision.score_total,
                    Json(routing_decision.gate_triggers),
                    routing_decision.explanation,
                    routing_decision.policy_version,
                    routing_decision.scorer_version,
                    Json(file_profile.to_dict()),
                    Json(complexity_score.to_dict()),
                    routing_decision.decided_at,
                    routing_decision.decision_duration_ms,
                ),
            )
            persist_audit_event(
                cur,
                import_id,
                'routing_decision',
                routing_decision.to_dict(),
            )

            # --- Tier Execution: run tier-specific processing ---
            tier_result = execute_tier(
                routing_decision.tier,
                file_profile,
                complexity_score,
                routing_decision,
                text,
                ollama_url=OLLAMA_URL,
                ollama_model=OLLAMA_MODEL,
            )

            # Persist tier execution result
            cur.execute(
                """
                INSERT INTO tier_executions (
                    import_id, routing_decision_id, tier, status,
                    processing_ms, flags, recommendations,
                    ai_analyses, swarm_task_count, supervisor_verdict, error
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    import_id,
                    routing_decision.routing_decision_id,
                    tier_result.tier,
                    tier_result.status,
                    tier_result.processing_ms,
                    Json(tier_result.flags),
                    Json(tier_result.recommendations),
                    Json(tier_result.ai_analyses) if tier_result.ai_analyses else None,
                    tier_result.swarm_task_count,
                    tier_result.supervisor_verdict,
                    tier_result.error,
                ),
            )
            persist_audit_event(
                cur,
                import_id,
                'tier_execution',
                tier_result.to_dict(),
            )

            logger.info(
                "Tier %d execution for job %s: status=%s, verdict=%s, flags=%s",
                tier_result.tier, job_uuid, tier_result.status,
                tier_result.supervisor_verdict, tier_result.flags,
            )

            # If tier execution parked the file, skip automated translation
            if tier_result.status == "parked":
                logger.warning(
                    "File %s PARKED by tier %d execution — skipping auto-processing",
                    job_uuid, tier_result.tier,
                )
                cur.execute(
                    """
                    UPDATE imports
                       SET status = 'parked',
                           validation_status = %s,
                           validation_report_json = %s,
                           processed_at = NOW()
                     WHERE id = %s
                    """,
                    (
                        validation_status,
                        Json(validation_report_json) if validation_report_json else None,
                        import_id,
                    ),
                )
                persist_audit_event(
                    cur, import_id, 'import_parked',
                    {
                        'reason': 'tier_execution_parked',
                        'tier': tier_result.tier,
                        'flags': tier_result.flags,
                        'supervisor_verdict': tier_result.supervisor_verdict,
                    },
                )
                conn.commit()
                total_duration = time.perf_counter() - overall_start
                logger.info(
                    "PARKED job %s import %s in %.3fs — requires human review",
                    job_uuid, import_id, total_duration,
                )
                return ftype, size, import_id

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
                projection_by_claim_id: dict[str | None, list[dict]] = {}
                if harness_report is not None:
                    for projection in harness_report.claims:
                        projection_by_claim_id.setdefault(projection.claim_id, []).append(asdict(projection))
                claim_rows = []
                for claim in claims:
                    amount = claim.get("amount")
                    if amount is not None:
                        try:
                            amount = decimal.Decimal(str(amount))
                        except Exception:
                            amount = None
                    claim_id = claim.get("claim_id")
                    projection_json = None
                    if claim_id in projection_by_claim_id and projection_by_claim_id[claim_id]:
                        projection_json = projection_by_claim_id[claim_id].pop(0)
                    claim_rows.append(
                        (
                            import_id,
                            claim_id,
                            amount,
                            phi_crypto.seal_text(claim.get("raw")),
                            Json(projection_json) if projection_json is not None else None,
                            validation_status if harness_report is not None else None,
                        )
                    )
                claims_count = harness_report.claim_count if harness_report is not None else len(claim_rows)
                execute_batch(
                    cur,
                    "INSERT INTO claims (import_id, claim_id, amount, raw_claim, cms_projection_json, claim_status_code) VALUES (%s,%s,%s,%s,%s,%s)",
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
                       validation_status = %s,
                       validation_report_json = %s,
                       status = 'processed',
                       processed_at = NOW()
                 WHERE id = %s
                """,
                (
                    claims_count,
                    order_lines_count,
                    validation_status,
                    Json(validation_report_json) if validation_report_json is not None else None,
                    import_id,
                ),
            )
            logger.info(
                "Updated import %s status in %.3fs",
                import_id,
                time.perf_counter() - update_start,
            )
            persist_audit_event(
                cur,
                import_id,
                'import_processed',
                {
                    'status': 'processed',
                    'validation_status': validation_status,
                    'claims_count': claims_count,
                    'order_lines_count': order_lines_count,
                },
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

def bootstrap_codeset_registry():
    """Activate the effective-dated code-set registry (Workstream 2).

    Loads the bundled seed versions into the process-wide registry so SNIP type 5
    and CMS scrubbing resolve external codes by the claim's service date. When a
    DB is reachable the persisted versions take precedence. Best-effort: a
    failure leaves validation to fall back to structural/format checks.
    """
    try:
        from validation.codesets.loader_service import load_seed_versions
        from validation.codesets.registry import set_active_registry

        registry = load_seed_versions()
        try:
            from validation.codesets.db import (
                ensure_codeset_tables,
                load_active_registry,
                persist_version,
            )

            with closing(get_db()) as conn:
                ensure_codeset_tables(conn)
                for codeset in registry.codesets():
                    for version in registry.versions(codeset):
                        persist_version(conn, version)
                registry = load_active_registry(conn)
        except Exception:
            logger.warning("Code-set DB persistence unavailable; using seed registry", exc_info=True)
        set_active_registry(registry)
        logger.info("Code-set registry active: %s", registry.codesets())
    except Exception:
        logger.exception("Failed to bootstrap code-set registry")


def handle_ack_generate_message(payload: dict) -> dict:
    """Build the configured acknowledgements for an ``ack.generate`` message."""
    from validation.acks import handle_ack_generate

    return handle_ack_generate(payload)


def main():
    run_startup_migrations()
    bootstrap_codeset_registry()

    conn, ch = get_rmq_channel()
    rmq_queue = os.environ.get("RMQ_QUEUE", "edi_files")
    acks_queue = os.environ.get("RMQ_ACKS_QUEUE", "acks")
    ack_generate_queue = os.environ.get("RMQ_ACK_GENERATE_QUEUE", "ack.generate")

    # Ensure queues exist & are durable
    ch.queue_declare(queue=rmq_queue, durable=True)
    ch.queue_declare(queue=acks_queue, durable=True)
    ch.queue_declare(queue=ack_generate_queue, durable=True)

    logger.info("Worker connected. Consuming from %s, publishing acks to %s", rmq_queue, acks_queue)

    def cb(ch_, method, properties, body):
        # Continue the correlation/lineage id started by the API. Prefer the
        # explicit payload field, fall back to the AMQP property/header, then to
        # the job id, so the chain is never silently broken across the hop.
        payload = {}
        token = None
        try:
            payload = json.loads(body.decode("utf-8"))
            header_cid = None
            if properties is not None:
                header_cid = getattr(properties, "correlation_id", None)
                if not header_cid and getattr(properties, "headers", None):
                    header_cid = properties.headers.get(CORRELATION_HEADER)
            correlation_id = (
                payload.get("correlation_id") or header_cid or payload.get("job_id")
            )
            token = audit_log.bind_correlation_id(correlation_id)

            audit_log.log_event(
                logger,
                "worker_message_received",
                job_id=payload.get("job_id"),
                trading_partner_id=payload.get("trading_partner_id"),
            )
            ftype, size, import_id = process_payload(payload)
            audit_log.log_event(
                logger,
                "worker_message_processed",
                job_id=payload.get("job_id"),
                import_id=import_id,
                file_type=ftype,
                byte_size=size,
            )
            # publish a simple ack message, propagating the correlation id on
            # both the body and the AMQP properties for the next hop.
            ch_.basic_publish(
                exchange="",
                routing_key=acks_queue,
                body=json.dumps({
                    "job_id": payload.get("job_id"),
                    "filename": payload.get("filename"),
                    "file_type": ftype,
                    "import_id": import_id,
                    "correlation_id": audit_log.get_correlation_id(),
                }).encode("utf-8"),
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    correlation_id=audit_log.get_correlation_id(),
                    headers={CORRELATION_HEADER: audit_log.get_correlation_id()},
                ),
            )
            ch_.basic_ack(delivery_tag=method.delivery_tag)
        except Exception:
            logger.exception("Failed to process message; rejecting")
            ch_.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        finally:
            if token is not None:
                audit_log.reset_correlation_id(token)

    def ack_cb(ch_, method, properties, body):
        try:
            payload = json.loads(body.decode("utf-8"))
            result = handle_ack_generate_message(payload)
            ch_.basic_publish(
                exchange="",
                routing_key=acks_queue,
                body=json.dumps(result).encode("utf-8"),
                properties=pika.BasicProperties(delivery_mode=2),
            )
            logger.info(
                "Generated %s ack(s) [%s] for ack.generate request",
                list(result.get("acknowledgments", {}).keys()),
                result.get("ack_profile"),
            )
            ch_.basic_ack(delivery_tag=method.delivery_tag)
        except Exception:
            logger.exception("Failed to handle ack.generate message; rejecting")
            ch_.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    ch.basic_qos(prefetch_count=10)
    ch.basic_consume(queue=rmq_queue, on_message_callback=cb, auto_ack=False)
    ch.basic_consume(queue=ack_generate_queue, on_message_callback=ack_cb, auto_ack=False)

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
