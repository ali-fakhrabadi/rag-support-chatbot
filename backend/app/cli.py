"""Operator commands.

    python -m app.cli create-workspace --slug acme --name "Acme" --admin-email a@acme.example
    python -m app.cli add-user --workspace acme --email b@acme.example --role member
    python -m app.cli reindex --workspace acme      # re-embed all chunks with the current model

Passwords are read from RAG_CLI_PASSWORD or prompted for; they are never accepted as arguments.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import select, update

from app.core.config import get_settings
from app.db.models import Chunk, Document, Role, Tenant
from app.db.session import bind_tenant, session_scope
from app.providers.embeddings import get_embedding_provider
from app.services import tenants


def _password() -> str:
    password = os.environ.get("RAG_CLI_PASSWORD") or getpass.getpass("Password: ")
    if not password:
        sys.exit("A password is required")
    return password


def create_workspace(args: argparse.Namespace) -> None:
    with session_scope() as db:
        tenant = tenants.create_tenant(db, args.slug, args.name)
        user = tenants.upsert_user(db, args.admin_email, _password(), args.full_name or "")
        tenants.add_membership(db, tenant, user, Role.TENANT_ADMIN)
    print(f"workspace {args.slug} created with admin {args.admin_email}")


def add_user(args: argparse.Namespace) -> None:
    with session_scope() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == args.workspace))
        if tenant is None:
            sys.exit("Unknown workspace")
        user = tenants.upsert_user(db, args.email, _password(), args.full_name or "")
        tenants.add_membership(db, tenant, user, Role(args.role))
    print(f"{args.email} added to {args.workspace} as {args.role}")


def reindex(args: argparse.Namespace) -> None:
    settings = get_settings()
    embedder = get_embedding_provider(settings)
    with session_scope() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == args.workspace))
        if tenant is None:
            sys.exit("Unknown workspace")
        bind_tenant(db, tenant.id)
        documents = db.scalars(select(Document).where(Document.tenant_id == tenant.id)).all()
        for document in documents:
            chunks = db.scalars(
                select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.ordinal)
            ).all()
            texts = [
                "\n".join(filter(None, [document.title, c.section, c.content])) for c in chunks
            ]
            vectors = embedder.embed_documents(texts) if texts else []
            for chunk, vector in zip(chunks, vectors, strict=True):
                chunk.embedding, chunk.embedding_model = vector, embedder.model
            db.execute(
                update(Document)
                .where(Document.id == document.id)
                .values(embedding_model=embedder.model)
            )
            print(f"re-embedded {len(chunks)} chunks of {document.id}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(required=True)
    p = sub.add_parser("create-workspace")
    p.add_argument("--slug", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--admin-email", required=True)
    p.add_argument("--full-name")
    p.set_defaults(func=create_workspace)
    p = sub.add_parser("add-user")
    p.add_argument("--workspace", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--full-name")
    p.add_argument("--role", choices=[r.value for r in Role], default=Role.MEMBER.value)
    p.set_defaults(func=add_user)
    p = sub.add_parser("reindex")
    p.add_argument("--workspace", required=True)
    p.set_defaults(func=reindex)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
