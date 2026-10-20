"""Agent chat API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agent.agent import get_agent
from app.dependencies import get_db
from app.repositories.document_repo import DocumentRepository
from app.schemas.check_result import AgentChatRequest, AgentChatResponse
from auth import get_current_user
from models import User

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/chat", response_model=AgentChatResponse)
def agent_chat(
    payload: AgentChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentChatResponse:
    """Chat with the plagiarism analysis agent."""
    document_text = None
    if payload.document_id:
        doc = DocumentRepository(db).get(payload.document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        document_text = doc.content

    agent = get_agent(db)
    result = agent.chat(payload.message, document_text=document_text)
    return AgentChatResponse(reply=result["reply"], tools_used=result.get("tools_used", []))
