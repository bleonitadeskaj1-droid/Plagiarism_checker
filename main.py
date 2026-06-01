from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import uvicorn
from sqlalchemy import inspect, text
from database import engine, SessionLocal
from models import Base, Professor, User, UserRole
from pathlib import Path
from auth import hash_password

Base.metadata.create_all(bind=engine)


def ensure_schema_extensions() -> None:
    inspector = inspect(engine)
    dialect = engine.dialect.name

    with engine.begin() as connection:
        if "theses" in inspector.get_table_names():
            thesis_columns = {c["name"] for c in inspector.get_columns("theses")}
            if "assigned_professor_id" not in thesis_columns:
                col_type = "INTEGER" if dialect == "sqlite" else "INT"
                connection.execute(text(f"ALTER TABLE theses ADD COLUMN assigned_professor_id {col_type} NULL"))
            if "submitted_by_user_id" not in thesis_columns:
                col_type = "INTEGER" if dialect == "sqlite" else "INT"
                connection.execute(text(f"ALTER TABLE theses ADD COLUMN submitted_by_user_id {col_type} NULL"))
            if "workflow_status" not in thesis_columns:
                if dialect == "sqlite":
                    connection.execute(text("ALTER TABLE theses ADD COLUMN workflow_status VARCHAR(50) NOT NULL DEFAULT 'in_process'"))
                else:
                    connection.execute(text("ALTER TABLE theses ADD COLUMN workflow_status ENUM('in_process','approved','rejected','needs_revision') NOT NULL DEFAULT 'in_process'"))

        if "students" in inspector.get_table_names():
            student_columns = {c["name"] for c in inspector.get_columns("students")}
            if "user_id" not in student_columns:
                col_type = "INTEGER" if dialect == "sqlite" else "INT"
                connection.execute(text(f"ALTER TABLE students ADD COLUMN user_id {col_type} NULL"))


ensure_schema_extensions()
Base.metadata.create_all(bind=engine)


def bootstrap_default_admin() -> None:
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == "admin").first()
        if existing:
            existing.email = "admin@aab.edu.al"
            existing.password_hash = hash_password("Admin123")
            existing.full_name = "Administrator AAB"
            existing.role = UserRole.admin
            existing.department = None
            existing.is_active = True
            db.commit()
            return

        db.add(User(
            username="admin",
            email="admin@aab.edu.al",
            password_hash=hash_password("Admin123"),
            full_name="Administrator AAB",
            role=UserRole.admin,
            department=None,
            is_active=True,
        ))
        db.commit()
    finally:
        db.close()


bootstrap_default_admin()


def bootstrap_demo_professor() -> None:
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == "prof.demo").first()
        if existing:
            existing.email = "prof.demo@aab.edu.al"
            existing.password_hash = hash_password("Prof@2024")
            existing.full_name = "Prof. Demo Demoviqi"
            existing.role = UserRole.professor
            existing.department = "Informatikë"
            existing.is_active = True
            db.commit()
            return

        db.add(User(
            username="prof.demo",
            email="prof.demo@aab.edu.al",
            password_hash=hash_password("Prof@2024"),
            full_name="Prof. Demo Demoviqi",
            role=UserRole.professor,
            department="Informatikë",
            is_active=True,
        ))
        db.commit()
    finally:
        db.close()


bootstrap_demo_professor()


def bootstrap_professor_profiles() -> None:
    db = SessionLocal()
    try:
        professors = db.query(User).filter(User.role == UserRole.professor, User.is_active == True).all()
        for user in professors:
            profile = db.query(Professor).filter(Professor.user_id == user.id).first()
            if profile:
                profile.full_name = user.full_name or user.username
                profile.department = user.department
                profile.is_active = True
                continue
            db.add(Professor(
                user_id=user.id,
                full_name=user.full_name or user.username,
                department=user.department,
                is_active=True,
            ))
        db.commit()
    finally:
        db.close()


bootstrap_professor_profiles()

app = FastAPI(title="Plagiarism Analyzer API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

from routers import theses, reports, students, topic_requests
from routers.admin_dashboard import router as admin_dashboard_router
from routers.auth_router import router as auth_router
from routers.confidential_docs import router as conf_router
from routers.admin_settings import router as admin_settings_router
from routers.admin_professors import router as admin_professors_router
from routers.analysis import router as analysis_router
from routers.notifications import router as notifications_router
from routers.thesis_interactions import router as thesis_interactions_router

app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])
app.include_router(theses.router,  prefix="/api/theses",       tags=["Temat"])
app.include_router(analysis_router,prefix="/api/analysis",     tags=["Analiza"])
app.include_router(conf_router,     prefix="/api/confidential", tags=["Konfidencial"])
app.include_router(admin_settings_router, prefix="/api/admin", tags=["Admin"])
app.include_router(admin_dashboard_router, prefix="/api/admin", tags=["Admin"])
app.include_router(admin_professors_router, prefix="/api/admin", tags=["Admin"])
app.include_router(reports.router, prefix="/api/reports",      tags=["Raportet"])
app.include_router(students.router,prefix="/api/students",     tags=["Studentet"])
app.include_router(notifications_router, prefix="/api/notifications", tags=["Notifications"])
app.include_router(thesis_interactions_router, prefix="/api/theses", tags=["Thesis Interactions"])
app.include_router(topic_requests.router, prefix="/api/topic-requests", tags=["Topic Requests"])


def _serve_html(filename: str):
    page_path = Path(__file__).parent / filename
    if page_path.exists():
        return FileResponse(page_path)
    return {"message": f"{filename} nuk u gjet"}

@app.get("/")
def root():
    return _serve_html("ballina.html")


@app.get("/index.html")
def index_page():
    return _serve_html("index.html")


@app.get("/ballina.html")
def ballina_page():
    return _serve_html("ballina.html")


@app.get("/profesor.html")
def professor_page():
    return _serve_html("profesor.html")


@app.get("/student.html")
def student_page():
    return _serve_html("student.html")


@app.get("/admin.html")
def admin_page():
    return _serve_html("admin.html")

@app.get("/health")
def health(): return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)