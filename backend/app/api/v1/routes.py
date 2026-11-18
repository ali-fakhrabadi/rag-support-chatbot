"""Version 1 of the HTTP API."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.config import Settings, get_settings
from app.core.deps import Principal, client_ip, get_db, get_principal, require_admin
from app.core.errors import NotFound, PayloadTooLarge
from app.db.models import AccessLevel, AuditEvent, DocumentStatus, Message, Tenant, User
from app.providers.embeddings import EmbeddingProvider, get_embedding_provider
from app.providers.errors import ProviderError
from app.providers.llm import LLMProvider, get_llm_provider
from app.schemas import api as s
from app.services import auth, chat, documents, tenants
from app.services.retrieval import retrieve

router = APIRouter(prefix="/api/v1")
Db = Annotated[Session, Depends(get_db)]
Caller = Annotated[Principal, Depends(get_principal)]
Admin = Annotated[Principal, Depends(require_admin)]
Ip = Annotated[str, Depends(client_ip)]
Cfg = Annotated[Settings, Depends(get_settings)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def embedding_provider(settings: Cfg) -> EmbeddingProvider:
    return get_embedding_provider(settings)


def llm_provider(settings: Cfg) -> LLMProvider:
    return get_llm_provider(settings)


# --- auth -------------------------------------------------------------------------------------
@router.post("/auth/login", response_model=s.TokenResponse, tags=["auth"])
def login(body: s.LoginRequest, db: Db, settings: Cfg, ip: Ip) -> s.TokenResponse:
    ratelimit.hit("login-ip", ip, settings.rate_limit_login_per_minute)
    ratelimit.hit("login-email", body.email.lower(), settings.rate_limit_login_per_minute)
    pair = auth.login(db, settings, body.email, body.password, body.workspace, ip)
    return s.TokenResponse(
        access_token=pair.access_token, refresh_token=pair.refresh_token, expires_in=pair.expires_in
    )


@router.post("/auth/refresh", response_model=s.TokenResponse, tags=["auth"])
def refresh(body: s.RefreshRequest, db: Db, settings: Cfg, ip: Ip) -> s.TokenResponse:
    ratelimit.hit("refresh-ip", ip, settings.rate_limit_login_per_minute * 3)
    pair = auth.refresh(db, settings, body.refresh_token, ip)
    return s.TokenResponse(
        access_token=pair.access_token, refresh_token=pair.refresh_token, expires_in=pair.expires_in
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
def logout(body: s.RefreshRequest, db: Db, principal: Caller, ip: Ip) -> Response:
    auth.logout(db, body.refresh_token, principal.user_id, ip)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- current user and workspace ---------------------------------------------------------------
@router.get("/me", response_model=s.MeOut, tags=["users"])
def me(db: Db, principal: Caller) -> s.MeOut:
    user = db.get(User, principal.user_id)
    tenant = db.get(Tenant, principal.tenant_id)
    if user is None or tenant is None:
        raise NotFound("Account not found")
    return s.MeOut(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=principal.role,
        tenant=s.TenantOut.model_validate(tenant),
    )


@router.get("/tenants/current/members", response_model=list[s.MemberOut], tags=["tenants"])
def list_members(db: Db, principal: Admin) -> list[s.MemberOut]:
    return [
        s.MemberOut(
            user_id=m.user_id,
            email=m.user.email,
            full_name=m.user.full_name,
            role=m.role,
            joined_at=m.created_at,
        )
        for m in tenants.list_members(db, principal)
    ]


@router.post(
    "/tenants/current/members", response_model=s.MemberOut, status_code=201, tags=["tenants"]
)
def add_member(body: s.MemberCreate, db: Db, principal: Admin, ip: Ip) -> s.MemberOut:
    m = tenants.invite_member(
        db, principal, body.email, body.full_name, body.password, body.role, ip
    )
    return s.MemberOut(
        user_id=m.user_id,
        email=m.user.email,
        full_name=m.user.full_name,
        role=m.role,
        joined_at=m.created_at,
    )


@router.patch("/tenants/current/members/{user_id}", response_model=s.MemberOut, tags=["tenants"])
def update_member(
    user_id: uuid.UUID, body: s.MemberUpdate, db: Db, principal: Admin, ip: Ip
) -> s.MemberOut:
    m = tenants.change_role(db, principal, user_id, body.role, ip)
    return s.MemberOut(
        user_id=m.user_id,
        email=m.user.email,
        full_name=m.user.full_name,
        role=m.role,
        joined_at=m.created_at,
    )


@router.delete("/tenants/current/members/{user_id}", status_code=204, tags=["tenants"])
def delete_member(user_id: uuid.UUID, db: Db, principal: Admin, ip: Ip) -> Response:
    tenants.remove_member(db, principal, user_id, ip)
    return Response(status_code=204)


@router.get(
    "/tenants/current/audit-events", response_model=s.Page[s.AuditEventOut], tags=["tenants"]
)
def audit_events(
    db: Db, principal: Admin, limit: Limit = 50, offset: Offset = 0
) -> s.Page[s.AuditEventOut]:
    base = select(AuditEvent).where(AuditEvent.tenant_id == principal.tenant_id)
    rows = db.scalars(
        base.order_by(AuditEvent.created_at.desc(), AuditEvent.id).limit(limit).offset(offset)
    ).all()
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    return s.Page(
        items=[s.AuditEventOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# --- documents --------------------------------------------------------------------------------
@router.post("/documents", response_model=s.UploadOut, status_code=202, tags=["documents"])
def upload_document(
    db: Db,
    principal: Admin,
    settings: Cfg,
    ip: Ip,
    file: Annotated[UploadFile, File(description="PDF, TXT or Markdown")],
    title: Annotated[str | None, Form(max_length=300)] = None,
    access_level: Annotated[AccessLevel, Form()] = AccessLevel.ALL_MEMBERS,
) -> s.UploadOut:
    ratelimit.hit("upload", str(principal.user_id), settings.rate_limit_upload_per_minute)
    # Read at most limit + 1 bytes so oversized uploads are rejected without buffering them all.
    data = file.file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise PayloadTooLarge(f"The file exceeds the {settings.max_upload_mb} MB limit")
    result = documents.create_upload(
        db, settings, principal, file.filename, data, title, access_level, ip
    )
    return s.UploadOut(
        document=s.DocumentOut.model_validate(result.document),
        job=s.JobOut.model_validate(result.job),
    )


@router.get("/documents", response_model=s.Page[s.DocumentOut], tags=["documents"])
def list_documents(
    db: Db,
    principal: Caller,
    limit: Limit = 20,
    offset: Offset = 0,
    status_filter: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> s.Page[s.DocumentOut]:
    items, total = documents.list_documents(db, principal, limit, offset, status_filter, q)
    return s.Page(
        items=[s.DocumentOut.model_validate(d) for d in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/documents/{document_id}", response_model=s.DocumentOut, tags=["documents"])
def get_document(document_id: uuid.UUID, db: Db, principal: Caller) -> s.DocumentOut:
    return s.DocumentOut.model_validate(documents.get_document(db, principal, document_id))


@router.patch("/documents/{document_id}", response_model=s.DocumentOut, tags=["documents"])
def update_document(
    document_id: uuid.UUID, body: s.DocumentUpdate, db: Db, principal: Admin, ip: Ip
) -> s.DocumentOut:
    document = documents.set_access_level(db, principal, document_id, body.access_level, ip)
    return s.DocumentOut.model_validate(document)


@router.delete("/documents/{document_id}", status_code=204, tags=["documents"])
def delete_document(document_id: uuid.UUID, db: Db, principal: Admin, ip: Ip) -> Response:
    documents.delete_document(db, principal, document_id, ip)
    return Response(status_code=204)


@router.get("/documents/{document_id}/jobs", response_model=list[s.JobOut], tags=["documents"])
def document_jobs(document_id: uuid.UUID, db: Db, principal: Caller) -> list[s.JobOut]:
    return [s.JobOut.model_validate(j) for j in documents.list_jobs(db, principal, document_id)]


@router.get(
    "/documents/{document_id}/chunks", response_model=s.Page[s.ChunkOut], tags=["documents"]
)
def document_chunks(
    document_id: uuid.UUID, db: Db, principal: Caller, limit: Limit = 20, offset: Offset = 0
) -> s.Page[s.ChunkOut]:
    _, chunks, total = documents.list_chunks(db, principal, document_id, limit, offset)
    return s.Page(
        items=[s.ChunkOut.model_validate(c) for c in chunks],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/documents/{document_id}/chunks/{chunk_id}", response_model=s.ChunkOut, tags=["documents"]
)
def document_chunk(
    document_id: uuid.UUID, chunk_id: uuid.UUID, db: Db, principal: Caller
) -> s.ChunkOut:
    _, chunk = documents.get_chunk(db, principal, document_id, chunk_id)
    return s.ChunkOut.model_validate(chunk)


@router.get("/ingestion-jobs/{job_id}", response_model=s.JobOut, tags=["documents"])
def ingestion_job(job_id: uuid.UUID, db: Db, principal: Caller) -> s.JobOut:
    return s.JobOut.model_validate(documents.get_job(db, principal, job_id))


# --- search and chat --------------------------------------------------------------------------
@router.post("/search", response_model=s.SearchResponse, tags=["chat"])
def search(
    body: s.SearchRequest,
    db: Db,
    principal: Caller,
    settings: Cfg,
    embedder: Annotated[EmbeddingProvider, Depends(embedding_provider)],
) -> s.SearchResponse:
    ratelimit.hit("search", str(principal.user_id), settings.rate_limit_chat_per_minute * 3)
    try:
        result = retrieve(db, settings, principal, embedder, body.query, top_k=body.top_k)
    except ProviderError as exc:
        raise chat.ModelUnavailable("The embedding model is unavailable") from exc
    return s.SearchResponse(
        hits=[
            s.SearchHit(
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                document_title=c.document_title,
                page=c.page,
                section=c.section,
                content=c.content,
                similarity=c.similarity,
                fused_score=c.fused_score,
            )
            for c in result.chunks
        ],
        timings_ms={"embed": result.embed_ms, "search": result.search_ms},
    )


def _message_out(m: Message, include_meta: bool) -> s.MessageOut:
    return s.MessageOut(
        id=m.id,
        role=m.role,
        content=m.content,
        citations=[s.Citation(**c) for c in m.citations],
        abstained=m.abstained,
        created_at=m.created_at,
        meta=m.meta if include_meta else None,
    )


@router.post("/chat", response_model=s.ChatResponse, tags=["chat"])
def ask(
    body: s.ChatRequest,
    db: Db,
    principal: Caller,
    settings: Cfg,
    embedder: Annotated[EmbeddingProvider, Depends(embedding_provider)],
    llm: Annotated[LLMProvider, Depends(llm_provider)],
) -> s.ChatResponse:
    ratelimit.hit("chat", str(principal.user_id), settings.rate_limit_chat_per_minute)
    outcome = chat.ask(db, settings, principal, embedder, llm, body.question, body.conversation_id)
    return s.ChatResponse(
        conversation_id=outcome.conversation.id,
        answer=_message_out(outcome.assistant_message, include_meta=True),
        question=_message_out(outcome.user_message, include_meta=False),
    )


@router.get("/conversations", response_model=s.Page[s.ConversationOut], tags=["chat"])
def list_conversations(
    db: Db, principal: Caller, limit: Limit = 20, offset: Offset = 0
) -> s.Page[s.ConversationOut]:
    items, total = chat.list_conversations(db, principal, limit, offset)
    return s.Page(
        items=[s.ConversationOut.model_validate(c) for c in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/conversations/{conversation_id}", response_model=s.ConversationDetail, tags=["chat"])
def get_conversation(conversation_id: uuid.UUID, db: Db, principal: Caller) -> s.ConversationDetail:
    conversation = chat.get_conversation(db, principal, conversation_id)
    messages = chat.list_messages(db, principal, conversation_id)
    data: dict[str, Any] = s.ConversationOut.model_validate(conversation).model_dump()
    return s.ConversationDetail(
        **data, messages=[_message_out(m, include_meta=False) for m in messages]
    )


@router.delete("/conversations/{conversation_id}", status_code=204, tags=["chat"])
def delete_conversation(conversation_id: uuid.UUID, db: Db, principal: Caller) -> Response:
    chat.delete_conversation(db, principal, conversation_id)
    return Response(status_code=204)
