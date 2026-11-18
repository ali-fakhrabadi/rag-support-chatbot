"""Question answering over a user's own conversations.

Conversations belong to one user in one tenant; nobody else, including tenant admins, can read
them. History from the same conversation is given to the model for follow-up questions, but
retrieval always runs with the caller's current permissions.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.deps import Principal
from app.core.errors import AppError, NotFound
from app.db.models import Conversation, Message, MessageRole
from app.providers.embeddings import EmbeddingProvider
from app.providers.errors import ProviderError
from app.providers.llm import ChatTurn, LLMProvider
from app.rag.citations import ABSTAIN_MESSAGE, validate_answer
from app.rag.prompt import (
    ANSWER_SCHEMA,
    SYSTEM_PROMPT,
    SourcePassage,
    build_user_prompt,
    quarantine,
)
from app.services.retrieval import RetrievedChunk, retrieve

log = logging.getLogger("rag.chat")
SNIPPET_CHARS = 320
_OLD_MARKERS = re.compile(r"\[\s*S\d+(?:\s*,\s*S\d+)*\s*\]")


class ModelUnavailable(AppError):
    status_code = 503
    code = "model_unavailable"


@dataclass
class ChatOutcome:
    conversation: Conversation
    user_message: Message
    assistant_message: Message


def get_conversation(db: Session, principal: Principal, conversation_id: uuid.UUID) -> Conversation:
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == principal.tenant_id,
            Conversation.user_id == principal.user_id,
        )
    )
    if conversation is None:
        raise NotFound("Conversation not found")
    return conversation


def list_conversations(
    db: Session, principal: Principal, limit: int, offset: int
) -> tuple[list[Conversation], int]:
    base = select(Conversation).where(
        Conversation.tenant_id == principal.tenant_id, Conversation.user_id == principal.user_id
    )
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    items = db.scalars(base.order_by(Conversation.updated_at.desc()).limit(limit).offset(offset))
    return list(items), total


def list_messages(db: Session, principal: Principal, conversation_id: uuid.UUID) -> list[Message]:
    conversation = get_conversation(db, principal, conversation_id)
    return list(
        db.scalars(
            select(Message)
            .where(
                Message.conversation_id == conversation.id, Message.tenant_id == principal.tenant_id
            )
            .order_by(Message.created_at, Message.role.desc())
        )
    )


def delete_conversation(db: Session, principal: Principal, conversation_id: uuid.UUID) -> None:
    db.delete(get_conversation(db, principal, conversation_id))
    db.commit()


def _history(db: Session, conversation: Conversation, limit: int) -> list[ChatTurn]:
    if limit <= 0:
        return []
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    ).all()
    # Old [Sn] markers point at the previous turn's sources; drop them so they cannot be
    # confused with this turn's numbering.
    return [ChatTurn(m.role.value, _OLD_MARKERS.sub("", m.content)) for m in reversed(rows)]


def _citation(sid: str, chunk: RetrievedChunk) -> dict[str, Any]:
    snippet = chunk.content[:SNIPPET_CHARS] + ("..." if len(chunk.content) > SNIPPET_CHARS else "")
    return {
        "source_id": sid,
        "document_id": str(chunk.document_id),
        "document_title": chunk.document_title,
        "chunk_id": str(chunk.chunk_id),
        "page": chunk.page,
        "section": chunk.section,
        "snippet": snippet,
        "similarity": chunk.similarity,
    }


def ask(
    db: Session,
    settings: Settings,
    principal: Principal,
    embedder: EmbeddingProvider,
    llm: LLMProvider,
    question: str,
    conversation_id: uuid.UUID | None,
) -> ChatOutcome:
    started = time.perf_counter()
    question = question.strip()
    now = datetime.now(UTC)
    if conversation_id:
        conversation = get_conversation(db, principal, conversation_id)
    else:
        conversation = Conversation(
            tenant_id=principal.tenant_id, user_id=principal.user_id, title=question[:120]
        )
        db.add(conversation)
        db.flush()
    history = _history(db, conversation, settings.history_messages)

    try:
        retrieval = retrieve(db, settings, principal, embedder, question)
    except ProviderError as exc:
        log.warning("embedding provider error: %s", exc.kind)
        raise ModelUnavailable("The embedding model is unavailable, try again shortly") from exc

    sources = {f"S{i}": chunk for i, chunk in enumerate(retrieval.chunks, start=1)}
    meta: dict[str, Any] = {
        "retrieved": [
            {
                "source_id": sid,
                "chunk_id": str(c.chunk_id),
                "document_id": str(c.document_id),
                "similarity": c.similarity,
                "vector_rank": c.vector_rank,
                "text_rank": c.text_rank,
                "fused_score": c.fused_score,
            }
            for sid, c in sources.items()
        ],
        "candidates": retrieval.candidates,
        "dropped_below_threshold": retrieval.dropped_below_threshold,
        "timings_ms": {"embed": retrieval.embed_ms, "search": retrieval.search_ms},
    }

    answer_text: str = ABSTAIN_MESSAGE
    cited: list[str] = []
    abstained = True
    reason: str | None = "no_relevant_sources"
    if not sources:
        meta["model"] = None
    else:
        passages = []
        for sid, c in sources.items():
            text, removed = quarantine(c.content)
            passages.append(SourcePassage(sid, c.document_title, c.page, c.section, text, removed))
        meta["quarantined_paragraphs"] = sum(p.quarantined for p in passages)
        messages = [
            ChatTurn("system", SYSTEM_PROMPT),
            *history,
            ChatTurn("user", build_user_prompt(question, passages)),
        ]
        try:
            result = llm.generate_json(messages, ANSWER_SCHEMA)
        except ProviderError as exc:
            log.warning("llm provider error: %s", exc.kind)
            if exc.kind == "bad_response":
                result = None
            else:
                raise ModelUnavailable(
                    "The language model is unavailable, try again shortly"
                ) from exc
        if result is None:
            reason = "malformed_output"
            meta["model"] = llm.model
        else:
            validated = validate_answer(result.data, set(sources))
            answer_text, cited = validated.text, validated.cited_ids
            abstained, reason = validated.abstained, validated.reason
            meta["model"] = result.model
            meta["dropped_citations"] = validated.dropped_ids
            meta["timings_ms"]["llm"] = result.duration_ms
            meta["tokens"] = {
                "prompt": result.prompt_tokens,
                "completion": result.completion_tokens,
            }
            meta["llm_detail_ms"] = result.extra

    citations = [_citation(sid, sources[sid]) for sid in cited]
    meta["abstain_reason"] = reason if abstained else None
    meta["total_ms"] = int((time.perf_counter() - started) * 1000)

    user_message = Message(
        tenant_id=principal.tenant_id,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=question,
        citations=[],
        abstained=False,
        meta={},
        created_at=now,
    )
    assistant_message = Message(
        tenant_id=principal.tenant_id,
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content=answer_text,
        citations=citations,
        abstained=abstained,
        meta=meta,
        created_at=datetime.now(UTC),
    )
    db.add_all([user_message, assistant_message])
    conversation.updated_at = datetime.now(UTC)
    db.commit()
    return ChatOutcome(conversation, user_message, assistant_message)
