"""Document use cases. Every query is scoped to the caller's tenant and access level."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.deps import Principal
from app.core.errors import Conflict, NotFound, ServiceUnavailable
from app.db.models import (
    AccessLevel,
    Chunk,
    Document,
    DocumentStatus,
    DocumentUpload,
    IngestionJob,
    JobStatus,
)
from app.ingestion.validation import title_from_filename, validate_upload
from app.services import audit
from app.worker import enqueue_ingestion

log = logging.getLogger("rag.documents")


def visible_documents(principal: Principal) -> Select[Document]:
    query = select(Document).where(Document.tenant_id == principal.tenant_id)
    if not principal.is_admin:
        query = query.where(Document.access_level == AccessLevel.ALL_MEMBERS)
    return query


def get_document(db: Session, principal: Principal, document_id: uuid.UUID) -> Document:
    document = db.scalar(visible_documents(principal).where(Document.id == document_id))
    if document is None:
        # Same answer for "does not exist" and "not yours", so ids cannot be probed.
        raise NotFound("Document not found")
    return document


def list_documents(
    db: Session,
    principal: Principal,
    limit: int,
    offset: int,
    status: DocumentStatus | None,
    query: str | None,
) -> tuple[list[Document], int]:
    base = visible_documents(principal)
    if status:
        base = base.where(Document.status == status)
    if query:
        like = f"%{query.replace('%', r'\%').replace('_', r'\_')}%"
        base = base.where(Document.title.ilike(like, escape="\\"))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    items = db.scalars(base.order_by(Document.created_at.desc()).limit(limit).offset(offset)).all()
    return list(items), total


@dataclass(frozen=True)
class UploadResult:
    document: Document
    job: IngestionJob


def create_upload(
    db: Session,
    settings: Settings,
    principal: Principal,
    filename: str | None,
    data: bytes,
    title: str | None,
    access_level: AccessLevel,
    ip: str,
) -> UploadResult:
    upload = validate_upload(filename, data, settings.max_upload_bytes)
    document = Document(
        tenant_id=principal.tenant_id,
        title=(title or "").strip()[:300] or title_from_filename(upload.filename),
        filename=upload.filename,
        media_type=upload.media_type,
        size_bytes=upload.size_bytes,
        sha256=upload.sha256,
        access_level=access_level,
        status=DocumentStatus.PENDING,
        uploaded_by=principal.user_id,
    )
    db.add(document)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("This file has already been uploaded", code="duplicate_document") from exc
    job = IngestionJob(tenant_id=principal.tenant_id, document_id=document.id)
    db.add(job)
    db.flush()
    db.add(DocumentUpload(job_id=job.id, tenant_id=principal.tenant_id, data=data))
    audit.record(
        db,
        "document.upload",
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        target_type="document",
        target_id=document.id,
        ip=ip,
        details={
            "media_type": upload.media_type,
            "size_bytes": upload.size_bytes,
            "access_level": access_level.value,
        },
    )
    db.commit()

    try:
        enqueue_ingestion(job.id, principal.tenant_id)
    except Exception as exc:
        log.error("could not enqueue ingestion job: %s", type(exc).__name__)
        job.status = JobStatus.FAILED
        job.error = "Ingestion queue unavailable"
        document.status = DocumentStatus.FAILED
        document.error = "Ingestion queue unavailable; delete and upload again"
        db.commit()
        raise ServiceUnavailable("Ingestion queue unavailable, try again shortly") from exc
    return UploadResult(document, job)


def delete_document(db: Session, principal: Principal, document_id: uuid.UUID, ip: str) -> None:
    document = get_document(db, principal, document_id)
    # Chunks (and their vectors), jobs and pending uploads go with the document (ON DELETE
    # CASCADE) in the same transaction, so no orphaned searchable text is left behind.
    db.delete(document)
    audit.record(
        db,
        "document.delete",
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        target_type="document",
        target_id=document_id,
        ip=ip,
        details={"title_length": len(document.title)},
    )
    db.commit()


def set_access_level(
    db: Session, principal: Principal, document_id: uuid.UUID, level: AccessLevel, ip: str
) -> Document:
    document = get_document(db, principal, document_id)
    previous = document.access_level
    document.access_level = level
    audit.record(
        db,
        "document.access_change",
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        target_type="document",
        target_id=document_id,
        ip=ip,
        details={"from": previous.value, "to": level.value},
    )
    db.commit()
    return document


def list_jobs(db: Session, principal: Principal, document_id: uuid.UUID) -> list[IngestionJob]:
    get_document(db, principal, document_id)
    return list(
        db.scalars(
            select(IngestionJob)
            .where(
                IngestionJob.tenant_id == principal.tenant_id,
                IngestionJob.document_id == document_id,
            )
            .order_by(IngestionJob.created_at.desc())
        ).all()
    )


def get_job(db: Session, principal: Principal, job_id: uuid.UUID) -> IngestionJob:
    job: IngestionJob | None = db.scalar(
        select(IngestionJob)
        .join(Document, Document.id == IngestionJob.document_id)
        .where(IngestionJob.id == job_id, IngestionJob.tenant_id == principal.tenant_id)
        .where(visible_documents(principal).whereclause)  # type: ignore[arg-type]
    )
    if job is None:
        raise NotFound("Ingestion job not found")
    return job


def get_chunk(
    db: Session, principal: Principal, document_id: uuid.UUID, chunk_id: uuid.UUID
) -> tuple[Document, Chunk]:
    document = get_document(db, principal, document_id)
    chunk = db.scalar(
        select(Chunk).where(
            Chunk.id == chunk_id,
            Chunk.document_id == document.id,
            Chunk.tenant_id == principal.tenant_id,
        )
    )
    if chunk is None:
        raise NotFound("Passage not found")
    return document, chunk


def list_chunks(
    db: Session, principal: Principal, document_id: uuid.UUID, limit: int, offset: int
) -> tuple[Document, list[Chunk], int]:
    document = get_document(db, principal, document_id)
    base = select(Chunk).where(
        Chunk.document_id == document.id, Chunk.tenant_id == principal.tenant_id
    )
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    chunks = db.scalars(base.order_by(Chunk.ordinal).limit(limit).offset(offset)).all()
    return document, list(chunks), total
