# ============================================
# routers/auth_router.py - Login dhe Perdoruesit
# ============================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from database import get_db
from models import Professor, User, UserRole
from auth import hash_password, verify_password, create_token, require_admin, get_current_user
from access_control import ensure_student_profile

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str
    email: str
    password: str
    full_name: Optional[str] = None
    role: UserRole = UserRole.student
    department: Optional[str] = None


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    full_name: Optional[str] = None
    department: Optional[str] = None


def _ensure_professor_profile(db: Session, user: User) -> None:
    if user.role != UserRole.professor:
        return
    profile = db.query(Professor).filter(Professor.user_id == user.id).first()
    if profile:
        profile.full_name = user.full_name or user.username
        profile.department = user.department
        profile.is_active = bool(user.is_active)
        return

    db.add(Professor(
        user_id=user.id,
        full_name=user.full_name or user.username,
        department=user.department,
        is_active=bool(user.is_active),
    ))


@router.post("/login")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(
        or_(User.username == data.username, User.email == data.username),
        User.is_active == True
    ).first()

    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Kredenciale të gabuara")

    user.last_login = datetime.utcnow()
    db.commit()

    token = create_token(user.id, user.role, user.department)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role,
            "department": user.department,
        }
    }


@router.post("/register")
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(status_code=400, detail="Username ekziston")
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(status_code=400, detail="Email ekziston")

    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        full_name=data.full_name,
        role=UserRole.student,
        department=data.department,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    ensure_student_profile(db, user, create_if_missing=True)

    token = create_token(user.id, user.role, user.department)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role,
            "department": user.department,
        }
    }


@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "department": current_user.department,
    }


@router.post("/users")
def create_user(
    data: UserCreate,
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
        role=data.role,
        department=data.department,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    if data.role == UserRole.professor:
        _ensure_professor_profile(db, user)
    elif data.role == UserRole.student:
        ensure_student_profile(db, user, create_if_missing=True)
    db.commit()

    return {"id": user.id, "message": f"Përdoruesi '{user.username}' u krijua me rol {user.role}"}


@router.get("/users")
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    users = db.query(User).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "full_name": u.full_name,
            "role": u.role,
            "department": u.department,
            "is_active": u.is_active,
            "email": u.email,
            "last_login": u.last_login.isoformat() if u.last_login else None,
        }
        for u in users
    ]


@router.patch("/users/{user_id}/deactivate")
def deactivate_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Nuk mund ta çaktivizoni veten")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Përdoruesi nuk u gjet")
    user.is_active = False
    db.commit()
    return {"message": f"Përdoruesi '{user.username}' u çaktivizua"}
