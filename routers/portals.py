"""Portal API routes used by student.html and profesor.html."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from access_control import assert_thesis_access, ensure_professor_profile
from auth import get_current_user
from database import get_db
from departments import thesis_specialization
from models import Thesis, User, UserRole
from routers.analysis import AnalysisRequest, start_analysis
from routers.theses import list_professors as professors_list, list_theses, upload_thesis
from services.thesis_delete import delete_thesis_completely

router = APIRouter()


def _require_role(user: User, role: UserRole) -> None:
    if user.role != role:
        raise HTTPException(status_code=403, detail=f"Ky endpoint kërkon rolin '{role.value}'")


@router.get("/student/professors")
def student_professors(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_role(current_user, UserRole.student)
    return professors_list(db)


@router.get("/student/my-thesis")
def student_my_thesis(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_role(current_user, UserRole.student)
    return list_theses(skip=skip, limit=limit, db=db, current_user=current_user)


@router.post("/student/submit-thesis")
async def student_submit_thesis(
    file: UploadFile = File(...),
    title: str = Form(...),
    assigned_professor_id: int = Form(...),
    department: Optional[str] = Form(None),
    year: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_role(current_user, UserRole.student)
    return await upload_thesis(
        file=file,
        title=title,
        assigned_professor_id=assigned_professor_id,
        department=department,
        year=year,
        supervisor=None,
        db=db,
        current_user=current_user,
    )


@router.get("/professor/theses")
def professor_theses(
    skip: int = 0,
    limit: int = 50,
    status: Optional[str] = None,
    department: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_role(current_user, UserRole.professor)
    return list_theses(
        skip=skip,
        limit=limit,
        department=department,
        status=status,
        db=db,
        current_user=current_user,
    )


@router.delete("/professor/theses/{thesis_id}")
def professor_delete_thesis(
    thesis_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Profesori fshin temën e caktuar — fshirje e plotë nga DB + skedarët."""
    _require_role(current_user, UserRole.professor)

    thesis = (
        db.query(Thesis)
        .options(joinedload(Thesis.uploaded_files), joinedload(Thesis.results))
        .filter(Thesis.id == thesis_id)
        .first()
    )
    if not thesis:
        raise HTTPException(status_code=404, detail="Tema nuk u gjet")
    assert_thesis_access(db, thesis, current_user)

    if getattr(thesis, "is_archive", False):
        raise HTTPException(status_code=403, detail="Temat e arkivës nuk mund të fshihen nga profesori")

    profile = ensure_professor_profile(db, current_user, create_if_missing=True)
    if not profile or thesis.assigned_professor_id != profile.id:
        raise HTTPException(status_code=403, detail="Kjo temë nuk është e caktuar për ju")

    try:
        delete_thesis_completely(db, thesis)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Fshirja dështoi: {exc}") from exc

    return {"message": "Tema u fshi plotësisht nga sistemi", "id": thesis_id}


class ProfessorAnalyzeBody(BaseModel):
    compare_confidential: bool = True
    compare_internal: bool = True
    search_web: bool = True
    department_filter: Optional[str] = None


@router.post("/professor/analyze/{thesis_id}")
async def professor_analyze_thesis(
    thesis_id: int,
    body: ProfessorAnalyzeBody,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_role(current_user, UserRole.professor)
    thesis = db.query(Thesis).filter(Thesis.id == thesis_id).first()
    if not thesis:
        raise HTTPException(status_code=404, detail="Tema nuk u gjet")
    thesis_dept = thesis_specialization(thesis)
    request = AnalysisRequest(
        thesis_id=thesis_id,
        compare_confidential=body.compare_confidential,
        compare_internal=body.compare_internal,
        search_web=body.search_web,
        department_filter=body.department_filter or thesis_dept,
    )
    return await start_analysis(request, background_tasks, db, current_user)
