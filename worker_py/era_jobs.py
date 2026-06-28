"""RabbitMQ worker for 835 restore jobs (Workstream 5).

Consumes ``era.redeliver`` / ``era.reconstruct`` jobs published by the API's
``/era/jobs`` endpoint and runs them against the immutable ERA store. Re-delivery
returns the stored 835 byte-for-byte; reconstruction rebuilds and balance-checks
from stored data. Results are written to the archive drop directory so downstream
transmission/mailbox automation can pick them up.

Run standalone:  ``python -m worker_py.era_jobs``
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logger = logging.getLogger("worker.era")

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://edi:edi@postgres:5432/edi?sslmode=require")
ERA_JOBS_QUEUE = os.getenv("RMQ_ERA_QUEUE", "era_jobs")
ARCHIVE_DIR = os.getenv("ARCHIVE_DIR", "/archive")

VALID_JOBS = {"era.redeliver", "era.reconstruct"}


def process_era_job(payload: dict, conn, *, service=None) -> dict:
    """Dispatch a single ERA job. Returns a structured result dict.

    ``service`` defaults to ``api.era_service`` but can be injected for testing.
    """
    if service is None:
        from api import era_service as service  # lazy import keeps tests light

    job = (payload.get("job") or "").strip().lower()
    artifact_id = payload.get("artifact_id")
    created_by = payload.get("created_by")
    if job not in VALID_JOBS:
        raise ValueError(f"unknown ERA job '{job}' (expected one of {sorted(VALID_JOBS)})")
    if not artifact_id:
        raise ValueError("artifact_id is required")

    if job == "era.redeliver":
        result = service.redeliver(conn, artifact_id, actor=created_by)
        if not result.get("found"):
            return {"job": job, "artifact_id": artifact_id, "found": False}
        raw = base64.b64decode(result["raw_835_b64"])
        path = _write_drop(result.get("filename") or f"redeliver_{artifact_id}.835", raw)
        return {"job": job, "artifact_id": artifact_id, "found": True, "output": path, "trn": result.get("trn")}

    # era.reconstruct
    result = service.reconstruct(conn, artifact_id, created_by=created_by)
    if not result.get("found"):
        return {"job": job, "artifact_id": artifact_id, "found": False}
    new_id = result["artifact"]["artifact_id"]
    path = _write_drop(f"reconstruct_{new_id}.835", result["x12"].encode("utf-8"))
    return {
        "job": job,
        "artifact_id": artifact_id,
        "found": True,
        "new_artifact_id": new_id,
        "balanced": result.get("balanced"),
        "output": path,
    }


def _write_drop(filename: str, data: bytes) -> str:
    target = Path(ARCHIVE_DIR) / "drop" / filename
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except Exception:
        logger.exception("Failed to write ERA output %s", target)
    return str(target)


def main() -> None:  # pragma: no cover - runtime consumer
    import pika
    import psycopg2

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    params = pika.URLParameters(RABBITMQ_URL)
    while True:
        try:
            connection = pika.BlockingConnection(params)
            break
        except Exception as exc:
            logger.warning("RabbitMQ not ready for ERA worker (%s); retrying", exc)
            time.sleep(5)
    ch = connection.channel()
    ch.queue_declare(queue=ERA_JOBS_QUEUE, durable=True)
    logger.info("ERA worker consuming from %s", ERA_JOBS_QUEUE)

    def cb(ch_, method, _props, body):
        try:
            payload = json.loads(body.decode("utf-8"))
            with psycopg2.connect(DATABASE_URL) as conn:
                result = process_era_job(payload, conn)
            logger.info("Processed ERA job: %s", result)
            ch_.basic_ack(delivery_tag=method.delivery_tag)
        except Exception:
            logger.exception("ERA job failed; rejecting")
            ch_.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    ch.basic_qos(prefetch_count=5)
    ch.basic_consume(queue=ERA_JOBS_QUEUE, on_message_callback=cb, auto_ack=False)
    try:
        ch.start_consuming()
    finally:
        try:
            connection.close()
        except Exception:
            pass


if __name__ == "__main__":  # pragma: no cover
    main()
