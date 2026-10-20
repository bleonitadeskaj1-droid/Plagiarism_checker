"""Plagiarism check API endpoints."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.repositories.document_repo import DocumentRepository
from app.repositories.result_repo import CheckResultRepository
from app.schemas.check_result import CheckReportResponse, CheckResultResponse, CheckRunRequest
from app.services.plagiarism_service import get_plagiarism_service
from auth import get_current_user
from models import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/checks", tags=["checks"])


@router.post("/run", response_model=CheckResultResponse, status_code=status.HTTP_201_CREATED)
def run_check(
    payload: CheckRunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CheckResultResponse:
    """Run a plagiarism check on a document."""
    doc_repo = DocumentRepository(db)
    document = doc_repo.get(payload.document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    result_repo = CheckResultRepository(db)
    check = result_repo.create(document_id=document.id, status="running")

    try:
        service = get_plagiarism_service()
        outcome = service.run_check(db, document)
        check = result_repo.update(
            check,
            similarity_score=outcome["similarity_score"],
            risk_level=outcome["risk_level"],
            matches=outcome["matches"],
            report=outcome["report"],
            summary=outcome["summary"],
            status="completed",
            completed_at=datetime.now(timezone.utc),
        )
    except Exception as exc:
        logger.error("Check failed: %s", exc)
        result_repo.update(check, status="failed", summary=str(exc))
        raise HTTPException(status_code=500, detail=f"Check failed: {exc}") from exc

    return CheckResultResponse.model_validate(check)


@router.get("/{check_id}", response_model=CheckResultResponse)
def get_check(
    check_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CheckResultResponse:
    """Get check result by ID."""
    repo = CheckResultRepository(db)
    check = repo.get(check_id)
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")
    return CheckResultResponse.model_validate(check)


@router.get("/{check_id}/report", response_model=CheckReportResponse)
def get_check_report(
    check_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CheckReportResponse:
    """Get detailed report for a check."""
    repo = CheckResultRepository(db)
    check = repo.get(check_id)
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")
    if not check.report:
        raise HTTPException(status_code=404, detail="Report not available")

    return CheckReportResponse(
        check_id=check.id,
        document_id=check.document_id,
        similarity_score=float(check.similarity_score),
        risk_level=check.risk_level,
        summary=check.summary,
        matches=check.matches or [],
        report=check.report,
    )
