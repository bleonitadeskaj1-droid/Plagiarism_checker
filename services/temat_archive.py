"""Import thesis files from the Temat/ folder into the database (archive entries)."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from database import PROJECT_ROOT
from models import SubmissionWorkflowStatus, Thesis, ThesisStatus, UploadedFile
from services.thesis_delete import get_excluded_archive_paths

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = frozenset({"pdf", "docx", "pptx", "txt", "doc"})
ARCHIVE_DEPARTMENT = "Arkivë"


def temat_folder() -> Path | None:
    for name in ("Temat", "temat", "TEMAT"):
        path = PROJECT_ROOT / name
        if path.is_dir():
            return path
    return None


def title_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    title = re.sub(r"[_\-]+", " ", stem)
    title = re.sub(r"\s+", " ", title).strip()
    return title or filename


def relative_file_path(absolute: Path) -> str:
    try:
        return absolute.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return f"Temat/{absolute.name}"


def thesis_enum_file_type(ext: str) -> str:
    """Map extension to thesis.file_type enum (pdf, docx, txt)."""
    ext = ext.lower()
    if ext in ("pdf", "docx", "txt"):
        return ext
    return "pdf"


def _find_existing_archive(db: Session, relative: str, filename: str) -> Thesis | None:
    like_name = f"%/{filename}"
    return (
        db.query(Thesis)
        .filter(
            Thesis.is_archive.is_(True),
            (Thesis.file_path == relative) | (Thesis.file_path.like(like_name)),
        )
        .first()
    )


def _try_extract_content(path: Path, ext: str) -> str | None:
    """Archive import keeps content empty (files stay on disk; avoids huge DB rows)."""
    return None


def sync_temat_archive(db: Session) -> dict:
    """
    Scan Temat/ and upsert archive thesis rows (is_archive=True).
    Returns counts: imported, updated, skipped, total_files, folder.
    """
    folder = temat_folder()
    result = {
        "imported": 0,
        "updated": 0,
        "skipped": 0,
        "total_files": 0,
        "folder": str(folder) if folder else None,
    }
    if not folder:
        logger.warning("Temat folder not found under %s", PROJECT_ROOT)
        return result

    excluded = get_excluded_archive_paths(db)

    for entry in sorted(folder.iterdir()):
        if not entry.is_file():
            continue
        ext = entry.suffix.lstrip(".").lower()
        if ext not in ALLOWED_EXTENSIONS:
            continue

        result["total_files"] += 1
        relative = relative_file_path(entry)
        if relative in excluded:
            result["skipped"] += 1
            continue
        filename = entry.name
        title = title_from_filename(filename)
        file_size = entry.stat().st_size
        year = datetime.fromtimestamp(entry.stat().st_mtime).year
        enum_type = thesis_enum_file_type(ext)

        existing = _find_existing_archive(db, relative, filename)
        if existing:
            changed = False
            if existing.file_path != relative:
                existing.file_path = relative
                existing.file_type = enum_type
                changed = True
            if existing.title != title:
                existing.title = title
                changed = True

            upload = existing.uploaded_files[0] if existing.uploaded_files else None
            if upload:
                upload.file_name = filename
                upload.file_path = relative
                upload.file_type = ext
                upload.file_size = file_size
            else:
                db.add(
                    UploadedFile(
                        thesis_id=existing.id,
                        file_name=filename,
                        file_path=relative,
                        file_type=ext,
                        file_size=file_size,
                    )
                )
                changed = True

            if changed:
                result["updated"] += 1
                db.commit()
            else:
                result["skipped"] += 1
            continue

        content = _try_extract_content(entry, ext)
        thesis = Thesis(
            title=title,
            file_path=relative,
            file_type=enum_type,
            year=year,
            department=ARCHIVE_DEPARTMENT,
            workflow_status=SubmissionWorkflowStatus.approved,
            status=ThesisStatus.completed,
            is_archive=True,
            content=content,
        )
        db.add(thesis)
        db.flush()

        db.add(
            UploadedFile(
                thesis_id=thesis.id,
                file_name=filename,
                file_path=relative,
                file_type=ext,
                file_size=file_size,
            )
        )
        result["imported"] += 1
        db.commit()

    return result
