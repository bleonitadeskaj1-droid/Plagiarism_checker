# ============================================
# database.py - SQLAlchemy setup and session
# ============================================

import os
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.sql import text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError


def _build_database_url() -> str:
    """Build a MySQL connection string unless DATABASE_URL is explicitly set."""
    explicit_url = os.getenv("DATABASE_URL")
    if explicit_url:
        return explicit_url

    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = os.getenv("MYSQL_PORT", "3306")
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "")
    database = os.getenv("MYSQL_DATABASE", "plagiarism_checker")

    return (
        "mysql+pymysql://"
        f"{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{database}?charset=utf8mb4"
    )


def _build_sqlite_fallback_url() -> str:
    db_path = Path(__file__).with_name("plagiarism_checker.db")
    return f"sqlite:///{db_path.as_posix()}"


DATABASE_URL = _build_database_url()
FALLBACK_DATABASE_URL = _build_sqlite_fallback_url()


def _engine_options(database_url: str) -> dict:
    return {"connect_args": {"check_same_thread": False}} if database_url.startswith("sqlite") else {}


def _bootstrap_mysql_database(database_url: str) -> None:
    """Create the target MySQL database if it does not already exist."""
    url = make_url(database_url)
    if not url.drivername.startswith("mysql") or not url.database:
        return

    admin_url = url.set(database=None)
    bootstrap_engine = create_engine(admin_url, pool_pre_ping=True)
    try:
        with bootstrap_engine.connect() as connection:
            connection.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{url.database}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
    finally:
        bootstrap_engine.dispose()


if DATABASE_URL.startswith("mysql"):
    try:
        _bootstrap_mysql_database(DATABASE_URL)
        engine = create_engine(
            DATABASE_URL,
            **_engine_options(DATABASE_URL),
            pool_pre_ping=True,
            pool_recycle=3600,
        )
    except (ImportError, OperationalError):
        engine = create_engine(
            FALLBACK_DATABASE_URL,
            **_engine_options(FALLBACK_DATABASE_URL),
            pool_pre_ping=True,
        )
else:
    engine = create_engine(
        DATABASE_URL or FALLBACK_DATABASE_URL,
        **_engine_options(DATABASE_URL or FALLBACK_DATABASE_URL),
        pool_pre_ping=True,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()