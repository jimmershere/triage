import os, json, base64, logging, time, re, decimal
import pika, psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv

load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("worker")

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://edi:edi@postgres:5432/edi")

def get_rmq_channel():
    for attempt in range(10):
        try:
            credentials = pika.PlainCredentials('ediapp', '3wm078uu')
            params = pika.ConnectionParameters(
                host='rabbitmq', port=5672,
                credentials=credentials,
                heartbeat=600, blocked_connection_timeout=300
            )
            conn = pika.BlockingConnection(params)
            ch = conn.channel()
            return conn, ch
        except pika.exceptions.AMQPConnectionError as e:
            print(f"RabbitMQ not ready (attempt {attempt+1}/10): {e}")
            time.sleep(5)
    raise RuntimeError("RabbitMQ connection failed after 10 retries")

def get_db():
    return psycopg2.connect(DATABASE_URL)

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

def make_999_like(isa_ctrl: str | None, total_claims: int) -> str:
    # Extremely simplified "999-like" content (not a real 999!)
    ctrl = isa_ctrl or "000000001"
    return f"999-LIKE ACK\\nControl: {ctrl}\\nAccepted claims: {total_claims}\\nStatus: ACCEPTED"

def make_contrl_like(doc_no: str | None, total_lines: int) -> str:
    # Extremely simplified "CONTRL-like" content
    doc = doc_no or "UNKNOWN"
    return f"CONTRL-LIKE ACK\\nDoc: {doc}\\nAccepted lines: {total_lines}\\nStatus: ACCEPTED"

def process_payload(payload: dict):
    raw = base64.b64decode(payload["data_b64"])
    text = raw.decode("utf-8", errors="replace")
    ftype = detect_format(text)
    size = len(raw)
    filename = payload.get("filename", "upload.dat")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO imports (filename, file_type, byte_size) VALUES (%s,%s,%s) RETURNING id",
                (filename, ftype, size),
            )
            import_id = cur.fetchone()[0]
            claims_count = None
            order_lines_count = None
            ack_content = None
            ack_type = None

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
                ack_content = make_999_like(isa_ctrl, claims_count or 0)
                ack_type = "999-like"
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
                ack_content = make_contrl_like(doc_no, order_lines_count or 0)
                ack_type = "CONTRL-like"
            else:
                ack_content = "UNKNOWN FORMAT - no ack generated"
                ack_type = "none"

            cur.execute(
                "UPDATE imports SET claims_count=%s, order_lines_count=%s WHERE id=%s",
                (claims_count, order_lines_count, import_id),
            )
            cur.execute(
                "INSERT INTO acks (import_id, ack_type, content) VALUES (%s,%s,%s)",
                (import_id, ack_type, ack_content),
            )
        conn.commit()

    return ftype, size

def main():
    conn, ch = get_rmq_channel()
    logger.info("Worker connected to RabbitMQ. Waiting for messages...")
    def cb(ch_, method, properties, body):
        try:
            payload = json.loads(body.decode("utf-8"))
            ftype, size = process_payload(payload)
            logger.info("Processed %s bytes as %s for file %s", size, ftype, payload.get("filename"))
            # Publish ack content pointer (optional, here just echo file + type)
            ch_.basic_publish(
                exchange="",
                routing_key="acks",
                body=json.dumps({"job_id": payload.get("job_id"), "filename": payload.get("filename"), "file_type": ftype}).encode("utf-8"),
                properties=pika.BasicProperties(delivery_mode=2),
            )
            ch_.basic_ack(delivery_tag=method.delivery_tag)
        except Exception as e:
            logger.exception("Failed to process message; rejecting")
            ch_.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    ch.basic_qos(prefetch_count=4)
    ch.basic_consume(queue="ingest", on_message_callback=cb, auto_ack=False)
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
