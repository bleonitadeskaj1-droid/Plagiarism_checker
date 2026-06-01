from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.sql import func
from typing import Literal, Optional

from auth import get_current_user
from access_control import can_view_final_review, ensure_professor_profile, get_thesis_with_access
from database import get_db
from models import Review, SubmissionWorkflowStatus, ThesisEvaluation, ThesisFeedback, ThesisMessage, ThesisStatus, User, UserRole

router = APIRouter()


class MessageCreate(BaseModel):
    message_text: str


class FeedbackCreate(BaseModel):
    feedback_text: str


class EvaluationUpsert(BaseModel):
    grade: str
    evaluation_text: str


class ReviewUpsert(BaseModel):
    status: Literal["approved", "rejected", "needs_revision"]
    comments: str
    plagiarism_percentage: Optional[float] = None


@router.get("/{thesis_id}/messages")
def list_messages(
    thesis_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_thesis_with_access(db, thesis_id, current_user)
    rows = (
        db.query(ThesisMessage)
        .filter(ThesisMessage.thesis_id == thesis_id)
        .order_by(ThesisMessage.created_at.asc())
        .all()
    )
    return [
        {
            "id": r.id,
            "sender_user_id": r.sender_user_id,
            "message_text": r.message_text,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/{thesis_id}/messages")
def create_message(
    thesis_id: int,
    payload: MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_thesis_with_access(db, thesis_id, current_user)
    text = (payload.message_text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Mesazhi nuk mund të jetë bosh")

    row = ThesisMessage(
        thesis_id=thesis_id,
        sender_user_id=current_user.id,
        message_text=text,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "id": row.id,
        "sender_user_id": row.sender_user_id,
        "message_text": row.message_text,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/{thesis_id}/review")
def get_review(
    thesis_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thesis = get_thesis_with_access(db, thesis_id, current_user)
    if not can_view_final_review(thesis, current_user, db):
        raise HTTPException(status_code=403, detail="Tema është ende në proces")

    row = db.query(Review).filter(Review.thesis_id == thesis_id).first()
    if not row:
        return None
    return {
        "id": row.id,
        "thesis_id": row.thesis_id,
        "professor_id": row.professor_id,
        "status": row.status.value if getattr(row.status, "value", None) else row.status,
        "comments": row.comments,
        "plagiarism_percentage": float(row.plagiarism_percentage or 0),
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
    }


@router.post("/{thesis_id}/review")
def upsert_review(
    thesis_id: int,
    payload: ReviewUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thesis = get_thesis_with_access(db, thesis_id, current_user)

    if current_user.role not in (UserRole.professor, UserRole.admin):
        raise HTTPException(status_code=403, detail="Vetëm profesori ose admin mund të ruajë review")

    if current_user.role == UserRole.professor:
        profile = ensure_professor_profile(db, current_user, create_if_missing=True)
        if not profile or thesis.assigned_professor_id != profile.id:
            raise HTTPException(status_code=403, detail="Kjo temë nuk është e caktuar për ju")
        professor_id = profile.id
    else:
        professor_id = thesis.assigned_professor_id

    comments = (payload.comments or "").strip()
    if not comments:
        raise HTTPException(status_code=400, detail="Komentet janë të detyrueshme")

    review = db.query(Review).filter(Review.thesis_id == thesis_id).first()
    if not review:
        review = Review(thesis_id=thesis_id, professor_id=professor_id, status=payload.status, comments=comments)
        db.add(review)
    else:
        review.professor_id = professor_id
        review.status = payload.status
        review.comments = comments
        review.reviewed_at = func.now()

    latest_result = None
    if payload.plagiarism_percentage is None:
        from models import PlagiarismResult

        latest_result = (
            db.query(PlagiarismResult)
            .filter(PlagiarismResult.thesis_id == thesis_id)
            .order_by(PlagiarismResult.analyzed_at.desc())
            .first()
        )
        review.plagiarism_percentage = float(latest_result.overall_score or 0) if latest_result else 0
    else:
        review.plagiarism_percentage = float(payload.plagiarism_percentage)

    thesis.workflow_status = SubmissionWorkflowStatus(payload.status)
    thesis.status = ThesisStatus.completed if thesis.workflow_status == SubmissionWorkflowStatus.approved else ThesisStatus.flagged
    db.commit()
    db.refresh(review)

    return {
        "id": review.id,
        "thesis_id": review.thesis_id,
        "professor_id": review.professor_id,
        "status": review.status.value if getattr(review.status, "value", None) else review.status,
        "comments": review.comments,
        "plagiarism_percentage": float(review.plagiarism_percentage or 0),
        "reviewed_at": review.reviewed_at.isoformat() if review.reviewed_at else None,
    }


@router.get("/{thesis_id}/feedback")
def list_feedback(
    thesis_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thesis = get_thesis_with_access(db, thesis_id, current_user)
    if current_user.role == UserRole.student and not can_view_final_review(thesis, current_user, db):
        raise HTTPException(status_code=403, detail="Tema eshte ende ne proces")
    rows = (
        db.query(ThesisFeedback)
        .filter(ThesisFeedback.thesis_id == thesis_id)
        .order_by(ThesisFeedback.created_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "professor_id": r.professor_id,
            "feedback_text": r.feedback_text,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/{thesis_id}/feedback")
def create_feedback(
    thesis_id: int,
    payload: FeedbackCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thesis = get_thesis_with_access(db, thesis_id, current_user)
    if current_user.role not in (UserRole.professor, UserRole.admin):
        raise HTTPException(status_code=403, detail="Vetëm profesori ose admin mund të japë feedback")

    if current_user.role == UserRole.professor:
        profile = ensure_professor_profile(db, current_user, create_if_missing=True)
        if not profile or thesis.assigned_professor_id != profile.id:
            raise HTTPException(status_code=403, detail="Kjo temë nuk është e caktuar për ju")
        professor_id = profile.id
    else:
        professor_id = thesis.assigned_professor_id

    text = (payload.feedback_text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Feedback-u nuk mund të jetë bosh")

    row = ThesisFeedback(
        thesis_id=thesis_id,
        professor_id=professor_id,
        feedback_text=text,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "id": row.id,
        "professor_id": row.professor_id,
        "feedback_text": row.feedback_text,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/{thesis_id}/evaluation")
def get_evaluation(
    thesis_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thesis = get_thesis_with_access(db, thesis_id, current_user)
    if current_user.role == UserRole.student and not can_view_final_review(thesis, current_user, db):
        raise HTTPException(status_code=403, detail="Tema eshte ende ne proces")
    row = db.query(ThesisEvaluation).filter(ThesisEvaluation.thesis_id == thesis_id).first()
    if not row:
        return None
    return {
        "id": row.id,
        "professor_id": row.professor_id,
        "grade": row.grade,
        "evaluation_text": row.evaluation_text,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.put("/{thesis_id}/evaluation")
def upsert_evaluation(
    thesis_id: int,
    payload: EvaluationUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thesis = get_thesis_with_access(db, thesis_id, current_user)

    if current_user.role not in (UserRole.professor, UserRole.admin):
        raise HTTPException(status_code=403, detail="Vetëm profesori ose admin mund të vendosë vlerësim")

    if current_user.role == UserRole.professor:
        profile = ensure_professor_profile(db, current_user, create_if_missing=True)
        if not profile or thesis.assigned_professor_id != profile.id:
            raise HTTPException(status_code=403, detail="Kjo temë nuk është e caktuar për ju")
        professor_id = profile.id
    else:
        professor_id = thesis.assigned_professor_id

    grade = (payload.grade or "").strip()
    text = (payload.evaluation_text or "").strip()
    if not grade or not text:
        raise HTTPException(status_code=400, detail="Nota dhe vlerësimi janë të detyrueshme")

    row = db.query(ThesisEvaluation).filter(ThesisEvaluation.thesis_id == thesis_id).first()
    if not row:
        row = ThesisEvaluation(
            thesis_id=thesis_id,
            professor_id=professor_id,
            grade=grade,
            evaluation_text=text,
        )
        db.add(row)
    else:
        row.professor_id = professor_id
        row.grade = grade
        row.evaluation_text = text

    db.commit()
    db.refresh(row)

    return {
        "id": row.id,
        "professor_id": row.professor_id,
        "grade": row.grade,
        "evaluation_text": row.evaluation_text,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
