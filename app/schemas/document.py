"""Pydantic schemas for documents."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    filename: str
    file_type: str
    word_count: int
    content_hash: str
    created_at: datetime


class DocumentDetailResponse(DocumentResponse):
    content: str
