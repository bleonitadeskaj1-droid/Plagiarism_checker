"""Admin AI assistant — responses grounded in live database stats."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import require_admin
from database import check_db_connection, get_db
from models import Thesis, User, UserRole
from routers.agent_helpers import build_agent_reply, build_thesis_stats

router = APIRouter()


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class AgentChatResponse(BaseModel):
    reply: str


def _admin_extras(db: Session) -> dict:
    conf_docs = 0
    try:
        from models import ConfidentialDocument, DocStatus

        conf_docs = (
            db.query(func.count(ConfidentialDocument.id))
            .filter(ConfidentialDocument.status == DocStatus.active)
            .scalar()
            or 0
        )
    except Exception:
        pass

    dept_rows = (
        db.query(Thesis.department, func.count(Thesis.id))
        .filter(Thesis.is_archive.is_(False), Thesis.department.isnot(None))
        .group_by(Thesis.department)
        .order_by(func.count(Thesis.id).desc())
        .limit(10)
        .all()
    )
    departments = [f"• {d or 'Pa departament'}: {c} tema" for d, c in dept_rows]

    return {
        "total_users": db.query(func.count(User.id)).scalar() or 0,
        "students": db.query(func.count(User.id)).filter(User.role == UserRole.student).scalar() or 0,
        "professors": db.query(func.count(User.id)).filter(User.role == UserRole.professor).scalar() or 0,
        "conf_docs": int(conf_docs),
        "departments": departments,
        "db_ok": check_db_connection().get("ok", False),
    }


@router.post("/agent/chat", response_model=AgentChatResponse)
def admin_agent_chat(
    payload: AgentChatRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    stats = build_thesis_stats(db, professor_id=None)
    extras = _admin_extras(db)
    name = admin.full_name or admin.username or "Admin"
    reply = build_agent_reply(
        payload.message,
        stats,
        audience="admin",
        name=name,
        department=admin.department,
        extras=extras,
        db=db,
    )
    return AgentChatResponse(reply=reply)
