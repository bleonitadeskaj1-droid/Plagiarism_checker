"""Sync user roles with email domain rules (student vs professor)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from access_control import ensure_student_profile
from auth_email import is_professor_email, is_student_email
from models import Professor, Student, Thesis, User, UserRole


def sync_user_role_from_email(db: Session, user: User) -> bool:
    if user.role == UserRole.admin:
        return False

    changed = False
    email = user.email or ""

    if is_professor_email(email) and user.role != UserRole.professor:
        user.role = UserRole.professor
        changed = True
        student = db.query(Student).filter(Student.user_id == user.id).first()
        if student:
            has_theses = db.query(Thesis).filter(
                (Thesis.student_id == student.id) | (Thesis.submitted_by_user_id == user.id)
            ).first()
            if not has_theses:
                db.delete(student)
        _ensure_professor_profile(db, user)
    elif is_student_email(email) and user.role != UserRole.student:
        user.role = UserRole.student
        changed = True
        ensure_student_profile(db, user, create_if_missing=True)

    return changed


def _ensure_professor_profile(db: Session, user: User) -> None:
    profile = db.query(Professor).filter(Professor.user_id == user.id).first()
    if profile:
        profile.full_name = user.full_name or user.username
        profile.department = user.department
        profile.is_active = bool(user.is_active)
        return
    db.add(
        Professor(
            user_id=user.id,
            full_name=user.full_name or user.username,
            department=user.department,
            is_active=bool(user.is_active),
        )
    )


def sync_all_user_roles(db: Session) -> int:
    """Fix every user whose role does not match their email domain."""
    fixed = 0
    for user in db.query(User).filter(User.is_active.is_(True)).all():
        if sync_user_role_from_email(db, user):
            fixed += 1
    if fixed:
        db.commit()
    return fixed
