# ============================================
# routers/theses.py - Endpoints për Temat
# ============================================

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel
from typing import Optional
import os
import shutil
import PyPDF2
import docx2txt

from database import get_db
from auth import get_current_user
from access_control import (
    can_view_final_review,
    assert_thesis_access,
    create_assignment_notification,
    ensure_professor_profile,
    ensure_student_profile,
)
from models import (
    PlagiarismResult,
    Professor,
    Review,
    SubmissionWorkflowStatus,
    Thesis,
    ThesisStatus,
    UploadedFile,
    User,
    UserRole,
)

router = APIRouter()

UPLOAD_DIR = "uploads/theses"
os.makedirs(UPLOAD_DIR, exist_ok=True)


class ThesisCreate(BaseModel):
    title: str
    student_id: Optional[int] = None
    assigned_professor_id: int
    abstract: Optional[str] = None
    year: Optional[int] = None
    department: Optional[str] = None
    supervisor: Optional[str] = None


@router.get("/")
def list_theses(
    skip: int = 0,
    limit: int = 20,
    department: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Merr listën e temave."""
    query = db.query(Thesis)

    if current_user.role == UserRole.professor:
        profile = ensure_professor_profile(db, current_user, create_if_missing=True)
        if not profile:
            return {"total": 0, "items": []}
        query = query.filter(Thesis.assigned_professor_id == profile.id)
    elif current_user.role == UserRole.student:
        query = query.filter(Thesis.submitted_by_user_id == current_user.id)

    if department:
        query = query.filter(Thesis.department == department)
    if status:
        query = query.filter(Thesis.status == status)

    total = query.count()
    theses = (
        query.order_by(Thesis.created_at.desc(), Thesis.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    items = []
    for t in theses:
        can_view = (
            current_user.role != UserRole.student
            or can_view_final_review(t, current_user, db)
        )
        overall_score = None
        result_id = None
        if can_view:
            latest = (
                db.query(PlagiarismResult)
                .filter(
                    PlagiarismResult.thesis_id == t.id,
                    PlagiarismResult.status == "completed",
                )
                .order_by(PlagiarismResult.analyzed_at.desc())
                .first()
            )
            if latest:
                result_id = latest.id
                overall_score = float(latest.overall_score or 0)
        items.append(
            {
                "id": t.id,
                "title": t.title,
                "student_name": t.student.full_name if t.student else (t.submitted_by_user.full_name if t.submitted_by_user else None),
                "file_name": (t.uploaded_files[0].file_name if t.uploaded_files else None) or (os.path.basename(t.file_path) if t.file_path else None),
                "file_path": t.file_path,
                "has_file": bool(t.file_path or (t.uploaded_files and t.uploaded_files[0].file_path)),
                "file_type": t.file_type,
                "submission_date": t.created_at.isoformat() if t.created_at else None,
                "department": t.department,
                "year": t.year,
                "supervisor": t.supervisor,
                "assigned_professor_id": t.assigned_professor_id,
                "assigned_professor": t.assigned_professor.full_name if t.assigned_professor else None,
                "status": t.workflow_status.value if getattr(t.workflow_status, "value", None) else t.workflow_status,
                "workflow_status": t.workflow_status.value if getattr(t.workflow_status, "value", None) else t.workflow_status,
                "analysis_status": None if not can_view else (t.status.value if getattr(t.status, "value", None) else t.status),
                "review": _serialize_review(t, current_user, db),
                "overall_score": overall_score,
                "result_id": result_id,
                "can_view_result": can_view and result_id is not None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
        )

    return {"total": total, "items": items}


@router.post("/upload")
async def upload_thesis(
    file: UploadFile = File(...),
    title: str = Form(...),
    student_id: Optional[int] = Form(None),
    assigned_professor_id: Optional[int] = Form(None),
    department: Optional[str] = Form(None),
    year: Optional[int] = Form(None),
    supervisor: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload një temë diplome (PDF ose DOCX)."""

    # When called from another route, Form(...) defaults stay as Form objects.
    if not isinstance(supervisor, (str, type(None))):
        supervisor = None
    if not isinstance(department, (str, type(None))):
        department = None

    # Kontrollo llojin e skedarit
    allowed_types = ["application/pdf",
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                     "text/plain"]

    file_ext = file.filename.split(".")[-1].lower()
    if file_ext not in ["pdf", "docx", "txt"]:
        raise HTTPException(status_code=400, detail="Vetëm PDF, DOCX dhe TXT pranohen")

    # Ruaj skedarin (local ose S3 sipas STORAGE_BACKEND)
    from services.storage import get_storage

    storage_key = f"theses/{title[:50].replace(' ', '_')}_{file.filename}"
    content_types = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "txt": "text/plain",
    }
    try:
        file.file.seek(0)
    except Exception:
        pass
    file_path = get_storage().save(
        storage_key,
        file.file,
        content_type=content_types.get(file_ext),
    )
    local_path = str(get_storage().open_local_path(file_path))

    # Ekstrakto tekstin
    content = (extract_text(local_path, file_ext) or "").strip()
    if not content:
        raise HTTPException(
            status_code=400,
            detail="Skedari nuk përmban tekst të lexueshëm për analizë"
        )

    if current_user.role == UserRole.student:
        student_profile = ensure_student_profile(db, current_user, create_if_missing=True)
        if student_profile:
            student_id = student_profile.id

    if not assigned_professor_id and current_user.role == UserRole.student:
        raise HTTPException(status_code=400, detail="Duhet të zgjidhni një profesor")

    if not assigned_professor_id and current_user.role == UserRole.professor:
        profile = ensure_professor_profile(db, current_user, create_if_missing=True)
        if not profile:
            raise HTTPException(status_code=400, detail="Profili i profesorit mungon")
        assigned_professor_id = profile.id

    if not assigned_professor_id and current_user.role == UserRole.admin:
        fallback_professor = (
            db.query(Professor)
            .filter(Professor.is_active == True)
            .order_by(Professor.id.asc())
            .first()
        )
        if not fallback_professor:
            raise HTTPException(status_code=400, detail="Nuk ka profesor aktiv për caktim")
        assigned_professor_id = fallback_professor.id

    professor = db.query(Professor).filter(Professor.id == assigned_professor_id).first()
    if not professor or not professor.is_active:
        raise HTTPException(status_code=400, detail="Profesori i zgjedhur nuk ekziston")

    submitter_name = current_user.full_name or current_user.username

    # Krijo rekord në databazë
    thesis = Thesis(
        title=title,
        student_id=student_id,
        submitted_by_user_id=current_user.id,
        assigned_professor_id=assigned_professor_id,
        content=content,
        file_path=file_path,
        file_type=file_ext,
        department=department,
        year=year,
        supervisor=supervisor,
        workflow_status=SubmissionWorkflowStatus.in_process,
        status=ThesisStatus.pending
    )
    db.add(thesis)
    db.flush()

    db.add(UploadedFile(
        thesis_id=thesis.id,
        file_name=file.filename,
        file_path=file_path,
        file_type=file_ext,
        file_size=os.path.getsize(file_path) if os.path.exists(file_path) else None,
    ))

    create_assignment_notification(db, thesis, assigned_professor_id, submitter_name)
    db.commit()
    db.refresh(thesis)

    return {
        "message": "Tema u ngarkua me sukses",
        "thesis_id": thesis.id,
        "assigned_professor_id": thesis.assigned_professor_id,
        "title": thesis.title,
        "content_length": len(content) if content else 0,
        "status": thesis.workflow_status.value if getattr(thesis.workflow_status, "value", None) else thesis.workflow_status,
        "workflow_status": thesis.workflow_status.value if getattr(thesis.workflow_status, "value", None) else thesis.workflow_status,
    }


@router.post("/manual")
def create_thesis_manual(
    data: ThesisCreate,
    content: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Krijo temë me tekst direkt (pa skedar)."""
    professor = db.query(Professor).filter(Professor.id == data.assigned_professor_id).first()
    if not professor or not professor.is_active:
        raise HTTPException(status_code=400, detail="Profesori i zgjedhur nuk ekziston")

    submitter_name = current_user.full_name or current_user.username

    if current_user.role == UserRole.student:
        student_profile = ensure_student_profile(db, current_user, create_if_missing=True)
        if student_profile:
            data.student_id = student_profile.id

    thesis = Thesis(
        title=data.title,
        student_id=data.student_id,
        submitted_by_user_id=current_user.id,
        assigned_professor_id=data.assigned_professor_id,
        abstract=data.abstract,
        content=content,
        year=data.year,
        department=data.department,
        supervisor=data.supervisor,
        workflow_status=SubmissionWorkflowStatus.in_process,
        status=ThesisStatus.pending
    )
    db.add(thesis)
    db.flush()
    create_assignment_notification(db, thesis, data.assigned_professor_id, submitter_name)
    db.commit()
    db.refresh(thesis)

    return {
        "thesis_id": thesis.id,
        "assigned_professor_id": thesis.assigned_professor_id,
        "message": "Tema u krijua"
    }


@router.get("/professors-list")
def list_professors(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    professors = (
        db.query(Professor)
        .join(User, User.id == Professor.user_id)
        .filter(User.is_active == True, Professor.is_active == True)
        .order_by(Professor.full_name.asc())
        .all()
    )

    return [
        {
            "id": p.id,
            "user_id": p.user_id,
            "full_name": p.full_name,
            "department": p.department,
            "email": p.user.email if p.user else None,
        }
        for p in professors
    ]


@router.get("/{thesis_id}/file")
def download_thesis_file(
    thesis_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thesis = db.query(Thesis).filter(Thesis.id == thesis_id).first()
    if not thesis:
        raise HTTPException(status_code=404, detail="Tema nuk u gjet")
    assert_thesis_access(db, thesis, current_user)

    file_row = thesis.uploaded_files[0] if thesis.uploaded_files else None
    file_path = file_row.file_path if file_row else thesis.file_path
    file_name = file_row.file_name if file_row else (os.path.basename(thesis.file_path) if thesis.file_path else None)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Skedari nuk u gjet")

    return FileResponse(file_path, filename=file_name or os.path.basename(file_path))


@router.get("/{thesis_id}")
def get_thesis(
    thesis_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Merr detajet e një teme."""
    thesis = db.query(Thesis).filter(Thesis.id == thesis_id).first()
    if not thesis:
        raise HTTPException(status_code=404, detail="Tema nuk u gjet")
    assert_thesis_access(db, thesis, current_user)

    return {
        "id": thesis.id,
        "title": thesis.title,
        "abstract": thesis.abstract,
        "department": thesis.department,
        "year": thesis.year,
        "supervisor": thesis.supervisor,
        "student_name": thesis.student.full_name if thesis.student else (thesis.submitted_by_user.full_name if thesis.submitted_by_user else None),
        "file_name": (thesis.uploaded_files[0].file_name if thesis.uploaded_files else None) or (os.path.basename(thesis.file_path) if thesis.file_path else None),
        "file_path": thesis.file_path,
        "assigned_professor_id": thesis.assigned_professor_id,
        "assigned_professor": thesis.assigned_professor.full_name if thesis.assigned_professor else None,
        "status": thesis.workflow_status.value if getattr(thesis.workflow_status, "value", None) else thesis.workflow_status,
        "workflow_status": thesis.workflow_status.value if getattr(thesis.workflow_status, "value", None) else thesis.workflow_status,
        "analysis_status": None if current_user.role == UserRole.student and not can_view_final_review(thesis, current_user, db) else (thesis.status.value if getattr(thesis.status, "value", None) else thesis.status),
        "file_type": thesis.file_type,
        "content_preview": thesis.content[:500] if thesis.content else None,
        "created_at": thesis.created_at.isoformat() if thesis.created_at else None,
        "review": _serialize_review(thesis, current_user, db),
    }


@router.delete("/{thesis_id}")
def delete_thesis(
    thesis_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fshi temën plotësisht nga databaza (rezultate, matches, reviews, skedarë)."""
    if current_user.role not in (UserRole.professor, UserRole.admin):
        raise HTTPException(status_code=403, detail="Vetëm profesori ose admini mund të fshijë temën")

    thesis = (
        db.query(Thesis)
        .options(joinedload(Thesis.uploaded_files), joinedload(Thesis.results))
        .filter(Thesis.id == thesis_id)
        .first()
    )
    if not thesis:
        raise HTTPException(status_code=404, detail="Tema nuk u gjet")
    assert_thesis_access(db, thesis, current_user)

    if getattr(thesis, "is_archive", False) and current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Temat e arkivës nuk mund të fshihen nga profesori")

    try:
        from services.thesis_delete import delete_thesis_completely

        delete_thesis_completely(db, thesis)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Fshirja dështoi: {exc}") from exc

    return {"message": "Tema u fshi plotësisht nga sistemi", "id": thesis_id}


def extract_text(file_path: str, file_ext: str) -> str:
    """Ekstrakton tekst nga PDF, DOCX ose TXT."""
    try:
        if file_ext == "pdf":
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                text = ""
                for page in reader.pages:
                    page_text = page.extract_text() or ""
                    if page_text:
                        text += page_text + "\n"
            return text.strip()

        elif file_ext == "docx":
            return (docx2txt.process(file_path) or "").strip()

        elif file_ext == "txt":
            for encoding in ("utf-8-sig", "utf-8", "latin-1"):
                try:
                    with open(file_path, "r", encoding=encoding) as f:
                        return f.read().strip()
                except UnicodeDecodeError:
                    continue
            with open(file_path, "r", errors="ignore") as f:
                return f.read().strip()

    except Exception as e:
        return f"Gabim gjatë leximit të skedarit: {str(e)}"

    return ""


def _serialize_review(thesis: Thesis, current_user: User, db: Session):
    review = thesis.reviews[0] if thesis.reviews else None
    if not review:
        return None

    if not can_view_final_review(thesis, current_user, db):
        return None

    return {
        "id": review.id,
        "thesis_id": review.thesis_id,
        "professor_id": review.professor_id,
        "status": review.status.value if getattr(review.status, "value", None) else review.status,
        "comments": review.comments,
        "plagiarism_percentage": float(review.plagiarism_percentage or 0),
        "reviewed_at": review.reviewed_at.isoformat() if review.reviewed_at else None,
    }
