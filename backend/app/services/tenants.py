"""Tenants, users and memberships."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError, Conflict, NotFound
from app.core.passwords import hash_password, validate_password_strength
from app.db.models import Membership, RefreshToken, Role, Tenant, User
from app.db.session import bind_tenant
from app.services import audit

_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$")


def create_tenant(db: Session, slug: str, name: str) -> Tenant:
    slug = slug.strip().lower()
    if not _SLUG.match(slug):
        raise AppError("Workspace slug must be 3-63 lowercase letters, digits or hyphens")
    if db.scalar(select(Tenant.id).where(Tenant.slug == slug)):
        raise Conflict("Workspace already exists")
    tenant = Tenant(slug=slug, name=name.strip()[:200] or slug)
    db.add(tenant)
    db.flush()
    return tenant


def upsert_user(db: Session, email: str, password: str, full_name: str = "") -> User:
    email = email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        validate_password_strength(password)
        user = User(
            email=email,
            full_name=full_name.strip()[:200],
            password_hash=hash_password(password),
            is_active=True,
        )
        db.add(user)
        db.flush()
    return user


def set_password(db: Session, email: str, password: str) -> User:
    """Operator reset: replace the password and revoke every refresh token of the user."""
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        raise NotFound("Unknown user")
    validate_password_strength(password)
    user.password_hash = hash_password(password)
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    db.flush()
    return user


def add_membership(db: Session, tenant: Tenant, user: User, role: Role) -> Membership:
    existing = db.scalar(
        select(Membership).where(Membership.tenant_id == tenant.id, Membership.user_id == user.id)
    )
    if existing:
        raise Conflict("User is already a member of this workspace")
    membership = Membership(tenant_id=tenant.id, user_id=user.id, role=role)
    db.add(membership)
    db.flush()
    return membership


def list_members(db: Session, principal: Principal) -> list[Membership]:
    return list(
        db.scalars(
            select(Membership)
            .where(Membership.tenant_id == principal.tenant_id)
            .order_by(Membership.created_at)
        ).unique()
    )


def invite_member(
    db: Session,
    principal: Principal,
    email: str,
    full_name: str,
    password: str,
    role: Role,
    ip: str,
) -> Membership:
    """Create the account if needed and add it to the caller's workspace (admins only)."""
    tenant = db.get(Tenant, principal.tenant_id)
    assert tenant is not None
    email = email.strip().lower()
    existing = db.scalar(select(User).where(User.email == email))
    try:
        user = existing or upsert_user(db, email, password, full_name)
    except ValueError as exc:
        raise AppError(str(exc), code="weak_password", status_code=422) from exc
    membership = add_membership(db, tenant, user, role)
    bind_tenant(db, principal.tenant_id)
    audit.record(
        db,
        "member.add",
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        target_type="user",
        target_id=user.id,
        ip=ip,
        details={"role": role.value},
    )
    db.commit()
    return membership


def _get_member(db: Session, principal: Principal, user_id: uuid.UUID) -> Membership:
    membership = db.scalar(
        select(Membership).where(
            Membership.tenant_id == principal.tenant_id, Membership.user_id == user_id
        )
    )
    if membership is None:
        raise NotFound("Member not found")
    return membership


def _admin_count(db: Session, tenant_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count()).where(
                Membership.tenant_id == tenant_id, Membership.role == Role.TENANT_ADMIN
            )
        )
        or 0
    )


def change_role(
    db: Session, principal: Principal, user_id: uuid.UUID, role: Role, ip: str
) -> Membership:
    membership = _get_member(db, principal, user_id)
    if (
        membership.role is Role.TENANT_ADMIN
        and role is Role.MEMBER
        and _admin_count(db, principal.tenant_id) <= 1
    ):
        raise Conflict("A workspace needs at least one admin")
    previous = membership.role
    membership.role = role
    audit.record(
        db,
        "member.role_change",
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        target_type="user",
        target_id=user_id,
        ip=ip,
        details={"from": previous.value, "to": role.value},
    )
    db.commit()
    return membership


def remove_member(db: Session, principal: Principal, user_id: uuid.UUID, ip: str) -> None:
    membership = _get_member(db, principal, user_id)
    if membership.role is Role.TENANT_ADMIN and _admin_count(db, principal.tenant_id) <= 1:
        raise Conflict("A workspace needs at least one admin")
    db.delete(membership)
    audit.record(
        db,
        "member.remove",
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        target_type="user",
        target_id=user_id,
        ip=ip,
    )
    db.commit()
