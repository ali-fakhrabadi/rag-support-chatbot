"""Append-only audit trail for security-sensitive operations.

Details must never contain secrets, passwords, tokens or document text.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AuditEvent

log = logging.getLogger("rag.audit")


def record(
    db: Session,
    action: str,
    *,
    outcome: str = "success",
    tenant_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    ip: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            action=action,
            outcome=outcome,
            target_type=target_type,
            target_id=str(target_id) if target_id else None,
            ip=ip,
            details=details or {},
        )
    )
    log.info(
        "audit",
        extra={
            "action": action,
            "outcome": outcome,
            "tenant_id": str(tenant_id) if tenant_id else None,
            "actor": str(actor_user_id) if actor_user_id else None,
            "target": f"{target_type}:{target_id}" if target_type else None,
        },
    )
