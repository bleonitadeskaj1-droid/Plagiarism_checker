# ============================================
# auth.py - Autentikimi JWT
# ============================================

import logging
import os
from datetime import datetime, timedelta

import bcrypt
import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from database import get_db
from models import User, UserRole

load_dotenv()
logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv("JWT_SECRET") or os.getenv("SECRET_KEY") or "change-me-in-production-min-16-chars"
ALGORITHM = "HS256"
TOKEN_HOURS = 8

security = HTTPBearer()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: int, role: str, department: str | None = None) -> str:
    role_value = role.value if hasattr(role, "value") else role
    payload = {
        "sub": str(user_id),
        "role": role_value,
        "department": department,
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_HOURS),
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    logger.debug("JWT issued for user_id=%s role=%s", user_id, role_value)
    return token


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token ka skaduar")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token i pavlefshëm")


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    payload = decode_token(credentials.credentials)
    user = db.query(User).filter(User.id == int(payload["sub"]), User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="Përdoruesi nuk ekziston")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Kërkohet roli Admin")
    return current_user


def require_admin_or_professor(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in [UserRole.admin, UserRole.professor]:
        raise HTTPException(status_code=403, detail="Kërkohet roli Admin ose Profesor")
    return current_user
