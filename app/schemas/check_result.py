"""Pydantic schemas for plagiarism checks."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CheckRunRequest(BaseModel):
    document_id: int = Field(gt=0)


class MatchItem(BaseModel):
    document_id: int
    title: str
    similarity: float
    excerpt: str


class CheckResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    similarity_score: float
    risk_level: str
    status: str
    summary: str | None = None
    matches: list[dict[str, Any]] | None = None
    created_at: datetime
    completed_at: datetime | None = None


class CheckReportResponse(BaseModel):
    check_id: int
    document_id: int
    similarity_score: float
    risk_level: str
    summary: str | None
    matches: list[dict[str, Any]]
    report: dict[str, Any]


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    document_id: int | None = None


class AgentChatResponse(BaseModel):
    reply: str
    tools_used: list[str] = Field(default_factory=list)
