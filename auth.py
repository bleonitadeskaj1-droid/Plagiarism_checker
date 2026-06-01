# ============================================
# auth.py - Autentikimi JWT
# ============================================

from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
import jwt
import bcrypt
import os

from database import get_db
from models import User, UserRole

SECRET_KEY  = os.getenv("JWT_SECRET", "super-secret-change-in-production")
ALGORITHM   = "HS256"
TOKEN_HOURS = 8

security = HTTPBearer()


# ── FJALËKALIMI ──
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ── TOKEN ──
def create_token(user_id: int, role: str, department: str = None) -> str:
    payload = {
        "sub": str(user_id),
        "role": role,
        "department": department,
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_HOURS)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token ka skaduar")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token i pavlefshëm")


# ── DEPENDENCIES ──
def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
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