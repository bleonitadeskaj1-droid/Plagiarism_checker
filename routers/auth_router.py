# ============================================
# routers/auth_router.py - Login dhe Perdoruesit
# ============================================

import logging
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db

logger = logging.getLogger(__name__)
from models import Professor, User, UserRole
from auth import hash_password, verify_password, create_token, require_admin, get_current_user
from access_control import ensure_student_profile
from auth_email import (
    PROFESSOR_EMAIL_MESSAGE,
    STUDENT_EMAIL_MESSAGE,
    assert_professor_email,
    assert_student_email,
    is_professor_email,
    is_student_email,
    role_for_registration,
)
from services.role_sync import sync_user_role_from_email

from services.user_delete import delete_user_completely
from departments import (
    PROFESSOR_DEPARTMENT,
    is_valid_professor_department,
    is_valid_student_specialization,
    normalize_professor_department,
    normalize_student_specialization,
)
from services.password_reset import (
    request_password_reset,
    reset_password_with_token,
    validate_reset_token,
)

router = APIRouter()


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


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
    register_as: Literal["student", "professor"]


def _user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.value if hasattr(user.role, "value") else user.role,
        "department": user.department,
    }


def _sync_user_role_from_email(db: Session, user: User) -> bool:
    return sync_user_role_from_email(db, user)


def _enforce_role_email(user: User) -> None:
    if user.role == UserRole.admin:
        return
    if user.role == UserRole.student and not is_student_email(user.email):
        raise HTTPException(status_code=403, detail=STUDENT_EMAIL_MESSAGE)
    if user.role == UserRole.professor and not is_professor_email(user.email):
        raise HTTPException(status_code=403, detail=PROFESSOR_EMAIL_MESSAGE)


def _ensure_professor_profile(db: Session, user: User) -> None:
    if user.role != UserRole.professor:
        return
    if user.department != PROFESSOR_DEPARTMENT:
        user.department = PROFESSOR_DEPARTMENT
    profile = db.query(Professor).filter(Professor.user_id == user.id).first()
    if profile:
        profile.full_name = user.full_name or user.username
        profile.department = PROFESSOR_DEPARTMENT
        profile.is_active = bool(user.is_active)
        return

    db.add(Professor(
        user_id=user.id,
        full_name=user.full_name or user.username,
        department=PROFESSOR_DEPARTMENT,
        is_active=bool(user.is_active),
    ))


@router.post("/login")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    logger.info("Login attempt for identifier=%s", data.username)
    try:
        user = db.query(User).filter(
            or_(User.username == data.username, User.email == data.username),
            User.is_active == True
        ).first()
    except OperationalError as exc:
        logger.error("Database unavailable during login: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Databaza nuk është e disponueshme. Kontrollo MySQL/XAMPP dhe variablat .env.",
        ) from exc
    except SQLAlchemyError as exc:
        logger.exception("Unexpected database error during login")
        raise HTTPException(status_code=500, detail="Gabim i brendshëm i databazës") from exc

    if not user or not verify_password(data.password, user.password_hash):
        logger.warning("Login failed for identifier=%s", data.username)
        raise HTTPException(status_code=401, detail="Kredenciale të gabuara")

    if _sync_user_role_from_email(db, user):
        db.commit()
        db.refresh(user)

    _enforce_role_email(user)

    if user.role == UserRole.professor:
        _ensure_professor_profile(db, user)

    user.last_login = datetime.utcnow()
    db.commit()
    db.refresh(user)

    token = create_token(user.id, user.role, user.department)
    logger.info("Login successful for user_id=%s username=%s role=%s", user.id, user.username, user.role)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": _user_payload(user),
    }


@router.post("/register")
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    role = role_for_registration(data.email, data.register_as)

    if role == UserRole.professor:
        dept = normalize_professor_department(data.department)
        if not dept:
            raise HTTPException(status_code=400, detail="Profesorët duhet të zgjedhin departamentin")
        if not is_valid_professor_department(data.department):
            raise HTTPException(status_code=400, detail="Departamenti i zgjedhur nuk është i vlefshëm")
    else:
        dept = normalize_student_specialization(data.department)
        if not dept:
            raise HTTPException(status_code=400, detail="Studentët duhet të zgjedhin drejtimin")
        if not is_valid_student_specialization(dept):
            raise HTTPException(status_code=400, detail="Drejtimi i zgjedhur nuk është i vlefshëm")

    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(status_code=400, detail="Username ekziston")
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(status_code=400, detail="Email ekziston")

    user = User(
        username=data.username,
        email=data.email.strip().lower(),
        password_hash=hash_password(data.password),
        full_name=data.full_name,
        role=role,
        department=dept,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Siguri: detyro rolin e saktë në DB (edhe nëse enum default mbishkruan)
    db.execute(text("UPDATE users SET role = :role WHERE id = :id"), {"role": role.value, "id": user.id})
    db.commit()
    db.refresh(user)

    if user.role != role:
        logger.error("Role mismatch after insert: expected=%s got=%s user_id=%s", role, user.role, user.id)
        raise HTTPException(status_code=500, detail="Roli nuk u ruajt saktë. Provo përsëri.")

    if role == UserRole.professor:
        _ensure_professor_profile(db, user)
    else:
        ensure_student_profile(db, user, create_if_missing=True)

    if sync_user_role_from_email(db, user):
        db.commit()
        db.refresh(user)
        if user.role == UserRole.professor:
            _ensure_professor_profile(db, user)
            db.commit()

    token = create_token(user.id, user.role, user.department)
    logger.info("Register successful user_id=%s role=%s email=%s", user.id, user.role, user.email)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": _user_payload(user),
    }


@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role.value if hasattr(current_user.role, "value") else current_user.role,
        "department": current_user.department,
    }


@router.post("/forgot-password")
def forgot_password(data: ForgotPasswordRequest, db: Session = Depends(get_db)):
    try:
        return request_password_reset(db, data.email)
    except Exception as exc:
        logger.exception("Forgot password failed for email=%s", data.email)
        raise HTTPException(status_code=500, detail="Dërgimi i email-it dështoi") from exc


@router.get("/reset-password/validate")
def validate_password_reset_token(token: str, db: Session = Depends(get_db)):
    if not validate_reset_token(db, token):
        raise HTTPException(status_code=400, detail="Lidhja e rivendosjes është e pavlefshme ose ka skaduar")
    return {"valid": True}


@router.post("/reset-password")
def reset_password(data: ResetPasswordRequest, db: Session = Depends(get_db)):
    try:
        reset_password_with_token(db, data.token, data.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "Fjalëkalimi u ndryshua me sukses. Tani mund të hysh në sistem."}


@router.post("/change-password")
def change_password(
    data: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if len(data.new_password) < 8:
        raise HTTPException(status_code=400, detail="Fjalëkalimi i ri duhet min. 8 karaktere")
    if not verify_password(data.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Fjalëkalimi aktual është i gabuar")
    current_user.password_hash = hash_password(data.new_password)
    db.commit()
    return {"message": "Fjalëkalimi u ndryshua me sukses"}


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

    if data.role == UserRole.student:
        assert_student_email(data.email)
    elif data.role == UserRole.professor:
        assert_professor_email(data.email)

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
            "role": u.role.value if hasattr(u.role, "value") else u.role,
            "department": u.department,
            "is_active": u.is_active,
            "email": u.email,
            "last_login": u.last_login.isoformat() if u.last_login else None,
        }
        for u in users
    ]


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Nuk mund ta fshish veten")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Përdoruesi nuk u gjet")

    try:
        delete_user_completely(db, user)
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Fshirja dështoi: {exc}") from exc

    return {"message": "Përdoruesi u fshi", "id": user_id}


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
