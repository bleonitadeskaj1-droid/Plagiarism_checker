"""Complete thesis deletion (DB relations + optional file on disk)."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from database import PROJECT_ROOT
from models import (
    Notification,
    PlagiarismMatch,
    PlagiarismResult,
    Review,
    SystemSetting,
    Thesis,
    UploadedFile,
)
from services.legacy_table_cleanup import delete_legacy_thesis_rows
from services.thesis_text import resolve_thesis_file_path

ARCHIVE_EXCLUDE_GROUP = "temat_archive"
ARCHIVE_EXCLUDE_KEY = "excluded_paths"


def get_excluded_archive_paths(db: Session) -> set[str]:
    row = (
        db.query(SystemSetting)
        .filter(
            SystemSetting.group_name == ARCHIVE_EXCLUDE_GROUP,
            SystemSetting.setting_key == ARCHIVE_EXCLUDE_KEY,
        )
        .first()
    )
    if not row or not row.setting_value:
        return set()
    try:
        data = json.loads(row.setting_value)
        return set(data) if isinstance(data, list) else set()
    except (json.JSONDecodeError, TypeError):
        return set()


def exclude_archive_path(db: Session, relative_path: str) -> None:
    if not relative_path:
        return
    paths = get_excluded_archive_paths(db)
    paths.add(relative_path.replace("\\", "/"))
    payload = json.dumps(sorted(paths))
    row = (
        db.query(SystemSetting)
        .filter(
            SystemSetting.group_name == ARCHIVE_EXCLUDE_GROUP,
            SystemSetting.setting_key == ARCHIVE_EXCLUDE_KEY,
        )
        .first()
    )
    if row:
        row.setting_value = payload
    else:
        db.add(
            SystemSetting(
                group_name=ARCHIVE_EXCLUDE_GROUP,
                setting_key=ARCHIVE_EXCLUDE_KEY,
                setting_value=payload,
            )
        )


def _unlink_upload_file(thesis: Thesis) -> None:
    file_row = thesis.uploaded_files[0] if thesis.uploaded_files else None
    raw = (file_row.file_path if file_row else None) or thesis.file_path
    if raw and str(raw).startswith("s3://"):
        try:
            from services.storage import get_storage
            get_storage().delete(str(raw))
        except Exception:
            pass
        return
    path = resolve_thesis_file_path(thesis, file_row)
    if not path:
        return
    try:
        rel = str(path.resolve().relative_to(PROJECT_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return
    if rel.startswith("uploads/") and path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def delete_thesis_completely(db: Session, thesis: Thesis) -> None:
    """Remove a thesis and all dependent rows that block FK constraints."""
    thesis_id = thesis.id
    is_archive = bool(getattr(thesis, "is_archive", False))
    file_path = thesis.file_path

    _unlink_upload_file(thesis)

    if is_archive and file_path:
        exclude_archive_path(db, file_path)

    result_ids = [
        row[0]
        for row in db.query(PlagiarismResult.id)
        .filter(PlagiarismResult.thesis_id == thesis_id)
        .all()
    ]
    if result_ids:
        db.query(PlagiarismMatch).filter(PlagiarismMatch.result_id.in_(result_ids)).delete(
            synchronize_session=False
        )
    db.query(PlagiarismResult).filter(PlagiarismResult.thesis_id == thesis_id).delete(
        synchronize_session=False
    )
    db.query(Notification).filter(Notification.thesis_id == thesis_id).delete(
        synchronize_session=False
    )
    delete_legacy_thesis_rows(db, thesis_id)
    db.query(Review).filter(Review.thesis_id == thesis_id).delete(synchronize_session=False)
    db.query(UploadedFile).filter(UploadedFile.thesis_id == thesis_id).delete(
        synchronize_session=False
    )
    db.query(Thesis).filter(Thesis.id == thesis_id).delete(synchronize_session=False)
    db.commit()
