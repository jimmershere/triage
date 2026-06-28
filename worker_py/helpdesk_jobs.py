"""RabbitMQ worker for helpdesk resubmission events (Workstream 4).

Consumes ``claim.resubmitted`` events and updates the matching helpdesk case(s):
a passing corrected resubmission resolves the case; a failing one moves it back
to awaiting another attempt. This is how cases close automatically once a
corrected resubmission passes validation.

Event contract (published by the ingest/validation pipeline on resubmission)::

    {
      "resubmission_claim_id": "<claimtrace claim id of the new submission>",
      "original_claim_id":     "<claimtrace claim id of the original (optional)>",
      "case_id":               "<specific case id (optional)>",
      "passed":                true,
      "actor":                 "validation-pipeline"
    }

Run standalone:  ``python -m worker_py.helpdesk_jobs``
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logger = logging.getLogger("worker.helpdesk")

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://edi:edi@postgres:5432/edi?sslmode=require")
RESUBMISSION_QUEUE = os.getenv("RMQ_RESUBMISSION_QUEUE", "claim_resubmissions")


def process_resubmission_event(payload: dict, conn, *, service=None) -> dict:
    """Apply a resubmission event to the matching helpdesk case(s).

    ``service`` defaults to ``api.helpdesk_service`` but can be injected for tests.
    """
    if service is None:
        from api import helpdesk_service as service  # lazy import keeps tests light

    resubmission_claim_id = payload.get("resubmission_claim_id")
    if not resubmission_claim_id:
        raise ValueError("resubmission_claim_id is required")
    return service.apply_resubmission(
        conn,
        resubmission_claim_id=resubmission_claim_id,
        original_claim_id=payload.get("original_claim_id"),
        case_id=payload.get("case_id"),
        passed=bool(payload.get("passed", True)),
        actor=payload.get("actor") or "resubmission-worker",
    )


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
            logger.warning("RabbitMQ not ready for helpdesk worker (%s); retrying", exc)
            time.sleep(5)
    ch = connection.channel()
    ch.queue_declare(queue=RESUBMISSION_QUEUE, durable=True)
    logger.info("Helpdesk worker consuming from %s", RESUBMISSION_QUEUE)

    def cb(ch_, method, _props, body):
        try:
            payload = json.loads(body.decode("utf-8"))
            with psycopg2.connect(DATABASE_URL) as conn:
                result = process_resubmission_event(payload, conn)
            logger.info("Applied resubmission event: matched=%s", result.get("matched"))
            ch_.basic_ack(delivery_tag=method.delivery_tag)
        except Exception:
            logger.exception("Resubmission event failed; rejecting")
            ch_.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    ch.basic_qos(prefetch_count=5)
    ch.basic_consume(queue=RESUBMISSION_QUEUE, on_message_callback=cb, auto_ack=False)
    try:
        ch.start_consuming()
    finally:
        try:
            connection.close()
        except Exception:
            pass


if __name__ == "__main__":  # pragma: no cover
    main()
