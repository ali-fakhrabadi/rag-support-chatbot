"""Celery application and tasks."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from celery import Celery, signals

from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()

celery_app = Celery("rag", broker=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,  # a crashed worker does not lose the job
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=900,
    task_time_limit=960,
    task_default_queue="ingestion",
    broker_connection_retry_on_startup=True,
    worker_hijack_root_logger=False,
    task_ignore_result=True,
)

MAX_RETRIES = 3


@signals.setup_logging.connect
def _logging(**_: Any) -> None:
    configure_logging(settings.log_level)


@celery_app.task(name="rag.ingest_document", bind=True, max_retries=MAX_RETRIES)
def ingest_document(self: Any, job_id: str, tenant_id: str) -> str:
    from app.providers.embeddings import get_embedding_provider
    from app.services.ingestion import run_ingestion

    outcome = run_ingestion(
        uuid.UUID(job_id),
        uuid.UUID(tenant_id),
        settings,
        get_embedding_provider(settings),
        final_attempt=self.request.retries >= MAX_RETRIES,
    )
    if outcome.status == "retry":
        # Exponential backoff: 15 s, 30 s, 60 s.
        raise self.retry(countdown=15 * 2**self.request.retries)
    logging.getLogger("rag.worker").info("ingestion finished", extra={"status": outcome.status})
    return outcome.status


def enqueue_ingestion(job_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    ingest_document.apply_async(args=[str(job_id), str(tenant_id)], retry=False)
