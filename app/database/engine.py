"""Database engine and session factory — the only place for DB connection setup."""

import logging

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

_engine_kwargs: dict = {
    "pool_pre_ping": True,
    "pool_recycle": 1800,
    "echo": settings.db_echo,
}

if settings.database_url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs["pool_size"] = settings.db_pool_size
    _engine_kwargs["max_overflow"] = settings.db_max_overflow

engine: Engine = create_engine(settings.database_url, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def verify_connection() -> None:
    """Fail fast at startup if the database is unreachable."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        logger.info("Database connection OK: %s", _safe_url(settings.database_url))
    except Exception as exc:
        logger.error("Database connection failed: %s", exc)
        raise RuntimeError(
            f"Cannot connect to database ({settings.database_url.split('@')[-1]}). "
            "Check DATABASE_URL and ensure the server is running."
        ) from exc


def _safe_url(url: str) -> str:
    if "@" in url:
        return url.split("@", 1)[-1]
    return url


def get_session() -> Session:
    return SessionLocal()
