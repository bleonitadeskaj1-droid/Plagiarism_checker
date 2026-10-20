"""Check result repository."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.check_result import CheckResult
from app.repositories.base import BaseRepository


class CheckResultRepository(BaseRepository[CheckResult]):
    """Data access for plagiarism check results."""

    def __init__(self, session: Session) -> None:
        super().__init__(session, CheckResult)

    def get_by_document(self, document_id: int) -> list[CheckResult]:
        """Return all checks for a document."""
        stmt = (
            select(CheckResult)
            .where(CheckResult.document_id == document_id)
            .order_by(CheckResult.created_at.desc())
        )
        return list(self.session.scalars(stmt).all())
