"""Complete user deletion (profiles + related rows)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from models import (
    ConfidentialDocument,
    Notification,
    PlagiarismMatch,
    Professor,
    Review,
    Student,
    Thesis,
    User,
    UserRole,
)
from services.legacy_table_cleanup import (
    delete_legacy_professor_topic_rows,
    delete_legacy_student_rows,
    delete_legacy_user_message_rows,
    delete_user_auxiliary_rows,
    thesis_ids_linked_to_user,
)
from services.thesis_delete import delete_thesis_completely


def _delete_user_confidential_documents(db: Session, user_id: int) -> None:
    doc_ids = [
        row[0]
        for row in db.query(ConfidentialDocument.id)
        .filter(ConfidentialDocument.uploaded_by == user_id)
        .all()
    ]
    if not doc_ids:
        return
    db.query(PlagiarismMatch).filter(PlagiarismMatch.conf_source_id.in_(doc_ids)).delete(
        synchronize_session=False
    )
    db.query(ConfidentialDocument).filter(ConfidentialDocument.id.in_(doc_ids)).delete(
        synchronize_session=False
    )


def _delete_user_theses(db: Session, user_id: int, student_id: int | None) -> None:
    thesis_ids = thesis_ids_linked_to_user(db, user_id, student_id)
    if not thesis_ids:
        return
    for thesis in db.query(Thesis).filter(Thesis.id.in_(thesis_ids)).all():
        delete_thesis_completely(db, thesis)


def delete_user_completely(db: Session, user: User, *, allow_last_admin: bool = False) -> None:
    user_id = user.id

    if user.role == UserRole.admin and not allow_last_admin:
        admin_count = (
            db.query(User)
            .filter(User.role == UserRole.admin, User.is_active.is_(True))
            .count()
        )
        if admin_count <= 1:
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail="Nuk mund të fshihet admini i fundit")

    student = db.query(Student).filter(Student.user_id == user_id).first()
    professor = db.query(Professor).filter(Professor.user_id == user_id).first()
    student_id = student.id if student else None

    if student:
        delete_legacy_student_rows(db, student_id)

    _delete_user_theses(db, user_id, student_id)

    if professor:
        delete_legacy_professor_topic_rows(db, professor.id)
        db.query(Notification).filter(Notification.recipient_professor_id == professor.id).delete(
            synchronize_session=False
        )
        db.query(Review).filter(Review.professor_id == professor.id).delete(synchronize_session=False)
        db.query(Thesis).filter(Thesis.assigned_professor_id == professor.id).update(
            {Thesis.assigned_professor_id: None}, synchronize_session=False
        )
        db.query(Professor).filter(Professor.id == professor.id).delete(synchronize_session=False)

    if student:
        db.query(Student).filter(Student.id == student_id).delete(synchronize_session=False)

    _delete_user_confidential_documents(db, user_id)
    delete_legacy_user_message_rows(db, user_id)
    delete_user_auxiliary_rows(db, user_id)
    db.query(User).filter(User.id == user_id).delete(synchronize_session=False)
    db.commit()
