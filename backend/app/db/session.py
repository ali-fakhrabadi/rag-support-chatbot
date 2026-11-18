"""Engine, sessions and the per-transaction tenant context used by Row-Level Security."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from app.core.config import get_settings

TENANT_KEY = "tenant_id"


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_size,
        connect_args={"application_name": "rag-support-chatbot"},
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False)


def _apply_tenant(connection: Connection, tenant_id: uuid.UUID | None) -> None:
    # set_config(..., true) is transaction-local; an empty value matches no rows in the policies.
    connection.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"),
        {"tid": str(tenant_id) if tenant_id else ""},
    )


@event.listens_for(Session, "after_begin")
def _set_tenant_on_begin(
    session: Session, _transaction: SessionTransaction, connection: Connection
) -> None:
    _apply_tenant(connection, session.info.get(TENANT_KEY))


def bind_tenant(session: Session, tenant_id: uuid.UUID) -> None:
    """Scope this session (current and later transactions) to one tenant."""
    session.info[TENANT_KEY] = tenant_id
    if session.in_transaction():
        _apply_tenant(session.connection(), tenant_id)


@contextmanager
def session_scope(tenant_id: uuid.UUID | None = None) -> Iterator[Session]:
    session = get_sessionmaker()()
    if tenant_id is not None:
        session.info[TENANT_KEY] = tenant_id
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
