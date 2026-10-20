"""FastAPI application — unified entry point (legacy portals + v1 API)."""

from __future__ import annotations

import os

# Limit BLAS threads on low-RAM machines (keeps ML stable when enabled)
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
# ML models: enabled by default. Set DISABLE_ML_MODELS=1 in .env only if you get OOM errors.

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.v1.router import api_router
from app.config import PROJECT_ROOT, settings
from app.database.engine import verify_connection
from app.legacy_bootstrap import bootstrap_default_users
from database import init_legacy_schema, check_db_connection, SessionLocal
from routers import reports, students, theses
from routers.departments_router import router as departments_router
from routers.admin_agent import router as admin_agent_router
from routers.admin_dashboard import router as admin_dashboard_router
from routers.admin_professors import router as admin_professors_router
from routers.admin_settings import router as admin_settings_router
from routers.admin_theses import router as admin_theses_router
from routers.portals import router as portals_router
from routers.professor_agent import router as professor_agent_router
from routers.analysis import router as analysis_router
from routers.auth_router import router as auth_router
from routers.confidential_docs import router as conf_router
from routers.notifications import router as notifications_router
from routers.plagiarism_router import router as plagiarism_router
from routers.thesis_interactions import router as thesis_interactions_router
from routers.audit import router as audit_router
from middleware.audit_middleware import AuditMiddleware

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "plagiarism.log", encoding="utf-8"),
    ],
)
logging.getLogger("watchfiles").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

FRONTEND_DIR = PROJECT_ROOT / "frontend"
HTML_PAGES = ("ballina.html", "student.html", "profesor.html", "admin.html", "index.html", "reset-password.html")


def _init_legacy_schema() -> None:
    init_legacy_schema()


def _init_v1_schema() -> None:
    from app.database.base import Base
    from app.database.engine import engine
    from app.models import CheckResult, Document, User  # noqa: F401

    Base.metadata.create_all(bind=engine)


def _register_legacy_routers(application: FastAPI) -> None:
    application.include_router(auth_router, prefix="/api/auth", tags=["Auth"])
    application.include_router(departments_router, prefix="/api", tags=["Departamentet"])
    application.include_router(theses.router, prefix="/api/theses", tags=["Temat"])
    application.include_router(thesis_interactions_router, prefix="/api/theses", tags=["Thesis Interactions"])
    application.include_router(analysis_router, prefix="/api/analysis", tags=["Analiza"])
    application.include_router(conf_router, prefix="/api/confidential", tags=["Konfidencial"])
    application.include_router(admin_settings_router, prefix="/api/admin", tags=["Admin"])
    application.include_router(admin_dashboard_router, prefix="/api/admin", tags=["Admin"])
    application.include_router(admin_professors_router, prefix="/api/admin", tags=["Admin"])
    application.include_router(admin_theses_router, prefix="/api/admin", tags=["Admin"])
    application.include_router(admin_agent_router, prefix="/api/admin", tags=["Admin"])  # AI assistant
    application.include_router(audit_router, prefix="/api/admin", tags=["Admin"])
    application.include_router(reports.router, prefix="/api/reports", tags=["Raportet"])
    application.include_router(students.router, prefix="/api/students", tags=["Studentet"])
    application.include_router(notifications_router, prefix="/api/notifications", tags=["Notifications"])
    application.include_router(plagiarism_router, prefix="/api/plagiarism", tags=["Plagiarism"])
    application.include_router(portals_router, prefix="/api", tags=["Portals"])
    application.include_router(professor_agent_router, prefix="/api", tags=["Portals"])


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.ensure_directories()
    try:
        verify_connection()
        _init_v1_schema()
    except Exception as exc:
        logger.warning("V1 database check failed (optional): %s", exc)

    _init_legacy_schema()
    bootstrap_default_users()

    legacy_status = check_db_connection()
    if not legacy_status.get("ok"):
        logger.error("Legacy database unavailable: %s", legacy_status.get("error"))
    else:
        logger.info("Legacy database OK: %s", legacy_status.get("url"))

    logger.info("Plagiarism Checker v%s started", __version__)
    yield
    logger.info("Application shutdown")


app = FastAPI(title="Plagiarism Checker", version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuditMiddleware)

app.include_router(api_router)
_register_legacy_routers(app)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


def _serve_html(filename: str):
    for base in (PROJECT_ROOT, PROJECT_ROOT / "templates"):
        path = base / filename
        if path.exists():
            return FileResponse(path)
    return {"message": f"{filename} nuk u gjet"}


@app.get("/health")
def health() -> dict:
    legacy = check_db_connection()
    data_stats: dict = {}
    if legacy.get("ok"):
        try:
            from models import Thesis, User

            db = SessionLocal()
            try:
                data_stats = {
                    "users": db.query(User).count(),
                    "theses_total": db.query(Thesis).count(),
                    "theses_archive": db.query(Thesis).filter(Thesis.is_archive.is_(True)).count(),
                    "theses_student_uploads": db.query(Thesis).filter(Thesis.is_archive.is_(False)).count(),
                }
            finally:
                db.close()
        except Exception as exc:
            data_stats = {"error": str(exc)}

    return {
        "status": "ok" if legacy.get("ok") else "degraded",
        "version": __version__,
        "env": settings.env,
        "legacy_database": legacy,
        "data": data_stats,
        "v1_database": settings.database_url.split("@")[-1] if "@" in settings.database_url else settings.database_url,
        "pages": {
            "ballina": "/ballina.html",
            "admin": "/admin.html",
            "profesor": "/profesor.html",
            "student": "/student.html",
        },
    }


@app.get("/")
def serve_home():
    return _serve_html("ballina.html")


@app.get("/ballina.html")
def serve_ballina():
    return _serve_html("ballina.html")


def _register_html_page(page: str) -> None:
    @app.get(f"/{page}", include_in_schema=False)
    def _handler():
        return _serve_html(page)


for _page in HTML_PAGES:
    if _page != "ballina.html":
        _register_html_page(_page)


@app.get("/admin_temat.js", include_in_schema=False)
def serve_admin_js():
    path = PROJECT_ROOT / "admin_temat.js"
    if path.exists():
        return FileResponse(path, media_type="application/javascript")
    return {"message": "admin_temat.js nuk u gjet"}


@app.get("/admin.css", include_in_schema=False)
def serve_admin_css():
    path = PROJECT_ROOT / "admin.css"
    if path.exists():
        return FileResponse(path, media_type="text/css")
    return {"message": "admin.css nuk u gjet"}


@app.get("/student.css", include_in_schema=False)
def serve_student_css():
    """Serve student portal stylesheet."""
    path = PROJECT_ROOT / "student.css"
    if path.exists():
        return FileResponse(path, media_type="text/css")
    return {"message": "student.css nuk u gjet"}


@app.get("/manifest.webmanifest", include_in_schema=False)
def serve_manifest():
    path = FRONTEND_DIR / "manifest.webmanifest"
    if path.exists():
        return FileResponse(path, media_type="application/manifest+json")
    return {"message": "manifest missing"}


@app.get("/sw.js", include_in_schema=False)
def serve_sw():
    path = FRONTEND_DIR / "sw.js"
    if path.exists():
        return FileResponse(path, media_type="application/javascript")
    return {"message": "sw.js missing"}


@app.get("/profesor.css", include_in_schema=False)
def serve_profesor_css():
    path = PROJECT_ROOT / "profesor.css"
    if path.exists():
        return FileResponse(path, media_type="text/css")
    return {"message": "profesor.css nuk u gjet"}
