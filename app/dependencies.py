"""FastAPI dependency injection."""

from collections.abc import Generator

from sqlalchemy.orm import Session

from app.database.engine import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """Yield a database session and close it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
