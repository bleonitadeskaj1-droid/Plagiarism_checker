"""Resolve thesis text from DB content or file on disk (including Temat/ archive)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from database import PROJECT_ROOT
from models import Thesis, UploadedFile

if TYPE_CHECKING:
    from document_processor import DocumentProcessor

logger = logging.getLogger(__name__)

_TEXT_CACHE: dict[int, str] = {}


def resolve_thesis_file_path(thesis: Thesis, file_row: Optional[UploadedFile] = None) -> Optional[Path]:
    uploaded = file_row
    if uploaded is None and thesis.uploaded_files:
        uploaded = thesis.uploaded_files[0]

    for raw in (uploaded.file_path if uploaded else None, thesis.file_path):
        if not raw:
            continue
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if path.exists():
            return path
    return None


def get_thesis_text(
    thesis: Thesis,
    processor: DocumentProcessor,
    *,
    use_cache: bool = True,
) -> str:
    """Return extracted thesis text; prefers stored content, falls back to file extraction."""
    if use_cache and thesis.id in _TEXT_CACHE:
        return _TEXT_CACHE[thesis.id]

    content = (thesis.content or "").strip()
    if content:
        if use_cache and thesis.id:
            _TEXT_CACHE[thesis.id] = content
        return content

    path = resolve_thesis_file_path(thesis)
    if not path:
        return ""

    try:
        text, _meta = processor.extract_text(path)
        text = (text or "").strip()
        if text and use_cache and thesis.id:
            _TEXT_CACHE[thesis.id] = text
        elif not text:
            logger.warning("No text extracted for thesis %s from %s", thesis.id, path)
        return text
    except Exception as exc:
        logger.warning("Failed to extract thesis %s from %s: %s", thesis.id, path, exc)
        return ""


def clear_thesis_text_cache() -> None:
    _TEXT_CACHE.clear()
