from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth import get_current_user
from access_control import ensure_professor_profile
from database import get_db
from models import Notification, User, UserRole

router = APIRouter()


@router.get("/")
def list_notifications(
    unread_only: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == UserRole.professor:
        profile = ensure_professor_profile(db, current_user, create_if_missing=True)
        if not profile:
            return []
        query = db.query(Notification).filter(Notification.recipient_professor_id == profile.id)
    elif current_user.role == UserRole.admin:
        query = db.query(Notification)
    else:
        raise HTTPException(status_code=403, detail="Nuk keni akses në njoftime")

    if unread_only:
        query = query.filter(Notification.is_read == False)

    notes = query.order_by(Notification.created_at.desc()).all()
    return [
        {
            "id": n.id,
            "thesis_id": n.thesis_id,
            "title": n.title,
            "message": n.message,
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in notes
    ]


@router.patch("/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    note = db.query(Notification).filter(Notification.id == notification_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Njoftimi nuk u gjet")

    if current_user.role == UserRole.professor:
        profile = ensure_professor_profile(db, current_user, create_if_missing=True)
        if not profile or note.recipient_professor_id != profile.id:
            raise HTTPException(status_code=403, detail="Nuk keni akses në këtë njoftim")
    elif current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Nuk keni akses në këtë njoftim")

    note.is_read = True
    db.commit()
    return {"message": "Njoftimi u shënua si i lexuar"}
