"""RabbitMQ ``codeset.reloaded`` event publishing (Workstream 2).

When the loader ingests a new code-set version it publishes a small
``codeset.reloaded`` notification so every consumer (validation workers, the
admin UI cache, scrubbing) can invalidate its in-process cache and pick up the
new version without a redeploy.

``pika`` is imported lazily and all broker errors are swallowed: publishing a
cache-invalidation hint must never break an ingest. :func:`build_reload_payload`
is pure and unit-tested without a live broker.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("validation.codesets.events")

CODESET_RELOAD_QUEUE = os.getenv("CODESET_RELOAD_QUEUE", "codeset.reloaded")


def build_reload_payload(
    codeset: str,
    version_label: str,
    *,
    checksum: str = "",
    generation: int | None = None,
    source_effective_date: str | None = None,
) -> dict[str, Any]:
    """Build the JSON-serializable ``codeset.reloaded`` event body."""
    return {
        "event": "codeset.reloaded",
        "codeset": codeset,
        "version_label": version_label,
        "checksum": checksum,
        "generation": generation,
        "source_effective_date": source_effective_date,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def publish_codeset_reloaded(
    codeset: str,
    version_label: str,
    *,
    checksum: str = "",
    generation: int | None = None,
    source_effective_date: str | None = None,
    url: str | None = None,
    queue: str | None = None,
) -> bool:
    """Publish a ``codeset.reloaded`` event. Returns ``True`` on success.

    Never raises — a failed publish is logged and reported as ``False`` so the
    ingest still succeeds.
    """
    payload = build_reload_payload(
        codeset,
        version_label,
        checksum=checksum,
        generation=generation,
        source_effective_date=source_effective_date,
    )
    body = json.dumps(payload).encode("utf-8")
    target_queue = queue or CODESET_RELOAD_QUEUE
    rabbit_url = url or os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
    try:
        import pika  # imported lazily; optional at runtime

        params = pika.URLParameters(rabbit_url)
        connection = pika.BlockingConnection(params)
        try:
            channel = connection.channel()
            channel.queue_declare(queue=target_queue, durable=True)
            channel.basic_publish(
                exchange="",
                routing_key=target_queue,
                body=body,
                properties=pika.BasicProperties(delivery_mode=2),
            )
        finally:
            connection.close()
        return True
    except Exception:  # pragma: no cover - depends on a live broker
        logger.warning(
            "Could not publish codeset.reloaded for %s/%s", codeset, version_label,
            exc_info=True,
        )
        return False
