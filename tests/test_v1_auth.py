"""Authentication and role checks for the /api/v1 API surface."""

from __future__ import annotations

from datetime import datetime, timedelta

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.router import api_router
from app.database.base import Base
from app.dependencies import get_db
from app.models import CheckResult, Document, User as V1User  # noqa: F401
from auth import SECRET_KEY, ALGORITHM, get_current_user
from models import User, UserRole

PROTECTED_REQUESTS = [
    ("post", "/api/v1/documents/upload"),
    ("get", "/api/v1/documents/"),
    ("get", "/api/v1/documents/1"),
    ("post", "/api/v1/checks/run"),
    ("get", "/api/v1/checks/1"),
    ("get", "/api/v1/checks/1/report"),
    ("post", "/api/v1/agent/chat"),
]


@pytest.fixture(scope="module")
def app() -> FastAPI:
    """Minimal app exposing only the v1 router, backed by in-memory SQLite."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    application = FastAPI()
    application.include_router(api_router)
    application.dependency_overrides[get_db] = override_get_db
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def as_role(app: FastAPI, role: UserRole) -> None:
    """Authenticate the next requests as a user with the given role."""
    user = User(id=1, username=role.value, email=f"{role.value}@aab.edu.al", role=role, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user


@pytest.mark.parametrize("method,path", PROTECTED_REQUESTS)
def test_anonymous_is_rejected(client: TestClient, method: str, path: str) -> None:
    response = getattr(client, method)(path)
    assert response.status_code == 401


@pytest.mark.parametrize("method,path", PROTECTED_REQUESTS)
def test_invalid_token_is_rejected(client: TestClient, method: str, path: str) -> None:
    response = getattr(client, method)(path, headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


def test_expired_token_is_rejected(client: TestClient) -> None:
    expired = jwt.encode(
        {"sub": "1", "role": "admin", "exp": datetime.utcnow() - timedelta(hours=1)},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    response = client.get("/api/v1/documents/", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401


def test_student_cannot_list_documents(app: FastAPI, client: TestClient) -> None:
    as_role(app, UserRole.student)
    assert client.get("/api/v1/documents/").status_code == 403


@pytest.mark.parametrize("role", [UserRole.professor, UserRole.admin])
def test_professor_and_admin_can_list_documents(app: FastAPI, client: TestClient, role: UserRole) -> None:
    as_role(app, role)
    response = client.get("/api/v1/documents/")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("role", [UserRole.student, UserRole.professor, UserRole.admin])
def test_authenticated_users_reach_the_endpoint_logic(app: FastAPI, client: TestClient, role: UserRole) -> None:
    """A 404 for a missing resource proves the request passed authentication."""
    as_role(app, role)
    assert client.get("/api/v1/documents/999").status_code == 404
    assert client.get("/api/v1/checks/999").status_code == 404
    assert client.get("/api/v1/checks/999/report").status_code == 404
    assert client.post("/api/v1/checks/run", json={"document_id": 999}).status_code == 404
    assert client.post("/api/v1/agent/chat", json={"message": "test", "document_id": 999}).status_code == 404
