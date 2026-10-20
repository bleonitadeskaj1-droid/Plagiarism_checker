"""Admin thesis management — compatible with /api/admin/theses routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from auth import require_admin
from database import get_db
from departments import normalize_student_specialization, thesis_specialization
from models import PlagiarismResult, Professor, Thesis, ThesisStatus, User
from services.temat_archive import sync_temat_archive
from services.thesis_delete import delete_thesis_completely

router = APIRouter()


class AdminThesisUpdate(BaseModel):
    title: Optional[str] = None
    department: Optional[str] = None
    year: Optional[int] = None
    status: Optional[str] = None
    workflow_status: Optional[str] = None
    assigned_professor_id: Optional[int] = None


def _latest_result(db: Session, thesis_id: int) -> PlagiarismResult | None:
    return (
        db.query(PlagiarismResult)
        .filter(PlagiarismResult.thesis_id == thesis_id)
        .order_by(PlagiarismResult.id.desc())
        .first()
    )


def _serialize_thesis(db: Session, thesis: Thesis) -> dict:
    professor = (
        db.query(Professor).filter(Professor.id == thesis.assigned_professor_id).first()
        if thesis.assigned_professor_id
        else None
    )
    student_user = (
        db.query(User).filter(User.id == thesis.submitted_by_user_id).first()
        if thesis.submitted_by_user_id
        else None
    )
    uploaded = thesis.uploaded_files[0] if thesis.uploaded_files else None
    result = _latest_result(db, thesis.id)
    dept = thesis_specialization(thesis)

    return {
        "id": thesis.id,
        "title": thesis.title,
        "student_name": (
            thesis.student.full_name
            if thesis.student
            else (
                student_user.full_name
                if student_user
                else ("Arkivë Temat" if getattr(thesis, "is_archive", False) else None)
            )
        ),
        "assigned_professor": professor.full_name if professor else None,
        "assigned_professor_id": thesis.assigned_professor_id,
        "department": dept,
        "year": thesis.year,
        "status": thesis.status.value if hasattr(thesis.status, "value") else thesis.status,
        "workflow_status": (
            thesis.workflow_status.value
            if hasattr(thesis.workflow_status, "value")
            else thesis.workflow_status
        ),
        "created_at": thesis.created_at.isoformat() if thesis.created_at else None,
        "plagiarism_score": float(result.overall_score) if result and result.overall_score is not None else None,
        "result_id": result.id if result else None,
        "result_status": result.status if result else None,
        "file_name": uploaded.file_name if uploaded else None,
        "file_type": thesis.file_type.value if hasattr(thesis.file_type, "value") else thesis.file_type,
        "has_file": bool(thesis.file_path or uploaded),
        "is_archive": bool(getattr(thesis, "is_archive", False)),
    }


@router.post("/theses/sync-temat")
def admin_sync_temat_archive(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Import/update themes from the Temat/ folder."""
    result = sync_temat_archive(db)
    return {"message": "Sinkronizimi i folderit Temat u përfundua", **result}


@router.get("/theses")
def admin_list_theses(
    status: Optional[str] = Query(None),
    professor_id: Optional[int] = Query(None),
    dept: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    sync_temat: bool = Query(True, description="Sinkronizo folderin Temat para listimit"),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    temat_sync = None
    if sync_temat:
        temat_sync = sync_temat_archive(db)

    query = db.query(Thesis).options(
        joinedload(Thesis.student),
        joinedload(Thesis.uploaded_files),
    )

    if status:
        query = query.filter(Thesis.status == status)
    if professor_id:
        query = query.filter(Thesis.assigned_professor_id == professor_id)
    dept_filter = dept or department
    if dept_filter:
        query = query.filter(Thesis.department == dept_filter)
    if search:
        pattern = f"%{search}%"
        query = query.outerjoin(Professor, Professor.id == Thesis.assigned_professor_id).outerjoin(
            User, User.id == Thesis.submitted_by_user_id
        ).filter(
            or_(
                Thesis.title.ilike(pattern),
                User.full_name.ilike(pattern),
                Professor.full_name.ilike(pattern),
            )
        )

    total = query.with_entities(func.count(Thesis.id.distinct())).scalar() or 0
    theses = query.order_by(Thesis.created_at.desc()).limit(limit).all()

    payload = {
        "total": int(total),
        "items": [_serialize_thesis(db, thesis) for thesis in theses],
    }
    if temat_sync is not None:
        payload["temat_sync"] = temat_sync
    return payload


@router.get("/theses/{thesis_id}")
def admin_get_thesis(
    thesis_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    thesis = db.query(Thesis).filter(Thesis.id == thesis_id).first()
    if not thesis:
        raise HTTPException(status_code=404, detail="Tema nuk u gjet")
    return _serialize_thesis(db, thesis)


@router.put("/theses/{thesis_id}")
def admin_update_thesis(
    thesis_id: int,
    payload: AdminThesisUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    thesis = db.query(Thesis).filter(Thesis.id == thesis_id).first()
    if not thesis:
        raise HTTPException(status_code=404, detail="Tema nuk u gjet")

    if payload.title is not None:
        thesis.title = payload.title
    if payload.department is not None:
        thesis.department = payload.department
    if payload.year is not None:
        thesis.year = payload.year
    if payload.status is not None:
        if payload.status not in {s.value for s in ThesisStatus}:
            raise HTTPException(status_code=400, detail="Status i pavlefshëm")
        thesis.status = payload.status
    if payload.workflow_status is not None:
        thesis.workflow_status = payload.workflow_status
    if payload.assigned_professor_id is not None:
        thesis.assigned_professor_id = payload.assigned_professor_id or None

    db.commit()
    return {"message": "Tema u përditësua"}


@router.delete("/theses/{thesis_id}")
def admin_delete_thesis(
    thesis_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    thesis = (
        db.query(Thesis)
        .options(joinedload(Thesis.uploaded_files), joinedload(Thesis.results))
        .filter(Thesis.id == thesis_id)
        .first()
    )
    if not thesis:
        raise HTTPException(status_code=404, detail="Tema nuk u gjet")

    try:
        delete_thesis_completely(db, thesis)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Fshirja dështoi: {exc}") from exc
    return {"message": "Tema u fshi", "id": thesis_id}
