"""Fshin rreshta nga tabela legacy (nëse ekzistojnë) — pa prishur DB të vjetra."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Tabela të hequra nga schema e re; mund të ekzistojnë ende në instalime të vjetra.
_LEGACY_THESIS_TABLES = (
    "topic_requests",
    "thesis_messages",
    "thesis_feedback",
    "thesis_evaluations",
    "reports",
)


def _table_exists(db: Session, table: str) -> bool:
    bind = db.get_bind()
    if bind.dialect.name == "sqlite":
        row = db.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t LIMIT 1"),
            {"t": table},
        ).first()
        return row is not None
    row = db.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = :t LIMIT 1"
        ),
        {"t": table},
    ).first()
    return row is not None


def delete_legacy_thesis_rows(db: Session, thesis_id: int) -> None:
    for table in _LEGACY_THESIS_TABLES:
        try:
            if not _table_exists(db, table):
                continue
            db.execute(text(f"DELETE FROM {table} WHERE thesis_id = :id"), {"id": thesis_id})
        except Exception as exc:
            logger.debug("Legacy cleanup skip %s thesis_id=%s: %s", table, thesis_id, exc)


def delete_legacy_student_rows(db: Session, student_id: int) -> None:
    try:
        if _table_exists(db, "topic_requests"):
            db.execute(
                text("DELETE FROM topic_requests WHERE student_id = :id"),
                {"id": student_id},
            )
    except Exception as exc:
        logger.debug("Legacy cleanup skip topic_requests student_id=%s: %s", student_id, exc)


def delete_legacy_professor_topic_rows(db: Session, professor_id: int) -> None:
    try:
        if _table_exists(db, "topic_requests"):
            db.execute(
                text("DELETE FROM topic_requests WHERE professor_id = :id"),
                {"id": professor_id},
            )
    except Exception as exc:
        logger.debug("Legacy cleanup skip topic_requests professor_id=%s: %s", professor_id, exc)


def delete_legacy_user_message_rows(db: Session, user_id: int) -> None:
    try:
        if _table_exists(db, "thesis_messages"):
            db.execute(
                text("DELETE FROM thesis_messages WHERE sender_user_id = :id"),
                {"id": user_id},
            )
    except Exception as exc:
        logger.debug("Legacy cleanup skip thesis_messages user_id=%s: %s", user_id, exc)


def delete_user_auxiliary_rows(db: Session, user_id: int) -> None:
    """Remove rows that reference users.id and block DELETE (sessions, registrations, etc.)."""
    for table, column in (
        ("login_sessions", "user_id"),
        ("registration_requests", "user_id"),
        ("password_reset_tokens", "user_id"),
    ):
        try:
            if not _table_exists(db, table):
                continue
            db.execute(text(f"DELETE FROM {table} WHERE {column} = :id"), {"id": user_id})
        except Exception as exc:
            logger.debug("User cleanup skip %s user_id=%s: %s", table, user_id, exc)


def thesis_ids_linked_to_user(db: Session, user_id: int, student_id: int | None = None) -> set[int]:
    """Collect thesis IDs tied to a user (ORM + legacy uploaded_by column)."""
    ids: set[int] = set()
    from models import Thesis

    ids.update(
        row[0]
        for row in db.query(Thesis.id).filter(Thesis.submitted_by_user_id == user_id).all()
    )
    if student_id is not None:
        ids.update(
            row[0]
            for row in db.query(Thesis.id).filter(Thesis.student_id == student_id).all()
        )
    try:
        if _table_exists(db, "theses"):
            bind = db.get_bind()
            columns = {c["name"] for c in __import__("sqlalchemy").inspect(bind).get_columns("theses")}
            if "uploaded_by" in columns:
                rows = db.execute(
                    text("SELECT id FROM theses WHERE uploaded_by = :id"),
                    {"id": user_id},
                ).fetchall()
                ids.update(row[0] for row in rows)
    except Exception as exc:
        logger.debug("Thesis uploaded_by lookup skip user_id=%s: %s", user_id, exc)
    return ids


def drop_universities_legacy(db: Session) -> None:
    """Heq tabelën universities dhe kolonën students.university_id (instalime të vjetra)."""
    bind = db.get_bind()
    dialect = bind.dialect.name
    inspector = __import__("sqlalchemy").inspect(bind)
    tables = set(inspector.get_table_names())

    if "students" in tables:
        student_cols = {c["name"] for c in inspector.get_columns("students")}
        if "university_id" in student_cols:
            try:
                if dialect == "mysql":
                    fk_row = db.execute(
                        text(
                            "SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
                            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'students' "
                            "AND COLUMN_NAME = 'university_id' AND REFERENCED_TABLE_NAME IS NOT NULL "
                            "LIMIT 1"
                        )
                    ).first()
                    if fk_row and fk_row[0]:
                        db.execute(text(f"ALTER TABLE students DROP FOREIGN KEY `{fk_row[0]}`"))
                    idx_rows = db.execute(
                        text(
                            "SELECT INDEX_NAME FROM information_schema.STATISTICS "
                            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'students' "
                            "AND COLUMN_NAME = 'university_id' AND INDEX_NAME != 'PRIMARY'"
                        )
                    ).fetchall()
                    for (idx_name,) in idx_rows:
                        if idx_name:
                            db.execute(text(f"ALTER TABLE students DROP INDEX `{idx_name}`"))
                    db.execute(text("ALTER TABLE students DROP COLUMN university_id"))
                elif dialect == "sqlite":
                    db.execute(text("ALTER TABLE students DROP COLUMN university_id"))
                db.commit()
                logger.info("Removed students.university_id column")
            except Exception as exc:
                db.rollback()
                logger.debug("Could not drop students.university_id: %s", exc)

    if "universities" in tables:
        try:
            db.execute(text("DROP TABLE IF EXISTS universities"))
            db.commit()
            logger.info("Dropped legacy table universities")
        except Exception as exc:
            db.rollback()
            logger.debug("Could not drop universities table: %s", exc)
