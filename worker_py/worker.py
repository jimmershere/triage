import os, json, base64, logging, time, re, decimal, uuid
import pika, psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv
from pathlib import Path
from contextlib import closing

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

    with conn.cursor() as cur:
        cur.execute("UPDATE imports SET status = 'queued' WHERE status IS NULL")
        cur.execute("ALTER TABLE imports ALTER COLUMN status SET DEFAULT 'queued'")
        cur.execute("ALTER TABLE imports ALTER COLUMN status SET NOT NULL")
        cur.execute("UPDATE imports SET file_type = 'unknown' WHERE file_type IS NULL")
        cur.execute("ALTER TABLE imports ALTER COLUMN file_type SET DEFAULT 'unknown'")
        cur.execute("ALTER TABLE imports ALTER COLUMN file_type SET NOT NULL")

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

def parse_x12_837(text: str):
    # Minimal segmentation: try to infer delimiters from ISA if present
    seg_term = "~"
    elem_sep = "*"
    comp_sep = ":"
    if text.startswith("ISA"):
        # ISA defines element separator at position 4, composite at ISA16, segment at last char of ISA line
        # We'll attempt a simple heuristic: the first 3 chars are "ISA", 4th is element sep
        elem_sep = text[3]
        # Segment terminator: find first occurrence of "IEA" line end char by scanning
        # Fallback to ~ if not found
        possible_terms = ['~', '\n', '\r']
        for ch in possible_terms:
            if f"IEA{elem_sep}" in text and ch in text.split(f"IEA{elem_sep}",1)[-1]:
                seg_term = ch
                break
    segments = [s for s in text.replace("\r","").split(seg_term) if s.strip()]
    claims = []
    current_claim = None
    for seg in segments:
        parts = seg.split(elem_sep)
        tag = parts[0].strip().upper()
        if tag == "CLM":
            # Start of a new claim
            if current_claim:
                claims.append(current_claim)
            clm01 = parts[1] if len(parts) > 1 else None
            amt = None
            if len(parts) > 2:
                try:
                    amt = decimal.Decimal(parts[2])
                except Exception:
                    amt = None
            current_claim = {"claim_id": clm01, "amount": amt, "raw": seg}
        else:
            if current_claim is not None:
                # Append other lines if desired; keep raw minimal
                pass
    if current_claim:
        claims.append(current_claim)
    # Minimal ISA control extraction for dummy 999-like ack
    isa_ctrl = None
    for seg in segments:
        if seg.startswith("ISA"+elem_sep):
            parts = seg.split(elem_sep)
            if len(parts) >= 14:
                isa_ctrl = parts[13]  # ISA control number (position 13 zero-based if ISA*...)
            break
    return claims, isa_ctrl

def parse_edifact_orders(text: str):
    seg_term = "'"
    elem_sep = "+"
    comp_sep = ":"
    segments = [s for s in text.replace("\r","").split(seg_term) if s.strip()]
    order_lines = []
    current_line = None
    doc_no = None
    for seg in segments:
        parts = seg.split(elem_sep)
        tag = parts[0].strip().upper()
        if tag == "BGM" and len(parts) >= 3:
            doc_no = parts[2]
        if tag == "LIN":
            if current_line:
                order_lines.append(current_line)
            line_no = None
            item_id = None
            if len(parts) >= 2:
                try:
                    line_no = int(parts[1])
                except Exception:
                    line_no = None
            if len(parts) >= 4:
                # e.g., LIN+1++123456:IN'
                comp = parts[3].split(comp_sep)
                item_id = comp[0] if comp else None
            current_line = {"line_no": line_no, "item_id": item_id, "qty": None, "price": None, "raw": seg}
        elif tag == "QTY" and current_line:
            # QTY+47:10'
            comp = parts[1].split(comp_sep) if len(parts) > 1 else []
            if len(comp) >= 2:
                try:
                    current_line["qty"] = decimal.Decimal(comp[1])
                except Exception:
                    pass
        elif tag == "PRI" and current_line:
            # PRI+AAA:12.34'
            comp = parts[1].split(comp_sep) if len(parts) > 1 else []
            if len(comp) >= 2:
                try:
                    current_line["price"] = decimal.Decimal(comp[1])
                except Exception:
                    pass
    if current_line:
        order_lines.append(current_line)
    return order_lines, doc_no

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


def control_from_uuid(job_uuid: uuid.UUID, offset: int = 0) -> str:
    base = job_uuid.int % (10 ** 9)
    value = (base + offset) % (10 ** 9)
    return f"{value:09d}"


def make_999_like(job_uuid: uuid.UUID, trading_partner_id: str | None, total_claims: int, isa_ctrl: str | None) -> str:
    ctrl = (isa_ctrl or control_from_uuid(job_uuid))[:9].rjust(9, "0")
    gs_ctrl = control_from_uuid(job_uuid, 1)
    partner_raw = safe_component(trading_partner_id, "HEDI-RECV").upper()
    partner_padded = partner_raw[:15].rjust(15)
    app_receiver = partner_raw[:12] or "RECEIVER"
    date_short = time.strftime("%y%m%d")
    time_short = time.strftime("%H%M")
    total = max(total_claims, 1)
    segments = [
        f"ISA*00*          *00*          *ZZ*HEDI999       *ZZ*{partner_padded}*{date_short}*{time_short}*^*00501*{ctrl}*0*T*:~",
        f"GS*FA*HEDI*{app_receiver}*20{date_short}*{time_short}*{gs_ctrl}*X*005010X231A1~",
        "ST*999*0001*005010X231A1~",
        "AK1*HC*0001~",
        "AK2*837*0001~",
        "AK5*A~",
        f"AK9*A*1*1*1~",
        "SE*7*0001~",
        f"GE*1*{gs_ctrl}~",
        f"IEA*1*{ctrl}~",
    ]
    return "\n".join(segments)


def make_277ca_like(job_uuid: uuid.UUID, trading_partner_id: str | None, total_claims: int) -> str:
    ctrl = control_from_uuid(job_uuid, 2)
    gs_ctrl = control_from_uuid(job_uuid, 3)
    partner_raw = safe_component(trading_partner_id, "HEDI-RECV").upper()
    partner_padded = partner_raw[:15].rjust(15)
    partner_short = partner_raw[:12] or "RECEIVER"
    date_full = time.strftime("%Y%m%d")
    time_short = time.strftime("%H%M")
    total = max(total_claims, 1)
    segments = [
        f"ISA*00*          *00*          *ZZ*HEDI277       *ZZ*{partner_padded}*{date_full[2:]}*{time_short}*^*00501*{ctrl}*0*T*:~",
        f"GS*HN*HEDI*{partner_short}*{date_full}*{time_short}*{gs_ctrl}*X*005010X214~",
        "ST*277*0001*005010X214~",
        f"BHT*0085*08*{ctrl}*{date_full}*{time_short}~",
        "HL*1**20*1~",
        "NM1*PR*2*HEDI HEALTH*****PI*HEDI277~",
        "HL*2*1*21*0~",
        f"NM1*41*2*{partner_short or 'RECEIVER'}*****46*{partner_short or 'RECEIVER'}~",
        f"TRN*1*{ctrl}*{partner_short or 'RECEIVER'}~",
        f"STC*A1:19*{date_full}*U*{total}*CLM~",
        "SE*9*0001~",
        f"GE*1*{gs_ctrl}~",
        f"IEA*1*{ctrl}~",
    ]
    return "\n".join(segments)


def make_contrl_like(doc_no: str | None, total_lines: int) -> str:
    doc = doc_no or "UNKNOWN"
    return f"CONTRL-LIKE ACK\\nDoc: {doc}\\nAccepted lines: {total_lines}\\nStatus: ACCEPTED"


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


def safe_component(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", value.strip())
    return cleaned or fallback


def control_from_uuid(job_uuid: uuid.UUID, offset: int = 0) -> str:
    base = job_uuid.int % (10 ** 9)
    value = (base + offset) % (10 ** 9)
    return f"{value:09d}"


def make_999_like(job_uuid: uuid.UUID, trading_partner_id: str | None, total_claims: int, isa_ctrl: str | None) -> str:
    ctrl = (isa_ctrl or control_from_uuid(job_uuid))[:9].rjust(9, "0")
    gs_ctrl = control_from_uuid(job_uuid, 1)
    partner_raw = safe_component(trading_partner_id, "HEDI-RECV").upper()
    partner_padded = partner_raw[:15].rjust(15)
    app_receiver = partner_raw[:12] or "RECEIVER"
    date_short = time.strftime("%y%m%d")
    time_short = time.strftime("%H%M")
    total = max(total_claims, 1)
    segments = [
        f"ISA*00*          *00*          *ZZ*HEDI999       *ZZ*{partner_padded}*{date_short}*{time_short}*^*00501*{ctrl}*0*T*:~",
        f"GS*FA*HEDI*{app_receiver}*20{date_short}*{time_short}*{gs_ctrl}*X*005010X231A1~",
        "ST*999*0001*005010X231A1~",
        "AK1*HC*0001~",
        "AK2*837*0001~",
        "AK5*A~",
        f"AK9*A*1*1*1~",
        "SE*7*0001~",
        f"GE*1*{gs_ctrl}~",
        f"IEA*1*{ctrl}~",
    ]
    return "\n".join(segments)


def make_277ca_like(job_uuid: uuid.UUID, trading_partner_id: str | None, total_claims: int) -> str:
    ctrl = control_from_uuid(job_uuid, 2)
    gs_ctrl = control_from_uuid(job_uuid, 3)
    partner_raw = safe_component(trading_partner_id, "HEDI-RECV").upper()
    partner_padded = partner_raw[:15].rjust(15)
    partner_short = partner_raw[:12] or "RECEIVER"
    date_full = time.strftime("%Y%m%d")
    time_short = time.strftime("%H%M")
    total = max(total_claims, 1)
    segments = [
        f"ISA*00*          *00*          *ZZ*HEDI277       *ZZ*{partner_padded}*{date_full[2:]}*{time_short}*^*00501*{ctrl}*0*T*:~",
        f"GS*HN*HEDI*{partner_short}*{date_full}*{time_short}*{gs_ctrl}*X*005010X214~",
        "ST*277*0001*005010X214~",
        f"BHT*0085*08*{ctrl}*{date_full}*{time_short}~",
        "HL*1**20*1~",
        "NM1*PR*2*HEDI HEALTH*****PI*HEDI277~",
        "HL*2*1*21*0~",
        f"NM1*41*2*{partner_short or 'RECEIVER'}*****46*{partner_short or 'RECEIVER'}~",
        f"TRN*1*{ctrl}*{partner_short or 'RECEIVER'}~",
        f"STC*A1:19*{date_full}*U*{total}*CLM~",
        "SE*9*0001~",
        f"GE*1*{gs_ctrl}~",
        f"IEA*1*{ctrl}~",
    ]
    return "\n".join(segments)


def make_contrl_like(doc_no: str | None, total_lines: int) -> str:
    doc = doc_no or "UNKNOWN"
    return f"CONTRL-LIKE ACK\\nDoc: {doc}\\nAccepted lines: {total_lines}\\nStatus: ACCEPTED"


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
    raw = base64.b64decode(payload["data_b64"])
    text = raw.decode("utf-8", errors="replace")
    ftype = detect_format(text)
    size = len(raw)
    filename = payload.get("filename", "upload.dat")

    claims_count = None
    order_lines_count = None
    ack_content = None
    ack_type = None
    ack_records: list[tuple[str, str]] = []
    job_uuid: uuid.UUID | None = None

    ack_records: list[tuple[str, str]] = []
    job_uuid: uuid.UUID | None = None

    with get_db() as conn:
        with conn.cursor() as cur:
           import_id, job_uuid = ensure_import_record(cur, payload, filename, ftype, size, raw) 
           if ftype.startswith("X12"):
                claims, isa_ctrl = parse_x12_837(text)
                claims_count = len(claims)
                if claims:
                    execute_batch(
                        cur,
                        "INSERT INTO claims (import_id, claim_id, amount, raw_claim) VALUES (%s,%s,%s,%s)",
                        [(import_id, c["claim_id"], c["amount"], c["raw"]) for c in claims],
                        page_size=500,
                    )
                ack_999 = make_999_like(job_uuid, payload.get("trading_partner_id"), claims_count or 0, isa_ctrl)
                ack_277 = make_277ca_like(job_uuid, payload.get("trading_partner_id"), claims_count or 0)
                ack_records.extend([("999", ack_999), ("277CA", ack_277)])
                
           elif ftype.startswith("EDIFACT"):
                lines, doc_no = parse_edifact_orders(text)
                order_lines_count = len(lines)
                if lines:
                    execute_batch(
                        cur,
                        "INSERT INTO order_lines (import_id, line_no, item_id, qty, price, raw_line) VALUES (%s,%s,%s,%s,%s,%s)",
                        [(import_id, l["line_no"], l["item_id"], l["qty"], l["price"], l["raw"]) for l in lines],
                        page_size=500,
                    )
                ack_contrl = make_contrl_like(doc_no, order_lines_count or 0)
                ack_records.append(("CONTRL", ack_contrl))
           else:
                ack_records.append(("NOTICE", "UNKNOWN FORMAT - no ack generated"))

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
        for ack_type, ack_content in ack_records:
                persist_ack(cur, import_id, ack_type, ack_content)
        conn.commit()

    if job_uuid is None:
        job_uuid = resolve_job_uuid(payload.get("job_id"))

    for ack_type, ack_content in ack_records:
        fanout_ack_files(payload.get("uploaded_by"), ack_type, ack_content, job_uuid, payload.get("trading_partner_id"))

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
