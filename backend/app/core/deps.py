"""Request dependencies: database session, caller identity, tenant scope and role checks.

The caller's role is read from the membership row on every request, never from token claims,
so removing a member or demoting an admin takes effect immediately.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import Forbidden, Unauthorized
from app.core.tokens import TokenError, decode_access_token
from app.db.models import Membership, Role, User
from app.db.session import bind_tenant, get_sessionmaker
from app.services import audit

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: Role
    email: str

    @property
    def is_admin(self) -> bool:
        return self.role is Role.TENANT_ADMIN


def get_db() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def client_ip(request: Request, settings: Settings = Depends(get_settings)) -> str:
    if settings.trust_proxy_headers:
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()[:64]
    return request.client.host if request.client else "unknown"


def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise Unauthorized("Missing bearer token")
    try:
        claims = decode_access_token(settings, credentials.credentials)
    except TokenError as exc:
        raise Unauthorized("Invalid or expired token") from exc
    row = db.execute(
        select(Membership.role, User.email)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.user_id == claims.user_id,
            Membership.tenant_id == claims.tenant_id,
            User.is_active.is_(True),
        )
    ).one_or_none()
    if row is None:
        raise Unauthorized("Account or membership no longer active")
    bind_tenant(db, claims.tenant_id)
    request.state.tenant_id = str(claims.tenant_id)
    request.state.user_id = str(claims.user_id)
    return Principal(claims.user_id, claims.tenant_id, row.role, row.email)


def require_admin(
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    ip: str = Depends(client_ip),
) -> Principal:
    if not principal.is_admin:
        audit.record(
            db,
            "authz.admin_required",
            outcome="denied",
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            ip=ip,
            details={"method": request.method, "path": request.url.path},
        )
        db.commit()
        raise Forbidden("Tenant admin role required")
    return principal
