"""Repository package."""

from app.repositories.base import BaseRepository
from app.repositories.document_repo import DocumentRepository
from app.repositories.result_repo import CheckResultRepository

__all__ = ["BaseRepository", "DocumentRepository", "CheckResultRepository"]
