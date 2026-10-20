"""Document API endpoints."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.config import settings
from app.dependencies import get_db
from app.repositories.document_repo import DocumentRepository
from app.schemas.document import DocumentDetailResponse, DocumentResponse
from app.services.text_processing import (
    SUPPORTED_EXTENSIONS,
    compute_content_hash,
    count_words,
    extract_text_from_file,
)
from auth import get_current_user, require_admin_or_professor
from models import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DocumentResponse:
    """Upload a document for plagiarism analysis."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    ext = Path(file.filename).suffix.lower().lstrip(".")
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    upload_path = settings.upload_dir / file.filename
    upload_path.parent.mkdir(parents=True, exist_ok=True)

    with upload_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        content = extract_text_from_file(upload_path, ext)
    except Exception as exc:
        upload_path.unlink(missing_ok=True)
        logger.error("Text extraction failed: %s", exc)
        raise HTTPException(status_code=422, detail=f"Text extraction failed: {exc}") from exc

    content_hash = compute_content_hash(content)
    repo = DocumentRepository(db)
    existing = repo.get_by_hash(content_hash)
    if existing:
        upload_path.unlink(missing_ok=True)
        return DocumentResponse.model_validate(existing)

    document = repo.create(
        title=title or Path(file.filename).stem,
        filename=file.filename,
        file_path=str(upload_path),
        file_type=ext,
        content=content,
        content_hash=content_hash,
        word_count=count_words(content),
    )

    logger.info("Document uploaded: id=%s by user_id=%s", document.id, current_user.id)
    return DocumentResponse.model_validate(document)


@router.get("/", response_model=list[DocumentResponse])
def list_documents(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_professor),
) -> list[DocumentResponse]:
    """List all uploaded documents. Restricted to professors and admins."""
    repo = DocumentRepository(db)
    return [DocumentResponse.model_validate(doc) for doc in repo.get_all(skip=skip, limit=limit)]


@router.get("/{document_id}", response_model=DocumentDetailResponse)
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DocumentDetailResponse:
    """Get document details including content."""
    repo = DocumentRepository(db)
    document = repo.get(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentDetailResponse.model_validate(document)
