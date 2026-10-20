"""Legacy database layer for auth, theses, and dashboards."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent


def _mysql_url() -> str:
    host = os.getenv("MYSQL_HOST", "localhost")
    port = os.getenv("MYSQL_PORT", "3306")
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "")
    database = os.getenv("MYSQL_DATABASE", "plagiarism_checker")
    return (
        "mysql+pymysql://"
        f"{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{database}?charset=utf8mb4"
    )


def _build_database_url() -> str:
    """Legacy DB — always MySQL unless LEGACY_DATABASE_URL is set explicitly."""
    explicit = os.getenv("LEGACY_DATABASE_URL")
    if explicit:
        return explicit
    return _mysql_url()


def _sqlite_fallback_url() -> str:
    return f"sqlite:///{(PROJECT_ROOT / 'plagiarism_checker.db').as_posix()}"


DATABASE_URL = _build_database_url()
FALLBACK_DATABASE_URL = _sqlite_fallback_url()


def _engine_options(url: str) -> dict:
    return {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}


def _bootstrap_mysql(url: str) -> None:
    parsed = make_url(url)
    if not parsed.drivername.startswith("mysql") or not parsed.database:
        return
    admin_engine = create_engine(parsed.set(database=None), pool_pre_ping=True)
    try:
        with admin_engine.connect() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{parsed.database}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
            conn.commit()
    finally:
        admin_engine.dispose()


def _create_engine(url: str):
    opts = dict(_engine_options(url))
    if url.startswith("mysql"):
        opts.update(
            pool_size=10,
            max_overflow=20,
            pool_timeout=60,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    else:
        opts.update(pool_pre_ping=True, pool_recycle=3600)
    return create_engine(url, **opts)


def _connect_engine(url: str):
    if url.startswith("mysql"):
        try:
            import pymysql  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "PyMySQL is required for MySQL. Install with: pip install PyMySQL"
            ) from exc
        _bootstrap_mysql(url)
    engine = _create_engine(url)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return engine


def _allow_sqlite_fallback() -> bool:
    return os.getenv("LEGACY_ALLOW_SQLITE_FALLBACK", "").lower() in {"1", "true", "yes"}


def _is_driver_or_connection_error(exc: Exception) -> bool:
    if isinstance(exc, ImportError):
        return True
    name = exc.__class__.__name__
    return name in {"OperationalError", "InterfaceError", "DatabaseError"}


def ensure_schema_extensions(bind=None) -> None:
    """Apply incremental schema fixes for databases created from older scripts."""
    bind = bind or engine
    inspector = inspect(bind)
    dialect = bind.dialect.name
    tables = set(inspector.get_table_names())

    with bind.begin() as connection:
        if "theses" in tables:
            thesis_columns = {column["name"] for column in inspector.get_columns("theses")}
            int_type = "INTEGER" if dialect == "sqlite" else "INT"
            bool_type = "BOOLEAN" if dialect == "sqlite" else "TINYINT(1)"

            if "assigned_professor_id" not in thesis_columns:
                connection.execute(
                    text(f"ALTER TABLE theses ADD COLUMN assigned_professor_id {int_type} NULL")
                )
            if "submitted_by_user_id" not in thesis_columns:
                connection.execute(
                    text(f"ALTER TABLE theses ADD COLUMN submitted_by_user_id {int_type} NULL")
                )
            if "workflow_status" not in thesis_columns:
                if dialect == "sqlite":
                    connection.execute(
                        text(
                            "ALTER TABLE theses ADD COLUMN workflow_status "
                            "VARCHAR(50) NOT NULL DEFAULT 'in_process'"
                        )
                    )
                else:
                    connection.execute(
                        text(
                            "ALTER TABLE theses ADD COLUMN workflow_status "
                            "ENUM('in_process','approved','rejected','needs_revision') "
                            "NOT NULL DEFAULT 'in_process'"
                        )
                    )
            if "is_archive" not in thesis_columns:
                connection.execute(
                    text(
                        f"ALTER TABLE theses ADD COLUMN is_archive {bool_type} "
                        "NOT NULL DEFAULT 0"
                    )
                )

        if "students" in tables:
            student_columns = {column["name"] for column in inspector.get_columns("students")}
            if "user_id" not in student_columns:
                int_type = "INTEGER" if dialect == "sqlite" else "INT"
                connection.execute(
                    text(f"ALTER TABLE students ADD COLUMN user_id {int_type} NULL")
                )

        if "plagiarism_results" in tables:
            result_columns = {column["name"] for column in inspector.get_columns("plagiarism_results")}
            if "risk_level" not in result_columns:
                connection.execute(
                    text("ALTER TABLE plagiarism_results ADD COLUMN risk_level VARCHAR(20) NULL")
                )

        if "system_settings" not in tables:
            if dialect == "sqlite":
                connection.execute(
                    text(
                        "CREATE TABLE system_settings ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "group_name VARCHAR(100) NOT NULL DEFAULT 'admin_panel', "
                        "setting_key VARCHAR(100) NOT NULL, "
                        "setting_value TEXT, "
                        "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                        "UNIQUE (group_name, setting_key)"
                        ")"
                    )
                )
            else:
                connection.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS system_settings ("
                        "id INT AUTO_INCREMENT PRIMARY KEY, "
                        "group_name VARCHAR(100) NOT NULL DEFAULT 'admin_panel', "
                        "setting_key VARCHAR(100) NOT NULL, "
                        "setting_value TEXT, "
                        "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP "
                        "ON UPDATE CURRENT_TIMESTAMP, "
                        "UNIQUE KEY uq_system_settings_group_key (group_name, setting_key)"
                        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
                    )
                )
        elif "system_settings" in tables:
            settings_columns = {column["name"] for column in inspector.get_columns("system_settings")}
            if "group_name" not in settings_columns:
                if dialect == "sqlite":
                    connection.execute(
                        text(
                            "ALTER TABLE system_settings "
                            "ADD COLUMN group_name VARCHAR(100) NOT NULL DEFAULT 'admin_panel'"
                        )
                    )
                else:
                    connection.execute(
                        text(
                            "ALTER TABLE system_settings "
                            "ADD COLUMN group_name VARCHAR(100) NOT NULL DEFAULT 'admin_panel' "
                            "AFTER id"
                        )
                    )
            if dialect == "mysql":
                connection.execute(
                    text(
                        "UPDATE system_settings "
                        "SET group_name = 'admin_panel' "
                        "WHERE group_name IS NULL OR group_name = ''"
                    )
                )

        if "password_reset_tokens" not in tables:
            if dialect == "sqlite":
                connection.execute(
                    text(
                        "CREATE TABLE password_reset_tokens ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "user_id INTEGER NOT NULL, "
                        "token_hash VARCHAR(64) NOT NULL UNIQUE, "
                        "expires_at TIMESTAMP NOT NULL, "
                        "used_at TIMESTAMP NULL, "
                        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                        "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
                        ")"
                    )
                )
            else:
                connection.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS password_reset_tokens ("
                        "id INT AUTO_INCREMENT PRIMARY KEY, "
                        "user_id INT NOT NULL, "
                        "token_hash VARCHAR(64) NOT NULL, "
                        "expires_at TIMESTAMP NOT NULL, "
                        "used_at TIMESTAMP NULL, "
                        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                        "UNIQUE KEY uq_password_reset_token_hash (token_hash), "
                        "KEY idx_password_reset_user (user_id), "
                        "CONSTRAINT fk_password_reset_user "
                        "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
                        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
                    )
                )


try:
    engine = _connect_engine(DATABASE_URL)
    ACTIVE_DATABASE_URL = DATABASE_URL
    logger.info("Legacy DB connected: %s", DATABASE_URL.split("@")[-1])
except Exception as exc:
    if _allow_sqlite_fallback() and _is_driver_or_connection_error(exc):
        logger.error("Legacy MySQL connection failed (%s). Using SQLite fallback.", exc)
        engine = _create_engine(FALLBACK_DATABASE_URL)
        ACTIVE_DATABASE_URL = FALLBACK_DATABASE_URL
    else:
        logger.critical("Legacy database connection failed: %s", exc)
        hint = (
            "Start MySQL/XAMPP, install PyMySQL (pip install PyMySQL), "
            "or set LEGACY_ALLOW_SQLITE_FALLBACK=1 for local dev."
        )
        raise RuntimeError(f"Legacy database unavailable ({exc}). {hint}") from exc

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def _create_all_safe(bind) -> None:
    """create_all that ignores SQLite 'already exists' on indexes/tables."""
    if bind.dialect.name != "sqlite":
        Base.metadata.create_all(bind=bind)
        return
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name in existing:
            continue
        try:
            table.create(bind=bind)
        except Exception as exc:
            if "already exists" in str(exc).lower():
                logger.debug("SQLite schema object exists for %s: %s", table.name, exc)
                continue
            raise


def init_legacy_schema() -> None:
    """Create ORM tables and apply incremental schema fixes."""
    import models  # noqa: F401

    _create_all_safe(engine)
    ensure_schema_extensions()
    _create_all_safe(engine)
    try:
        from services.role_sync import sync_all_user_roles

        db = SessionLocal()
        try:
            fixed = sync_all_user_roles(db)
            if fixed:
                logger.info("Synced roles for %s user(s) on startup", fixed)
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Role sync on startup skipped: %s", exc)

    try:
        from services.legacy_table_cleanup import drop_universities_legacy

        db = SessionLocal()
        try:
            drop_universities_legacy(db)
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Universities legacy drop skipped: %s", exc)

    try:
        from departments import backfill_professor_departments

        result = backfill_professor_departments(engine)
        total = result["users_updated"] + result["profiles_updated"]
        if total:
            logger.info(
                "Professor departments backfilled to %s (users=%s, profiles=%s)",
                result["department"],
                result["users_updated"],
                result["profiles_updated"],
            )
    except Exception as exc:
        logger.warning("Professor department backfill skipped: %s", exc)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_db_connection() -> dict:
    """Return structured DB health for /health and startup logs."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        safe_url = ACTIVE_DATABASE_URL.split("@")[-1] if "@" in ACTIVE_DATABASE_URL else ACTIVE_DATABASE_URL
        return {"ok": True, "url": safe_url, "driver": engine.dialect.name}
    except Exception as exc:
        logger.error("Database health check failed: %s", exc)
        return {"ok": False, "url": None, "error": str(exc)}
