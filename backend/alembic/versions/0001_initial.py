"""Initial schema: tenants, users, documents, chunks (pgvector + full text), chat, audit, RLS.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-18
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("RAG_DB_USER", "rag_app")
EMBEDDING_DIM = 768
TENANT_TABLES = (
    "documents",
    "ingestion_jobs",
    "document_uploads",
    "chunks",
    "conversations",
    "messages",
)
_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _uuid_pk() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True)


def _ts(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _tenant_fk() -> sa.Column:
    return sa.Column(
        "tenant_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    role = sa.Enum("tenant_admin", "member", name="membership_role")
    access = sa.Enum("all_members", "admins_only", name="access_level")
    doc_status = sa.Enum("pending", "processing", "ready", "failed", name="document_status")
    job_status = sa.Enum("queued", "running", "succeeded", "failed", name="job_status")
    msg_role = sa.Enum("user", "assistant", name="message_role")

    op.create_table(
        "tenants",
        _uuid_pk(),
        sa.Column("slug", sa.String(63), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        _ts("created_at"),
    )
    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False),
        _ts("created_at"),
    )
    op.create_table(
        "memberships",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", role, nullable=False),
        _ts("created_at"),
        sa.UniqueConstraint("tenant_id", "user_id"),
    )
    op.create_table(
        "refresh_tokens",
        _uuid_pk(),
        sa.Column("family_id", pg.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _tenant_fk(),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        _ts("expires_at"),
        _ts("revoked_at", nullable=True),
        _ts("created_at"),
    )
    op.create_table(
        "documents",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("access_level", access, nullable=False),
        sa.Column("status", doc_status, nullable=False),
        sa.Column("page_count", sa.Integer),
        sa.Column("chunk_count", sa.Integer, nullable=False),
        sa.Column("embedding_model", sa.String(200)),
        sa.Column("error", sa.String(500)),
        sa.Column(
            "uploaded_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("tenant_id", "sha256", name="uq_documents_tenant_sha256"),
    )
    op.create_index("ix_documents_tenant_created", "documents", ["tenant_id", "created_at"])
    op.create_table(
        "ingestion_jobs",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column(
            "document_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("status", job_status, nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("progress", sa.Integer, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("error", sa.String(500)),
        _ts("created_at"),
        _ts("started_at", nullable=True),
        _ts("finished_at", nullable=True),
    )
    op.create_table(
        "document_uploads",
        sa.Column(
            "job_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("ingestion_jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        _tenant_fk(),
        sa.Column("data", sa.LargeBinary, nullable=False),
    )
    op.create_table(
        "chunks",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column(
            "document_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("page_number", sa.Integer),
        sa.Column("section", sa.String(300)),
        sa.Column("char_start", sa.Integer, nullable=False),
        sa.Column("char_end", sa.Integer, nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("embedding_model", sa.String(200), nullable=False),
        sa.Column(
            "tsv",
            pg.TSVECTOR,
            sa.Computed("to_tsvector('english', content)", persisted=True),
        ),
    )
    op.create_index("ix_chunks_tenant_document", "chunks", ["tenant_id", "document_id"])
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")
    op.create_table(
        "conversations",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index(
        "ix_conversations_owner", "conversations", ["tenant_id", "user_id", "updated_at"]
    )
    op.create_table(
        "messages",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column(
            "conversation_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", msg_role, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("citations", pg.JSONB, nullable=False),
        sa.Column("abstained", sa.Boolean, nullable=False),
        sa.Column("meta", pg.JSONB, nullable=False),
        _ts("created_at"),
    )
    op.create_index("ix_messages_conversation", "messages", ["conversation_id", "created_at"])
    op.create_table(
        "audit_events",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE")
        ),
        sa.Column(
            "actor_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("target_type", sa.String(32)),
        sa.Column("target_id", sa.String(64)),
        sa.Column("ip", sa.String(64)),
        sa.Column("details", pg.JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_audit_tenant_created", "audit_events", ["tenant_id", "created_at"])

    # Row-Level Security: defence in depth behind the application's own tenant filters.
    # FORCE also applies the policies to the table owner.
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = {_TENANT}) WITH CHECK (tenant_id = {_TENANT})"
        )
    # Audit events may be written without a tenant (failed logins for unknown accounts) but
    # are only readable inside their own tenant.
    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_events FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY audit_read ON audit_events FOR SELECT USING (tenant_id = {_TENANT})")
    op.execute(
        "CREATE POLICY audit_write ON audit_events FOR INSERT "
        f"WITH CHECK (tenant_id IS NULL OR tenant_id = {_TENANT})"
    )

    # The application role gets data access only: no DDL, no ownership, no RLS bypass.
    op.execute(f'GRANT USAGE ON SCHEMA public TO "{APP_ROLE}"')
    op.execute(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{APP_ROLE}"'
    )
    op.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{APP_ROLE}"')
    op.execute(f'REVOKE ALL ON alembic_version FROM "{APP_ROLE}"')
    # Audit events are append-only for the application.
    op.execute(f'REVOKE UPDATE, DELETE ON audit_events FROM "{APP_ROLE}"')


def downgrade() -> None:
    for table in (
        "audit_events",
        "messages",
        "conversations",
        "chunks",
        "document_uploads",
        "ingestion_jobs",
        "documents",
        "refresh_tokens",
        "memberships",
        "users",
        "tenants",
    ):
        op.drop_table(table)
    for enum_name in (
        "message_role",
        "job_status",
        "document_status",
        "access_level",
        "membership_role",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
