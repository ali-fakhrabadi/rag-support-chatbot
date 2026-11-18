"""Alembic environment. Migrations run as the database owner, never as the application role."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from alembic import context
from app.db.models import Base


def _secret(name: str) -> str:
    path = os.environ.get(f"{name}_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return os.environ.get(name, "")


def _url() -> URL:
    return URL.create(
        "postgresql+psycopg",
        username=os.environ.get("RAG_DB_ADMIN_USER", "rag_owner"),
        password=_secret("RAG_DB_ADMIN_PASSWORD"),
        host=os.environ.get("RAG_DB_HOST", "postgres"),
        port=int(os.environ.get("RAG_DB_PORT", "5432")),
        database=os.environ.get("RAG_DB_NAME", "rag"),
    )


def run_migrations() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations()
