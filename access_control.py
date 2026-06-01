from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Notification, Professor, Review, Student, SubmissionWorkflowStatus, Thesis, User, UserRole


def ensure_professor_profile(db: Session, user: User, create_if_missing: bool = True) -> Professor | None:
    if user.role != UserRole.professor:
        return None

    profile = db.query(Professor).filter(Professor.user_id == user.id).first()
    if profile or not create_if_missing:
        return profile

    profile = Professor(
        user_id=user.id,
        full_name=user.full_name or user.username,
        department=user.department,
        is_active=bool(user.is_active),
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def ensure_student_profile(db: Session, user: User, create_if_missing: bool = True) -> Student | None:
    if user.role != UserRole.student:
        return None

    profile = db.query(Student).filter(Student.user_id == user.id).first()
    if profile or not create_if_missing:
        return profile

    student_code = f"STU-{user.id}"
    suffix = 1
    while db.query(Student).filter(Student.student_id == student_code).first():
        student_code = f"STU-{user.id}-{suffix}"
        suffix += 1

    profile = Student(
        user_id=user.id,
        university_id=None,
        full_name=user.full_name or user.username,
        student_id=student_code,
        email=user.email,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def assert_thesis_access(db: Session, thesis: Thesis, current_user: User) -> None:
    if current_user.role == UserRole.admin:
        return

    if current_user.role == UserRole.student:
        if thesis.submitted_by_user_id == current_user.id:
            return
        raise HTTPException(status_code=403, detail="Nuk keni akses në këtë temë")

    if current_user.role == UserRole.professor:
        profile = ensure_professor_profile(db, current_user, create_if_missing=True)
        if profile and thesis.assigned_professor_id == profile.id:
            return
        raise HTTPException(status_code=403, detail="Kjo temë nuk është e caktuar për ju")

    raise HTTPException(status_code=403, detail="Roli nuk ka akses")


def get_thesis_with_access(db: Session, thesis_id: int, current_user: User) -> Thesis:
    thesis = db.query(Thesis).filter(Thesis.id == thesis_id).first()
    if not thesis:
        raise HTTPException(status_code=404, detail="Tema nuk u gjet")
    assert_thesis_access(db, thesis, current_user)
    return thesis


def can_view_final_review(thesis: Thesis, current_user: User, db: Session) -> bool:
    if current_user.role == UserRole.admin:
        return True

    if current_user.role == UserRole.professor:
        profile = ensure_professor_profile(db, current_user, create_if_missing=True)
        return bool(profile and thesis.assigned_professor_id == profile.id)

    if current_user.role == UserRole.student:
        if thesis.submitted_by_user_id != current_user.id:
            return False
        return thesis.workflow_status != SubmissionWorkflowStatus.in_process

    return False


def create_assignment_notification(
    db: Session,
    thesis: Thesis,
    professor_id: int,
    student_display_name: str,
) -> Notification:
    note = Notification(
        recipient_professor_id=professor_id,
        thesis_id=thesis.id,
        title="Tema e re e caktuar",
        message=f"Tema '{thesis.title}' u caktua nga studenti {student_display_name}.",
        is_read=False,
    )
    db.add(note)
    return note
