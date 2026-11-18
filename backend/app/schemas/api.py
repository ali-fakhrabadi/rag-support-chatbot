"""Request and response schemas for the public API (v1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.db.models import AccessLevel, DocumentStatus, JobStatus, MessageRole, Role

T = TypeVar("T")


class Page(BaseModel, Generic[T]):  # noqa: UP046 - explicit Generic keeps pydantic happy
    items: list[T]
    total: int
    limit: int
    offset: int


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- auth -------------------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    workspace: str | None = Field(None, max_length=63, description="Workspace slug")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "email": "admin@northwind.example",
                    "password": "a-long-password",
                    "workspace": "northwind",
                }
            ]
        }
    )


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - not a secret
    expires_in: int


# --- users and tenants ------------------------------------------------------------------------
class TenantOut(ORM):
    id: uuid.UUID
    slug: str
    name: str


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: Role
    tenant: TenantOut


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: Role
    joined_at: datetime


class MemberCreate(BaseModel):
    email: EmailStr
    full_name: str = Field("", max_length=200)
    password: str = Field(min_length=12, max_length=256)
    role: Role = Role.MEMBER


class MemberUpdate(BaseModel):
    role: Role


# --- documents --------------------------------------------------------------------------------
class DocumentOut(ORM):
    id: uuid.UUID
    title: str
    filename: str
    media_type: str
    size_bytes: int
    access_level: AccessLevel
    status: DocumentStatus
    page_count: int | None
    chunk_count: int
    embedding_model: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class DocumentUpdate(BaseModel):
    access_level: AccessLevel


class JobOut(ORM):
    id: uuid.UUID
    document_id: uuid.UUID
    status: JobStatus
    stage: str
    progress: int
    attempts: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class UploadOut(BaseModel):
    document: DocumentOut
    job: JobOut


class ChunkOut(ORM):
    id: uuid.UUID
    document_id: uuid.UUID
    ordinal: int
    content: str
    page_number: int | None
    section: str | None
    char_start: int
    char_end: int


# --- search and chat --------------------------------------------------------------------------
class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(5, ge=1, le=20)


class SearchHit(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    page: int | None
    section: str | None
    content: str
    similarity: float
    fused_score: float


class SearchResponse(BaseModel):
    hits: list[SearchHit]
    timings_ms: dict[str, int]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"question": "How many days do we have to approve an expense report?"}]
        }
    )


class Citation(BaseModel):
    source_id: str
    document_id: uuid.UUID
    document_title: str
    chunk_id: uuid.UUID
    page: int | None
    section: str | None
    snippet: str
    similarity: float


class MessageOut(BaseModel):
    id: uuid.UUID
    role: MessageRole
    content: str
    citations: list[Citation]
    abstained: bool
    created_at: datetime
    meta: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    conversation_id: uuid.UUID
    answer: MessageOut
    question: MessageOut


class ConversationOut(ORM):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class AuditEventOut(ORM):
    id: uuid.UUID
    action: str
    outcome: str
    actor_user_id: uuid.UUID | None
    target_type: str | None
    target_id: str | None
    ip: str | None
    details: dict[str, Any]
    created_at: datetime
