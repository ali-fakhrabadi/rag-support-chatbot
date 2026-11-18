"""Login, refresh-token rotation (with reuse detection) and logout."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import Unauthorized
from app.core.passwords import hash_password, needs_rehash, verify_password
from app.core.tokens import create_access_token, hash_refresh_token, new_refresh_token
from app.db.models import Membership, RefreshToken, Tenant, User
from app.db.session import bind_tenant
from app.services import audit

_GENERIC = "Invalid email, password or workspace"


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    tenant_id: uuid.UUID


def _issue(
    db: Session,
    settings: Settings,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    family_id: uuid.UUID | None = None,
) -> TokenPair:
    access, expires_in = create_access_token(settings, user_id, tenant_id)
    refresh, digest = new_refresh_token()
    db.add(
        RefreshToken(
            family_id=family_id or uuid.uuid4(),
            user_id=user_id,
            tenant_id=tenant_id,
            token_hash=digest,
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
        )
    )
    return TokenPair(access, refresh, expires_in, tenant_id)


def login(
    db: Session, settings: Settings, email: str, password: str, tenant_slug: str | None, ip: str
) -> TokenPair:
    email = email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    password_ok = verify_password(password, user.password_hash if user else None)

    membership = None
    if user is not None and password_ok and user.is_active:
        query = select(Membership).join(Tenant).where(Membership.user_id == user.id)
        if tenant_slug:
            query = query.where(Tenant.slug == tenant_slug.strip().lower())
        memberships = db.scalars(query.order_by(Membership.created_at)).all()
        # Without a workspace slug, a user with several memberships must choose one.
        if len(memberships) == 1 or (tenant_slug and memberships):
            membership = memberships[0]

    if membership is None:
        audit.record(
            db,
            "auth.login",
            outcome="failure",
            actor_user_id=user.id if user else None,
            ip=ip,
            details={"reason": "bad_credentials_or_workspace"},
        )
        db.commit()
        raise Unauthorized(_GENERIC)

    assert user is not None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    bind_tenant(db, membership.tenant_id)
    pair = _issue(db, settings, user.id, membership.tenant_id)
    audit.record(db, "auth.login", tenant_id=membership.tenant_id, actor_user_id=user.id, ip=ip)
    db.commit()
    return pair


def refresh(db: Session, settings: Settings, token: str, ip: str) -> TokenPair:
    now = datetime.now(UTC)
    row = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(token))
    )
    if row is None:
        raise Unauthorized("Invalid refresh token")
    bind_tenant(db, row.tenant_id)
    if row.revoked_at is not None:
        # A rotated token was presented again: assume theft and revoke the whole family.
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        audit.record(
            db,
            "auth.refresh_reuse",
            outcome="denied",
            tenant_id=row.tenant_id,
            actor_user_id=row.user_id,
            ip=ip,
        )
        db.commit()
        raise Unauthorized("Refresh token reuse detected; please sign in again")
    if row.expires_at <= now:
        raise Unauthorized("Refresh token expired")
    active = db.scalar(
        select(Membership.id)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.user_id == row.user_id,
            Membership.tenant_id == row.tenant_id,
            User.is_active.is_(True),
        )
    )
    row.revoked_at = now
    if active is None:
        db.commit()
        raise Unauthorized("Account or membership no longer active")
    pair = _issue(db, settings, row.user_id, row.tenant_id, family_id=row.family_id)
    db.commit()
    return pair


def logout(db: Session, token: str, user_id: uuid.UUID, ip: str) -> None:
    row = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(token))
    )
    if row is not None and row.user_id == user_id:
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        audit.record(db, "auth.logout", tenant_id=row.tenant_id, actor_user_id=user_id, ip=ip)
    db.commit()
