"""Restore local SQLite login accounts (no MySQL). Password hashes only, never printed."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from auth import hash_password
from models import Student, User, UserRole

DB = PROJECT_ROOT / "data" / "plagiarism_checker.db"
ADMIN_PASSWORD = "AAB-Admin-2026"
STUDENT_EMAIL = "bleonita.deskaj@universitetiaab.com"
STUDENT_USERNAME = "bleonita.deskaj"
STUDENT_PASSWORD = "AAB-Student-2026"


def main() -> None:
    engine = create_engine(f"sqlite:///{DB.as_posix()}", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        if admin:
            admin.password_hash = hash_password(ADMIN_PASSWORD)
            admin.is_active = True
            admin.role = UserRole.admin
        else:
            db.add(
                User(
                    username="admin",
                    email="admin@aab.edu.al",
                    password_hash=hash_password(ADMIN_PASSWORD),
                    full_name="Administrator AAB",
                    role=UserRole.admin,
                    is_active=True,
                )
            )

        student = db.query(User).filter(
            (User.username == STUDENT_USERNAME) | (User.email == STUDENT_EMAIL)
        ).first()
        if student:
            student.password_hash = hash_password(STUDENT_PASSWORD)
            student.email = STUDENT_EMAIL
            student.username = STUDENT_USERNAME
            student.role = UserRole.student
            student.is_active = True
            student.department = student.department or "Programim"
        else:
            student = User(
                username=STUDENT_USERNAME,
                email=STUDENT_EMAIL,
                password_hash=hash_password(STUDENT_PASSWORD),
                full_name="Bleonita Deskaj",
                role=UserRole.student,
                department="Programim",
                is_active=True,
            )
            db.add(student)
            db.flush()
            db.add(
                Student(
                    user_id=student.id,
                    full_name="Bleonita Deskaj",
                    student_id=STUDENT_USERNAME,
                    email=STUDENT_EMAIL,
                )
            )

        db.commit()
        users = db.query(User.username, User.email, User.role).all()
        print("updated users:")
        for username, email, role in users:
            role_value = role.value if hasattr(role, "value") else role
            print(f"  {username} | {email} | {role_value}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
