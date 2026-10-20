"""Admin audit log API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from auth import require_admin
from database import get_db
from models import User
from services.audit_service import list_audit_logs

router = APIRouter()


@router.get("/audit")
def get_audit_logs(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    role: str | None = None,
    action: str | None = None,
    user_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    return list_audit_logs(
        db,
        limit=limit,
        offset=offset,
        role=role,
        action=action,
        user_id=user_id,
    )
