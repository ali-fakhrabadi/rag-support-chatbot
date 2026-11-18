"""Hybrid retrieval: pgvector cosine search + PostgreSQL full-text search, fused with RRF.

Authorisation is part of the SQL that ranks chunks (tenant, document access level, document
status and embedding model), so unauthorised or stale chunks are never even candidates. Row-Level
Security applies the tenant filter a second time.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.deps import Principal
from app.providers.embeddings import EmbeddingProvider
from app.rag.fusion import reciprocal_rank_fusion


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    ordinal: int
    page: int | None
    section: str | None
    content: str
    similarity: float
    vector_rank: int | None
    text_rank: int | None
    fused_score: float


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    candidates: int
    embed_ms: int
    search_ms: int
    dropped_below_threshold: int = 0
    timings: dict[str, int] = field(default_factory=dict)


_FILTER = """
    c.tenant_id = :tenant_id
    AND d.tenant_id = :tenant_id
    AND d.status = 'ready'
    AND c.embedding_model = :model
    AND (:is_admin OR d.access_level = 'all_members')
"""


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in vector) + "]"


def retrieve(
    db: Session,
    settings: Settings,
    principal: Principal,
    embedder: EmbeddingProvider,
    query: str,
    *,
    top_k: int | None = None,
    min_similarity: float | None = None,
) -> RetrievalResult:
    top_k = top_k or settings.retrieval_top_k
    threshold = settings.min_similarity if min_similarity is None else min_similarity
    n = settings.retrieval_candidates

    started = time.perf_counter()
    qvec = _vector_literal(embedder.embed_query(query))
    embed_ms = int((time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    params = {
        "tenant_id": principal.tenant_id,
        "is_admin": principal.is_admin,
        "model": embedder.model,
        "q": qvec,
        "text": query,
        "n": n,
    }
    # Iterative scans keep filtered HNSW queries from returning too few rows (pgvector >= 0.8).
    db.execute(text(f"SET LOCAL hnsw.ef_search = {int(settings.hnsw_ef_search)}"))
    db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    vector_ids = [
        row.id
        for row in db.execute(
            text(
                f"""SELECT c.id FROM chunks c JOIN documents d ON d.id = c.document_id
                WHERE {_FILTER}
                ORDER BY c.embedding <=> CAST(:q AS vector) LIMIT :n"""
            ),
            params,
        )
    ]
    text_ids = [
        row.id
        for row in db.execute(
            text(
                # Questions rarely contain every term of a passage, so terms are OR-ed;
                # ts_rank_cd still ranks passages that match more terms higher.
                f"""WITH q AS (SELECT to_tsquery('english', replace(
                         websearch_to_tsquery('english', :text)::text, ' & ', ' | ')) AS tsq)
                SELECT c.id FROM chunks c JOIN documents d ON d.id = c.document_id, q
                WHERE {_FILTER} AND c.tsv @@ q.tsq
                ORDER BY ts_rank_cd(c.tsv, q.tsq) DESC, c.id LIMIT :n"""
            ),
            params,
        )
    ]
    fused = reciprocal_rank_fusion([vector_ids, text_ids], k=settings.rrf_k)
    candidate_ids = [chunk_id for chunk_id, _ in fused]
    if not candidate_ids:
        return RetrievalResult([], 0, embed_ms, int((time.perf_counter() - started) * 1000))

    rows = {
        row.id: row
        for row in db.execute(
            text(
                f"""SELECT c.id, c.document_id, d.title, c.ordinal, c.page_number, c.section,
                       c.content, 1 - (c.embedding <=> CAST(:q AS vector)) AS similarity
                FROM chunks c JOIN documents d ON d.id = c.document_id
                WHERE {_FILTER} AND c.id = ANY(:ids)"""
            ),
            {**params, "ids": candidate_ids},
        )
    }
    vector_rank = {cid: i for i, cid in enumerate(vector_ids, start=1)}
    text_rank = {cid: i for i, cid in enumerate(text_ids, start=1)}
    chunks: list[RetrievedChunk] = []
    dropped = 0
    for chunk_id, score in fused:
        row = rows.get(chunk_id)
        if row is None:
            continue
        if float(row.similarity) < threshold:
            dropped += 1
            continue
        chunks.append(
            RetrievedChunk(
                chunk_id=row.id,
                document_id=row.document_id,
                document_title=row.title,
                ordinal=row.ordinal,
                page=row.page_number,
                section=row.section,
                content=row.content,
                similarity=round(float(row.similarity), 4),
                vector_rank=vector_rank.get(chunk_id),
                text_rank=text_rank.get(chunk_id),
                fused_score=round(score, 6),
            )
        )
        if len(chunks) >= top_k:
            break
    return RetrievalResult(
        chunks, len(candidate_ids), embed_ms, int((time.perf_counter() - started) * 1000), dropped
    )
