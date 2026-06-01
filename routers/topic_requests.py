import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from access_control import ensure_professor_profile, ensure_student_profile
from auth import get_current_user
from database import get_db
from models import Professor, Thesis, TopicRequest, User, UserRole

router = APIRouter()
logger = logging.getLogger(__name__)


class TopicRequestCreate(BaseModel):
    professor_id: int
    thesis_id: int
    note: str | None = None


def _serialize_topic(thesis: Thesis) -> dict:
    return {
        "id": thesis.id,
        "title": thesis.title,
        "department": thesis.department,
        "year": thesis.year,
        "supervisor": thesis.supervisor,
        "status": thesis.status,
        "created_at": thesis.created_at.isoformat() if thesis.created_at else None,
        "assigned_professor_id": thesis.assigned_professor_id,
        "assigned_professor": thesis.assigned_professor.full_name if thesis.assigned_professor else None,
    }


@router.get("/professors/{professor_id}/topics")
def list_professor_topics(
    professor_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    professor = (
        db.query(Professor)
        .join(User, User.id == Professor.user_id)
        .filter(Professor.id == professor_id, Professor.is_active == True, User.is_active == True)
        .first()
    )
    if not professor:
        raise HTTPException(status_code=404, detail="Profesori nuk u gjet")

    if current_user.role == UserRole.professor:
        profile = ensure_professor_profile(db, current_user, create_if_missing=True)
        if not profile or profile.id != professor_id:
            raise HTTPException(status_code=403, detail="Nuk keni akses në temat e këtij profesori")

    topics = (
        db.query(Thesis)
        .filter(Thesis.assigned_professor_id == professor_id)
        .order_by(Thesis.created_at.desc())
        .all()
    )
    logger.info("Loaded %s topics for professor_id=%s by user_id=%s", len(topics), professor_id, current_user.id)
    return [_serialize_topic(topic) for topic in topics]


@router.post("")
def create_topic_request(
    payload: TopicRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != UserRole.student:
        raise HTTPException(status_code=403, detail="Vetëm studenti mund të dërgojë kërkesë për temë")

    student_profile = ensure_student_profile(db, current_user, create_if_missing=True)
    if not student_profile:
        raise HTTPException(status_code=400, detail="Profili i studentit nuk u gjet")

    professor = (
        db.query(Professor)
        .join(User, User.id == Professor.user_id)
        .filter(Professor.id == payload.professor_id, Professor.is_active == True, User.is_active == True)
        .first()
    )
    if not professor:
        raise HTTPException(status_code=404, detail="Profesori nuk u gjet")

    thesis = db.query(Thesis).filter(
        Thesis.id == payload.thesis_id,
        Thesis.assigned_professor_id == professor.id,
    ).first()
    if not thesis:
        raise HTTPException(status_code=404, detail="Tema nuk i përket profesorit të zgjedhur")

    existing = db.query(TopicRequest).filter(
        TopicRequest.student_id == student_profile.id,
        TopicRequest.professor_id == professor.id,
        TopicRequest.thesis_id == thesis.id,
    ).first()
    if existing:
        return {
            "id": existing.id,
            "message": "Kërkesa ekziston tashmë",
            "status": existing.status,
            "requested_at": existing.requested_at.isoformat() if existing.requested_at else None,
        }

    request_row = TopicRequest(
        student_id=student_profile.id,
        professor_id=professor.id,
        thesis_id=thesis.id,
        note=(payload.note or "").strip() or None,
    )
    db.add(request_row)
    db.commit()
    db.refresh(request_row)

    logger.info(
        "Created topic request id=%s student_id=%s professor_id=%s thesis_id=%s",
        request_row.id,
        student_profile.id,
        professor.id,
        thesis.id,
    )

    return {
        "id": request_row.id,
        "message": "Kërkesa u dërgua me sukses",
        "status": request_row.status,
        "requested_at": request_row.requested_at.isoformat() if request_row.requested_at else None,
        "student": {
            "id": student_profile.id,
            "full_name": student_profile.full_name,
            "student_id": student_profile.student_id,
        },
        "professor": {
            "id": professor.id,
            "full_name": professor.full_name,
            "department": professor.department,
        },
        "topic": _serialize_topic(thesis),
    }


@router.get("/me")
def list_my_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == UserRole.student:
        student_profile = ensure_student_profile(db, current_user, create_if_missing=True)
        if not student_profile:
            return []
        rows = db.query(TopicRequest).filter(TopicRequest.student_id == student_profile.id).order_by(TopicRequest.requested_at.desc()).all()
    elif current_user.role == UserRole.professor:
        professor = ensure_professor_profile(db, current_user, create_if_missing=True)
        if not professor:
            return []
        rows = db.query(TopicRequest).filter(TopicRequest.professor_id == professor.id).order_by(TopicRequest.requested_at.desc()).all()
    else:
        rows = db.query(TopicRequest).order_by(TopicRequest.requested_at.desc()).all()

    return [
        {
            "id": row.id,
            "status": row.status,
            "requested_at": row.requested_at.isoformat() if row.requested_at else None,
            "student": {
                "id": row.student.id if row.student else None,
                "full_name": row.student.full_name if row.student else None,
                "student_id": row.student.student_id if row.student else None,
            },
            "professor": {
                "id": row.professor.id if row.professor else None,
                "full_name": row.professor.full_name if row.professor else None,
                "department": row.professor.department if row.professor else None,
            },
            "topic": _serialize_topic(row.thesis) if row.thesis else None,
            "note": row.note,
        }
        for row in rows
    ]


@router.get("/professor")
def list_professor_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != UserRole.professor:
        raise HTTPException(status_code=403, detail="Vetëm profesori mund ta shohë këtë listë")

    professor = ensure_professor_profile(db, current_user, create_if_missing=True)
    if not professor:
        return {"topics": [], "requests": []}

    topics = db.query(Thesis).filter(Thesis.assigned_professor_id == professor.id).order_by(Thesis.created_at.desc()).all()
    requests = db.query(TopicRequest).filter(TopicRequest.professor_id == professor.id).order_by(TopicRequest.requested_at.desc()).all()

    return {
        "topics": [_serialize_topic(topic) for topic in topics],
        "requests": [
            {
                "id": row.id,
                "status": row.status,
                "requested_at": row.requested_at.isoformat() if row.requested_at else None,
                "student": {
                    "id": row.student.id if row.student else None,
                    "full_name": row.student.full_name if row.student else None,
                    "student_id": row.student.student_id if row.student else None,
                },
                "topic": _serialize_topic(row.thesis) if row.thesis else None,
                "note": row.note,
            }
            for row in requests
        ],
    }