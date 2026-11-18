"""Row-Level Security, ingestion, retrieval and deletion against real PostgreSQL + pgvector."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.core.deps import Principal
from app.db.models import (
    AccessLevel,
    Chunk,
    Document,
    DocumentStatus,
    DocumentUpload,
    IngestionJob,
    JobStatus,
    Role,
)
from app.db.session import session_scope
from app.providers.embeddings import HashingEmbeddings
from app.services.ingestion import run_ingestion
from app.services.retrieval import retrieve
from tests.helpers import make_pdf
from tests.integration.conftest import Account

pytestmark = pytest.mark.integration


def _principal(account: Account) -> Principal:
    return Principal(account.user_id, account.tenant_id, Role(account.role), account.email)


def _add_document(
    account: Account,
    name: str,
    data: bytes,
    media_type: str,
    access: AccessLevel = AccessLevel.ALL_MEMBERS,
) -> tuple[uuid.UUID, uuid.UUID]:
    with session_scope(account.tenant_id) as db:
        document = Document(
            tenant_id=account.tenant_id,
            title=name,
            filename=name,
            media_type=media_type,
            size_bytes=len(data),
            sha256=uuid.uuid4().hex * 2,
            access_level=access,
        )
        db.add(document)
        db.flush()
        job = IngestionJob(tenant_id=account.tenant_id, document_id=document.id)
        db.add(job)
        db.flush()
        db.add(DocumentUpload(job_id=job.id, tenant_id=account.tenant_id, data=data))
        return document.id, job.id


def _ingest(
    account: Account,
    name: str,
    data: bytes,
    media_type: str = "text/plain",
    access: AccessLevel = AccessLevel.ALL_MEMBERS,
) -> uuid.UUID:
    document_id, job_id = _add_document(account, name, data, media_type, access)
    settings = get_settings()
    outcome = run_ingestion(
        job_id, account.tenant_id, settings, HashingEmbeddings(settings), final_attempt=True
    )
    assert outcome.status == "succeeded", outcome
    return document_id


# --- Row-Level Security -----------------------------------------------------------------------
def test_rls_hides_other_tenants_rows_even_without_a_where_clause(
    make_account: Callable[..., Account],
) -> None:
    a = make_account("alpha", "a@alpha.example")
    b = make_account("bravo", "b@bravo.example")
    _ingest(a, "alpha.txt", b"Alpha secret: the launch code is 1111.")
    _ingest(b, "bravo.txt", b"Bravo secret: the launch code is 2222.")

    with session_scope(a.tenant_id) as db:
        titles = db.scalars(select(Document.title)).all()  # deliberately no tenant filter
        contents = db.scalars(select(Chunk.content)).all()
    assert titles == ["alpha.txt"]
    assert all("2222" not in c for c in contents)

    with session_scope() as db:  # no tenant bound at all
        assert db.scalar(select(func.count()).select_from(Document)) == 0
        assert db.scalar(select(func.count()).select_from(Chunk)) == 0


def test_rls_rejects_writes_into_another_tenant(make_account: Callable[..., Account]) -> None:
    a = make_account("alpha", "a@alpha.example")
    b = make_account("bravo", "b@bravo.example")
    with pytest.raises(DBAPIError, match="row-level security"), session_scope(a.tenant_id) as db:
        db.add(
            Document(
                tenant_id=b.tenant_id,
                title="x",
                filename="x.txt",
                media_type="text/plain",
                size_bytes=1,
                sha256="0" * 64,
            )
        )
        db.flush()


def test_application_role_cannot_bypass_rls_or_change_audit(
    make_account: Callable[..., Account],
) -> None:
    a = make_account("alpha", "a@alpha.example")
    with session_scope(a.tenant_id) as db:
        assert (
            db.scalar(text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"))
            is False
        )
        assert (
            db.scalar(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")) is False
        )
    with pytest.raises(DBAPIError, match="permission denied"), session_scope(a.tenant_id) as db:
        db.execute(text("DELETE FROM audit_events"))


# --- ingestion --------------------------------------------------------------------------------
def test_ingestion_persists_chunks_vectors_and_full_text(
    make_account: Callable[..., Account],
    clean_db: Engine,
) -> None:
    a = make_account("alpha", "a@alpha.example")
    pdf = make_pdf(
        [
            "Travel policy. Economy class for flights under 6 hours.",
            "Hotel limit is 180 euros per night.",
        ]
    )
    document_id = _ingest(a, "travel.pdf", pdf, "application/pdf")
    md = b"# Handbook\n## Leave\nEmployees get 25 days of annual leave.\n## Tools\nLaptops."
    md_id = _ingest(a, "handbook.md", md, "text/markdown")

    with clean_db.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT page_number, section, vector_dims(embedding), tsv IS NOT NULL, "
                "embedding_model FROM chunks WHERE document_id = :d ORDER BY ordinal"
            ),
            {"d": document_id},
        ).all()
        assert [r[0] for r in rows] == [1, 2]
        assert all(r[2] == 768 and r[3] and r[4] == "hashing-v1" for r in rows)
        sections = conn.scalars(
            text("SELECT section FROM chunks WHERE document_id = :d ORDER BY ordinal"), {"d": md_id}
        ).all()
        assert sections == ["Handbook > Leave", "Handbook > Tools"]
        doc = conn.execute(
            text("SELECT status, page_count, chunk_count FROM documents WHERE id = :d"),
            {"d": document_id},
        ).one()
        assert tuple(doc) == ("ready", 2, 2)
        assert conn.scalar(text("SELECT count(*) FROM document_uploads")) == 0


def test_failed_extraction_marks_document_failed_and_drops_upload(
    make_account: Callable[..., Account],
) -> None:
    a = make_account("alpha", "a@alpha.example")
    document_id, job_id = _add_document(a, "bad.pdf", b"%PDF-1.4 garbage", "application/pdf")
    settings = get_settings()
    outcome = run_ingestion(
        job_id, a.tenant_id, settings, HashingEmbeddings(settings), final_attempt=False
    )
    assert outcome.status == "failed"
    with session_scope(a.tenant_id) as db:
        document = db.get(Document, document_id)
        job = db.get(IngestionJob, job_id)
        assert document is not None and job is not None
        assert document.status is DocumentStatus.FAILED and job.status is JobStatus.FAILED
        assert db.get(DocumentUpload, job_id) is None


def test_reingestion_replaces_chunks_without_duplicates(
    make_account: Callable[..., Account],
) -> None:
    a = make_account("alpha", "a@alpha.example")
    document_id, job_id = _add_document(a, "a.txt", b"one two three " * 40, "text/plain")
    settings = get_settings()
    embedder = HashingEmbeddings(settings)
    run_ingestion(job_id, a.tenant_id, settings, embedder, final_attempt=True)
    with session_scope(a.tenant_id) as db:
        first = db.scalar(select(func.count()).where(Chunk.document_id == document_id))
        db.add(DocumentUpload(job_id=job_id, tenant_id=a.tenant_id, data=b"one two three " * 40))
    run_ingestion(job_id, a.tenant_id, settings, embedder, final_attempt=True)
    with session_scope(a.tenant_id) as db:
        assert db.scalar(select(func.count()).where(Chunk.document_id == document_id)) == first


def test_deleting_a_document_removes_its_chunks(
    make_account: Callable[..., Account], clean_db: Engine
) -> None:
    a = make_account("alpha", "a@alpha.example")
    document_id = _ingest(a, "a.txt", b"Remove me later. " * 50)
    with session_scope(a.tenant_id) as db:
        db.delete(db.get(Document, document_id))
    with clean_db.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM chunks")) == 0
        assert conn.scalar(text("SELECT count(*) FROM ingestion_jobs")) == 0


# --- retrieval --------------------------------------------------------------------------------
def test_hybrid_retrieval_finds_the_relevant_chunk(make_account: Callable[..., Account]) -> None:
    a = make_account("alpha", "a@alpha.example")
    _ingest(a, "leave.txt", b"Annual leave: every employee receives 25 vacation days per year.")
    _ingest(a, "it.txt", b"The VPN client must be updated every quarter by the IT team.")
    settings = get_settings()
    with session_scope(a.tenant_id) as db:
        result = retrieve(
            db,
            settings,
            _principal(a),
            HashingEmbeddings(settings),
            "How many vacation days per year?",
        )
    assert result.chunks and result.chunks[0].document_title == "leave.txt"
    top = result.chunks[0]
    assert top.vector_rank == 1 and top.text_rank == 1 and top.similarity > 0


def test_retrieval_respects_tenant_and_access_level(make_account: Callable[..., Account]) -> None:
    admin = make_account("alpha", "admin@alpha.example")
    member = make_account("alpha", "member@alpha.example", role="member")
    other = make_account("bravo", "b@bravo.example")
    _ingest(
        admin,
        "salaries.txt",
        b"Executive salary bands are confidential: band E1 is high.",
        access=AccessLevel.ADMINS_ONLY,
    )
    _ingest(admin, "public.txt", b"Salary reviews happen every April for all staff.")
    _ingest(other, "bravo.txt", b"Bravo salary bands: band B1 and band B2.")
    settings = get_settings()
    embedder = HashingEmbeddings(settings)

    def titles(account: Account) -> set[str]:
        with session_scope(account.tenant_id) as db:
            result = retrieve(
                db, settings, _principal(account), embedder, "salary bands", min_similarity=-1.0
            )
        return {c.document_title for c in result.chunks}

    assert titles(admin) == {"salaries.txt", "public.txt"}
    assert titles(member) == {"public.txt"}
    assert titles(other) == {"bravo.txt"}


def test_similarity_threshold_drops_unrelated_chunks(make_account: Callable[..., Account]) -> None:
    a = make_account("alpha", "a@alpha.example")
    _ingest(a, "leave.txt", b"Annual leave is 25 days.")
    settings = get_settings()
    with session_scope(a.tenant_id) as db:
        result = retrieve(
            db,
            settings,
            _principal(a),
            HashingEmbeddings(settings),
            "quantum chromodynamics lattice",
            min_similarity=0.5,
        )
    assert result.chunks == []


def test_chunks_from_another_embedding_model_are_ignored(
    make_account: Callable[..., Account],
    clean_db: Engine,
) -> None:
    a = make_account("alpha", "a@alpha.example")
    _ingest(a, "leave.txt", b"Annual leave is 25 days per year.")
    with clean_db.begin() as conn:
        conn.execute(text("UPDATE chunks SET embedding_model = 'old-model'"))
    settings = get_settings()
    with session_scope(a.tenant_id) as db:
        result = retrieve(
            db,
            settings,
            _principal(a),
            HashingEmbeddings(settings),
            "annual leave days",
            min_similarity=-1.0,
        )
    assert result.chunks == []
