from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from auth import require_admin
from departments import build_department_stats, normalize_student_specialization, thesis_specialization
from services.admin_settings_service import get_admin_settings, get_plagiarism_threshold
from database import check_db_connection, get_db
from models import PlagiarismResult, Professor, Thesis, ThesisStatus, User, UserRole

router = APIRouter()


@router.get("/dashboard")
def get_dashboard(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    threshold = get_plagiarism_threshold(db)
    total_theses = db.query(func.count(Thesis.id)).scalar() or 0
    analyzed_theses = (
        db.query(func.count(PlagiarismResult.id))
        .filter(PlagiarismResult.status == "completed")
        .scalar()
        or 0
    )
    high_risk = (
        db.query(func.count(PlagiarismResult.id))
        .filter(
            PlagiarismResult.status == "completed",
            PlagiarismResult.overall_score > threshold,
        )
        .scalar()
        or 0
    )
    processing = (
        db.query(func.count(PlagiarismResult.id))
        .filter(PlagiarismResult.status == "pending")
        .scalar()
        or 0
    )
    processing += (
        db.query(func.count(Thesis.id))
        .filter(Thesis.status == ThesisStatus.analyzing)
        .scalar()
        or 0
    )

    total_users = db.query(func.count(User.id)).scalar() or 0
    professors = db.query(func.count(Professor.id)).filter(Professor.is_active == True).scalar() or 0
    students = db.query(func.count(User.id)).filter(User.role == UserRole.student).scalar() or 0

    recent_results = (
        db.query(PlagiarismResult, Thesis)
        .join(Thesis, Thesis.id == PlagiarismResult.thesis_id)
        .options(
            joinedload(Thesis.submitted_by_user),
            joinedload(Thesis.student),
            joinedload(Thesis.assigned_professor).joinedload(Professor.user),
        )
        .order_by(PlagiarismResult.analyzed_at.desc())
        .limit(5)
        .all()
    )

    def _thesis_department(thesis: Thesis) -> str | None:
        return thesis_specialization(thesis)

    department_rows = (
        db.query(Thesis.department, func.count(Thesis.id))
        .filter(Thesis.department.isnot(None))
        .group_by(Thesis.department)
        .order_by(func.count(Thesis.id).desc())
        .all()
    )
    max_department_count = max((count for _, count in department_rows), default=0) or 1

    return {
        "database": check_db_connection(),
        "stats": {
            "total_theses": total_theses,
            "analyzed_theses": analyzed_theses,
            "high_risk": high_risk,
            "processing": processing,
            "total_users": total_users,
            "professors": professors,
            "students": students,
            "plagiarism_threshold": threshold,
        },
        "settings": get_admin_settings(db),
        "recent_results": [
            {
                "id": result.id,
                "thesis_id": result.thesis_id,
                "title": thesis.title,
                "department": _thesis_department(thesis),
                "student_name": thesis.submitted_by_user.full_name if thesis.submitted_by_user else (
                    thesis.student.full_name if thesis.student else "—"
                ),
                "overall_score": float(result.overall_score or 0),
                "status": result.status,
                "analyzed_at": result.analyzed_at.isoformat() if result.analyzed_at else None,
            }
            for result, thesis in recent_results
        ],
        "departments": build_department_stats(department_rows),
    }