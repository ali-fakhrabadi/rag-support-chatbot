"""Fixtures for tests that run against REAL PostgreSQL (pgvector) and Redis.

A disposable database (``rag_test`` by default) is created with the owner role, migrated with
Alembic, and used through the non-owner application role, exactly like production. Required
environment (see .github/workflows/ci.yml):

    RAG_DB_HOST, RAG_DB_PORT, RAG_DB_ADMIN_USER, RAG_DB_ADMIN_PASSWORD, RAG_DB_PASSWORD,
    RAG_REDIS_URL

Embeddings use the deterministic ``hashing`` provider so these tests need no model server.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine

BACKEND = Path(__file__).resolve().parents[2]
TEST_DB = os.environ.get("RAG_TEST_DB_NAME", "rag_test")
os.environ.update(
    {
        "RAG_ENVIRONMENT": "test",
        "RAG_DB_NAME": TEST_DB,
        "RAG_DB_USER": os.environ.get("RAG_DB_USER", "rag_app"),
        "RAG_JWT_SECRET": os.environ.get("RAG_JWT_SECRET", "integration-test-secret-" + "x" * 24),
        "RAG_EMBEDDING_PROVIDER": "hashing",
        "RAG_MIN_SIMILARITY": "0.05",
        "RAG_RATE_LIMIT_LOGIN_PER_MINUTE": "1000",
        "RAG_RATE_LIMIT_CHAT_PER_MINUTE": "1000",
        "RAG_RATE_LIMIT_UPLOAD_PER_MINUTE": "1000",
        "RAG_CHUNK_SIZE": "400",
        "RAG_CHUNK_OVERLAP": "60",
        "RAG_MAX_UPLOAD_MB": "1",
    }
)

pytestmark = pytest.mark.integration


def _admin_url(database: str) -> URL:
    return URL.create(
        "postgresql+psycopg",
        username=os.environ["RAG_DB_ADMIN_USER"],
        password=os.environ["RAG_DB_ADMIN_PASSWORD"],
        host=os.environ.get("RAG_DB_HOST", "localhost"),
        port=int(os.environ.get("RAG_DB_PORT", "5432")),
        database=database,
    )


@pytest.fixture(scope="session")
def admin_engine() -> Iterator[Engine]:
    """Owner connection to the test database (bypasses nothing: used only for setup/cleanup)."""
    app_role = os.environ["RAG_DB_USER"]
    app_password = os.environ["RAG_DB_PASSWORD"]
    server = create_engine(_admin_url("postgres"), isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
        exists = conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": app_role})
        verb = "ALTER" if exists else "CREATE"
        conn.execute(
            text(
                f'{verb} ROLE "{app_role}" LOGIN NOSUPERUSER NOBYPASSRLS '
                f"PASSWORD '{app_password.replace(chr(39), chr(39) * 2)}'"
            )
        )
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{TEST_DB}" TO "{app_role}"'))
    server.dispose()

    env = {**os.environ, "RAG_DB_NAME": TEST_DB}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True
    )
    engine = create_engine(_admin_url(TEST_DB))
    yield engine
    engine.dispose()


TABLES = (
    "audit_events, messages, conversations, chunks, document_uploads, ingestion_jobs, documents, "
    "refresh_tokens, memberships, users, tenants"
)


@pytest.fixture
def clean_db(admin_engine: Engine) -> Iterator[Engine]:
    from app.core.config import get_settings
    from app.core.ratelimit import get_redis

    get_settings.cache_clear()
    with admin_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    get_redis().flushdb()
    yield admin_engine


@dataclass
class Account:
    tenant_id: uuid.UUID
    tenant_slug: str
    user_id: uuid.UUID
    email: str
    password: str
    role: str


@pytest.fixture
def make_account(clean_db: Engine) -> Callable[..., Account]:
    """Create a workspace member through the application services (application role)."""
    from app.db.models import Role, Tenant
    from app.db.session import session_scope
    from app.services import tenants

    def _make(slug: str, email: str, role: str = "tenant_admin") -> Account:
        password = f"pw-{uuid.uuid4().hex}"
        with session_scope() as db:
            tenant = db.query(Tenant).filter(Tenant.slug == slug).one_or_none()
            if tenant is None:
                tenant = tenants.create_tenant(db, slug, slug.title())
            user = tenants.upsert_user(db, email, password)
            tenants.add_membership(db, tenant, user, Role(role))
            return Account(tenant.id, slug, user.id, email, password, role)

    return _make


@pytest.fixture(scope="session")
def celery_worker() -> Iterator[subprocess.Popen[bytes]]:
    """A real Celery worker process consuming from the real Redis broker."""
    env = {**os.environ, "RAG_DB_NAME": TEST_DB}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.worker",
            "worker",
            "--loglevel=WARNING",
            "--concurrency=2",
            "-Q",
            "ingestion",
            "--without-gossip",
            "--without-mingle",
            "--hostname",
            f"test-{uuid.uuid4().hex[:6]}@%h",
        ],
        cwd=BACKEND,
        env=env,
    )
    time.sleep(4)
    assert proc.poll() is None, "celery worker exited early"
    yield proc
    proc.terminate()
    proc.wait(timeout=20)


@pytest.fixture
def api(clean_db: Engine) -> Iterator[object]:
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client
