from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional

from access_control import ensure_professor_profile
from auth import require_admin, hash_password
from database import get_db
from models import Professor, Thesis, User, UserRole

router = APIRouter()


class ProfessorCreate(BaseModel):
    username: str
    email: str
    password: str
    full_name: str
    department: Optional[str] = None
    is_active: bool = True


class ProfessorUpdate(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    full_name: Optional[str] = None
    department: Optional[str] = None
    is_active: Optional[bool] = None


@router.get("/professors")
def list_professors(
    include_inactive: bool = True,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    query = db.query(Professor).join(User, User.id == Professor.user_id)
    if not include_inactive:
        query = query.filter(Professor.is_active == True, User.is_active == True)

    rows = query.order_by(Professor.full_name.asc()).all()
    response = []
    for professor in rows:
        thesis_count = db.query(func.count(Thesis.id)).filter(Thesis.assigned_professor_id == professor.id).scalar() or 0
        response.append({
            "id": professor.id,
            "user_id": professor.user_id,
            "username": professor.user.username if professor.user else None,
            "email": professor.user.email if professor.user else None,
            "full_name": professor.full_name,
            "department": professor.department,
            "is_active": professor.is_active and bool(professor.user.is_active if professor.user else True),
            "created_at": professor.created_at.isoformat() if professor.created_at else None,
            "thesis_count": int(thesis_count),
        })
    return response


@router.post("/professors")
def create_professor(
    data: ProfessorCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(status_code=400, detail="Username ekziston")
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(status_code=400, detail="Email ekziston")

    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        full_name=data.full_name,
        role=UserRole.professor,
        department=data.department,
        is_active=data.is_active,
    )
    db.add(user)
    db.flush()

    professor = Professor(
        user_id=user.id,
        full_name=data.full_name,
        department=data.department,
        is_active=data.is_active,
    )
    db.add(professor)
    db.commit()
    db.refresh(professor)
    ensure_professor_profile(db, user, create_if_missing=True)

    return {
        "id": professor.id,
        "user_id": user.id,
        "message": "Profesori u krijua me sukses",
    }


@router.put("/professors/{professor_id}")
def update_professor(
    professor_id: int,
    data: ProfessorUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    professor = db.query(Professor).filter(Professor.id == professor_id).first()
    if not professor:
        raise HTTPException(status_code=404, detail="Profesori nuk u gjet")

    user = db.query(User).filter(User.id == professor.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Përdoruesi i profesorit nuk u gjet")

    if data.username and data.username != user.username:
        if db.query(User).filter(User.username == data.username, User.id != user.id).first():
            raise HTTPException(status_code=400, detail="Username ekziston")
        user.username = data.username
    if data.email and data.email != user.email:
        if db.query(User).filter(User.email == data.email, User.id != user.id).first():
            raise HTTPException(status_code=400, detail="Email ekziston")
        user.email = data.email
    if data.password:
        user.password_hash = hash_password(data.password)
    if data.full_name is not None:
        user.full_name = data.full_name
        professor.full_name = data.full_name
    if data.department is not None:
        user.department = data.department
        professor.department = data.department
    if data.is_active is not None:
        user.is_active = data.is_active
        professor.is_active = data.is_active

    db.commit()
    return {"id": professor.id, "message": "Profesori u përditësua"}


@router.delete("/professors/{professor_id}")
def delete_professor(
    professor_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    professor = db.query(Professor).filter(Professor.id == professor_id).first()
    if not professor:
        raise HTTPException(status_code=404, detail="Profesori nuk u gjet")

    assigned_count = db.query(func.count(Thesis.id)).filter(Thesis.assigned_professor_id == professor.id).scalar() or 0
    if assigned_count:
        professor.is_active = False
        if professor.user:
            professor.user.is_active = False
        db.commit()
        return {
            "message": "Profesori ka tema të caktuara; u çaktivizua në vend të fshirjes",
            "assigned_theses": int(assigned_count),
        }

    if professor.user:
        professor.user.is_active = False
    professor.is_active = False
    db.commit()
    return {"message": "Profesori u çaktivizua"}
