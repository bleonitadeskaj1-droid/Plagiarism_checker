"""Professor AI assistant — responses grounded in assigned-thesis database stats."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from access_control import ensure_professor_profile
from auth import get_current_user
from database import get_db
from models import User, UserRole
from routers.agent_helpers import build_agent_reply, build_thesis_stats

router = APIRouter()


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class AgentChatResponse(BaseModel):
    reply: str


def _require_professor(user: User) -> None:
    if user.role != UserRole.professor:
        raise HTTPException(status_code=403, detail="Ky endpoint kërkon rolin 'professor'")


@router.post("/professor/agent/chat", response_model=AgentChatResponse)
def professor_agent_chat(
    payload: AgentChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_professor(current_user)
    profile = ensure_professor_profile(db, current_user, create_if_missing=True)
    if not profile:
        raise HTTPException(status_code=403, detail="Profili i profesorit nuk u gjet")

    stats = build_thesis_stats(db, professor_id=profile.id)
    professor_name = profile.full_name or current_user.full_name or current_user.username
    department = profile.department or current_user.department
    reply = build_agent_reply(
        payload.message,
        stats,
        audience="professor",
        name=professor_name,
        department=department,
        db=db,
        professor_id=profile.id,
    )
    return AgentChatResponse(reply=reply)
