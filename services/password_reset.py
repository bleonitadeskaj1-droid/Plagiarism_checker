"""Rivendosje fjalëkalimi me token të sigurt dhe email."""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from auth import hash_password
from models import PasswordResetToken, User
from services.email_service import send_email, smtp_configured

logger = logging.getLogger(__name__)

RESET_MESSAGE = (
    "Nëse adresa email ekziston në sistem, do të merrni së shpejti "
    "udhëzime për rivendosjen e fjalëkalimit."
)
TOKEN_TTL_MINUTES = int(os.getenv("PASSWORD_RESET_TTL_MINUTES", "60"))


def _public_app_url() -> str:
    return (os.getenv("APP_PUBLIC_URL") or "http://127.0.0.1:8000").rstrip("/")


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _log_dev_reset_link(email: str, reset_url: str) -> None:
    line = f"[PASSWORD RESET] {email} -> {reset_url}\n"
    logger.warning("SMTP jo aktiv — lidhja e rivendosjes: %s", reset_url)
    log_path = Path(__file__).resolve().parent.parent / "logs" / "password_reset.log"
    log_path.parent.mkdir(exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def request_password_reset(db: Session, email: str) -> dict:
    normalized = (email or "").strip().lower()
    if not normalized or "@" not in normalized:
        return {"message": RESET_MESSAGE}

    user = (
        db.query(User)
        .filter(User.email == normalized, User.is_active == True)
        .first()
    )
    if not user:
        return {"message": RESET_MESSAGE}

    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None),
    ).update({"used_at": datetime.utcnow()}, synchronize_session=False)

    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(minutes=TOKEN_TTL_MINUTES)
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=_hash_token(raw_token),
            expires_at=expires_at,
        )
    )
    db.commit()

    reset_url = f"{_public_app_url()}/reset-password.html?token={raw_token}"
    subject = "Rivendos fjalëkalimin — Universiteti AAB"
    text_body = (
        f"Përshëndetje {user.full_name or user.username},\n\n"
        f"Kliko lidhjen më poshtë për të vendosur fjalëkalim të ri "
        f"(vlen {TOKEN_TTL_MINUTES} minuta):\n\n{reset_url}\n\n"
        "Nëse nuk e ke kërkuar ti, injoroje këtë email.\n"
        "Universiteti AAB — Sistemi i Plagjiaturës"
    )
    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;color:#222">
      <h2 style="color:#6B1414">Rivendos fjalëkalimin</h2>
      <p>Përshëndetje <strong>{user.full_name or user.username}</strong>,</p>
      <p>Kliko butonin për të vendosur fjalëkalim të ri (vlen {TOKEN_TTL_MINUTES} minuta):</p>
      <p style="text-align:center;margin:28px 0">
        <a href="{reset_url}" style="background:#C63F3F;color:#fff;padding:12px 24px;
           text-decoration:none;border-radius:6px;font-weight:600">Vendos fjalëkalim të ri</a>
      </p>
      <p style="font-size:13px;color:#666">Ose kopjo lidhjen:<br><a href="{reset_url}">{reset_url}</a></p>
      <p style="font-size:12px;color:#888">Nëse nuk e ke kërkuar ti, injoroje këtë email.</p>
    </div>
    """

    dev_log = os.getenv("PASSWORD_RESET_DEV_LOG", "1").lower() in {"1", "true", "yes"}
    sent = False
    if smtp_configured():
        try:
            sent = send_email(
                to_email=user.email,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
            )
        except Exception:
            if dev_log:
                _log_dev_reset_link(user.email, reset_url)
    elif dev_log:
        _log_dev_reset_link(user.email, reset_url)

    result = {"message": RESET_MESSAGE}
    if not sent and dev_log and os.getenv("ENV", "development").lower() != "production":
        result["dev_reset_url"] = reset_url
    return result


def validate_reset_token(db: Session, raw_token: str) -> bool:
    if not raw_token or len(raw_token) < 20:
        return False
    row = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.token_hash == _hash_token(raw_token),
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > datetime.utcnow(),
        )
        .first()
    )
    return row is not None


def reset_password_with_token(db: Session, raw_token: str, new_password: str) -> None:
    if len(new_password or "") < 8:
        raise ValueError("Fjalëkalimi duhet të ketë min. 8 karaktere")

    row = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.token_hash == _hash_token(raw_token),
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > datetime.utcnow(),
        )
        .first()
    )
    if not row:
        raise ValueError("Lidhja e rivendosjes është e pavlefshme ose ka skaduar")

    user = db.query(User).filter(User.id == row.user_id, User.is_active == True).first()
    if not user:
        raise ValueError("Përdoruesi nuk u gjet")

    user.password_hash = hash_password(new_password)
    row.used_at = datetime.utcnow()
    db.commit()
