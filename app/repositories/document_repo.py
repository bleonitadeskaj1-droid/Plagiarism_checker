"""Document repository."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document
from app.repositories.base import BaseRepository


class DocumentRepository(BaseRepository[Document]):
    """Data access for documents."""

    def __init__(self, session: Session) -> None:
        super().__init__(session, Document)

    def get_by_hash(self, content_hash: str) -> Document | None:
        """Find document by content hash."""
        stmt = select(Document).where(Document.content_hash == content_hash)
        return self.session.scalars(stmt).first()

    def get_all_with_content(self, skip: int = 0, limit: int = 100) -> list[Document]:
        """Return documents including text content."""
        return self.get_all(skip=skip, limit=limit)
