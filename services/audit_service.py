"""Audit trail for admin and professor mutations."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def record_audit(
    db: Session,
    *,
    user_id: int | None,
    username: str | None,
    role: str | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    method: str | None = None,
    path: str | None = None,
    status_code: int | None = None,
    ip_address: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Persist one audit event. Failures must never break the main request."""
    try:
        from models import AuditLog

        entry = AuditLog(
            user_id=user_id,
            username=(username or "")[:100] or None,
            role=(role or "")[:50] or None,
            action=(action or "unknown")[:100],
            resource_type=(resource_type or "")[:100] or None,
            resource_id=(str(resource_id) if resource_id is not None else None),
            method=(method or "")[:10] or None,
            path=(path or "")[:500] or None,
            status_code=status_code,
            ip_address=(ip_address or "")[:64] or None,
            details=json.dumps(details, ensure_ascii=False)[:8000] if details else None,
        )
        db.add(entry)
        db.commit()
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning("Audit write failed: %s", exc)


def list_audit_logs(
    db: Session,
    *,
    limit: int = 100,
    offset: int = 0,
    role: str | None = None,
    action: str | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    from models import AuditLog

    q = db.query(AuditLog).order_by(AuditLog.created_at.desc())
    if role:
        q = q.filter(AuditLog.role == role)
    if action:
        q = q.filter(AuditLog.action.contains(action))
    if user_id is not None:
        q = q.filter(AuditLog.user_id == user_id)

    total = q.count()
    rows = q.offset(max(0, offset)).limit(min(max(limit, 1), 500)).all()
    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "username": r.username,
                "role": r.role,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "method": r.method,
                "path": r.path,
                "status_code": r.status_code,
                "ip_address": r.ip_address,
                "details": r.details,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }
