"""Bootstrap default users for legacy auth — additive only, never overwrites existing data."""

from __future__ import annotations

import logging
import os

from app.config import settings
from auth import hash_password
from database import SessionLocal
from departments import PROFESSOR_DEPARTMENT
from models import Professor, User, UserRole

logger = logging.getLogger(__name__)

MIN_ADMIN_PASSWORD_LENGTH = 12
_WEAK_ADMIN_PASSWORDS = {
    "admin",
    "admin123",
    "administrator",
    "changeme",
    "password",
    "password123",
    "12345678",
    "123456789012",
}
# Development convenience only, and explicitly rejected in production.
# Set ADMIN_PASSWORD in .env to choose your own local password instead.
_DEV_ADMIN_PASSWORD = "dev-only-admin-password"


class AdminBootstrapError(RuntimeError):
    """Raised when production is missing a usable initial admin password."""


def _resolve_admin_password() -> tuple[str, str]:
    """Return (password, source) for the first admin. The value is never logged."""
    password = os.getenv("ADMIN_PASSWORD") or ""

    if settings.env != "production":
        if password:
            return password, "ADMIN_PASSWORD"
        logger.warning(
            "No admin account found; creating one with the development-only fallback "
            "defined in app/legacy_bootstrap.py. Set ADMIN_PASSWORD in .env to choose "
            "your own password. ADMIN_PASSWORD is mandatory when ENV=production."
        )
        return _DEV_ADMIN_PASSWORD, "development-only fallback"

    if not password:
        raise AdminBootstrapError(
            "No admin account exists and ADMIN_PASSWORD is not set. "
            "Set ADMIN_PASSWORD before starting with ENV=production."
        )
    too_weak = (
        len(password) < MIN_ADMIN_PASSWORD_LENGTH
        or password.lower() in _WEAK_ADMIN_PASSWORDS
        or password == _DEV_ADMIN_PASSWORD
    )
    if too_weak:
        raise AdminBootstrapError(
            "ADMIN_PASSWORD is too weak: use at least "
            f"{MIN_ADMIN_PASSWORD_LENGTH} characters and avoid common or development defaults."
        )
    return password, "ADMIN_PASSWORD"


def bootstrap_default_users() -> None:
    """Create the admin account only when missing. Existing users are left unchanged."""
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        if not admin:
            password, source = _resolve_admin_password()
            db.add(
                User(
                    username="admin",
                    email="admin@aab.edu.al",
                    password_hash=hash_password(password),
                    full_name="Administrator AAB",
                    role=UserRole.admin,
                    is_active=True,
                )
            )
            logger.info("Created initial admin account (password source: %s)", source)

        db.commit()

        for user in db.query(User).filter(User.role == UserRole.professor, User.is_active == True):
            profile = db.query(Professor).filter(Professor.user_id == user.id).first()
            if not profile:
                db.add(
                    Professor(
                        user_id=user.id,
                        full_name=user.full_name or user.username,
                        department=PROFESSOR_DEPARTMENT,
                        is_active=True,
                    )
                )
        db.commit()

        from departments import backfill_professor_departments

        backfill_professor_departments(db.get_bind())
        db.commit()
        logger.info("Legacy user bootstrap complete (existing data preserved)")
    except AdminBootstrapError:
        db.rollback()
        raise
    except Exception as exc:
        logger.error("Legacy user bootstrap failed: %s", exc)
        db.rollback()
    finally:
        db.close()
