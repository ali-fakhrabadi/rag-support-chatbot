"""The ingestion pipeline run by the Celery worker.

extract -> normalise -> chunk -> embed -> persist. Chunks are replaced in one transaction, so a
retry never leaves duplicates and readers never see a half-indexed document. The uploaded bytes
are deleted when the job finishes, whether it succeeded or failed for good.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Chunk, Document, DocumentStatus, DocumentUpload, IngestionJob, JobStatus
from app.db.session import session_scope
from app.ingestion.chunking import chunk_sections
from app.ingestion.extract import ExtractionError, extract
from app.providers.embeddings import EmbeddingProvider
from app.providers.errors import ProviderError

log = logging.getLogger("rag.ingestion")


@dataclass(frozen=True)
class IngestionOutcome:
    status: str  # "succeeded" | "failed" | "retry" | "gone"
    chunks: int = 0
    error: str | None = None


def _set_progress(job_id: uuid.UUID, tenant_id: uuid.UUID, stage: str, progress: int) -> None:
    with session_scope(tenant_id) as db:
        job = db.get(IngestionJob, job_id)
        if job is not None:
            job.stage, job.progress = stage, progress


def _fail(db: Session, job: IngestionJob, document: Document, message: str) -> None:
    now = datetime.now(UTC)
    job.status, job.stage, job.error, job.finished_at = JobStatus.FAILED, "failed", message, now
    document.status, document.error = DocumentStatus.FAILED, message
    db.execute(delete(DocumentUpload).where(DocumentUpload.job_id == job.id))


def run_ingestion(
    job_id: uuid.UUID,
    tenant_id: uuid.UUID,
    settings: Settings,
    embedder: EmbeddingProvider,
    *,
    final_attempt: bool,
) -> IngestionOutcome:
    # 1. Load the job and mark it running.
    with session_scope(tenant_id) as db:
        job = db.get(IngestionJob, job_id)
        if job is None or job.tenant_id != tenant_id:
            return IngestionOutcome("gone")
        document = db.get(Document, job.document_id)
        upload = db.get(DocumentUpload, job_id)
        if document is None:
            return IngestionOutcome("gone")
        if upload is None:
            if job.status is not JobStatus.SUCCEEDED:
                _fail(db, job, document, "Uploaded file is no longer available")
            return IngestionOutcome("failed", error="upload missing")
        job.status, job.stage, job.attempts = JobStatus.RUNNING, "extracting", job.attempts + 1
        job.started_at = job.started_at or datetime.now(UTC)
        document.status = DocumentStatus.PROCESSING
        data, media_type, title = upload.data, document.media_type, document.title

    # 2. Extract and chunk (no database transaction held while the CPU works).
    try:
        extracted = extract(
            data, media_type, max_pages=settings.max_pdf_pages, max_chars=settings.max_text_chars
        )
        drafts = chunk_sections(extracted.sections, settings.chunk_size, settings.chunk_overlap)
    except ExtractionError as exc:
        with session_scope(tenant_id) as db:
            failed_job = db.get(IngestionJob, job_id)
            failed_doc = db.get(Document, failed_job.document_id) if failed_job else None
            if failed_job and failed_doc:
                _fail(db, failed_job, failed_doc, str(exc)[:500])
        return IngestionOutcome("failed", error=str(exc))

    # 3. Embed in batches, reporting progress.
    _set_progress(job_id, tenant_id, "embedding", 10)
    vectors: list[list[float]] = []
    batch = max(settings.embedding_batch_size, 1)
    try:
        for start in range(0, len(drafts), batch):
            texts = [
                "\n".join(filter(None, [title, d.section, d.content]))
                for d in drafts[start : start + batch]
            ]
            vectors.extend(embedder.embed_documents(texts))
            _set_progress(
                job_id, tenant_id, "embedding", 10 + int(85 * len(vectors) / max(len(drafts), 1))
            )
    except ProviderError as exc:
        log.warning("embedding failed: %s (retryable=%s)", exc.kind, exc.retryable)
        if exc.retryable and not final_attempt:
            _set_progress(job_id, tenant_id, "waiting_retry", 10)
            return IngestionOutcome("retry", error=exc.message)
        with session_scope(tenant_id) as db:
            job, document = db.get(IngestionJob, job_id), None
            if job:
                document = db.get(Document, job.document_id)
            if job and document:
                _fail(db, job, document, f"Embedding failed: {exc.kind}")
        return IngestionOutcome("failed", error=exc.message)

    # 4. Persist atomically.
    with session_scope(tenant_id) as db:
        job = db.get(IngestionJob, job_id)
        document = db.get(Document, job.document_id) if job else None
        if job is None or document is None:
            return IngestionOutcome("gone")  # deleted while we were working
        db.execute(delete(Chunk).where(Chunk.document_id == document.id))
        db.add_all(
            Chunk(
                tenant_id=tenant_id,
                document_id=document.id,
                ordinal=d.ordinal,
                content=d.content,
                page_number=d.page,
                section=d.section[:300] if d.section else None,
                char_start=d.char_start,
                char_end=d.char_end,
                embedding=vector,
                embedding_model=embedder.model,
            )
            for d, vector in zip(drafts, vectors, strict=True)
        )
        now = datetime.now(UTC)
        document.status = DocumentStatus.READY
        document.page_count = extracted.page_count
        document.chunk_count = len(drafts)
        document.embedding_model = embedder.model
        document.error = None
        job.status, job.stage, job.progress, job.error = JobStatus.SUCCEEDED, "done", 100, None
        job.finished_at = now
        db.execute(delete(DocumentUpload).where(DocumentUpload.job_id == job.id))
    log.info("document indexed", extra={"chunks": len(drafts), "tenant_id": str(tenant_id)})
    return IngestionOutcome("succeeded", chunks=len(drafts))


def pending_job_ids(db: Session) -> list[uuid.UUID]:
    """Used by tests and the CLI; RLS limits it to the bound tenant."""
    return list(db.scalars(select(IngestionJob.id).where(IngestionJob.status == JobStatus.QUEUED)))
