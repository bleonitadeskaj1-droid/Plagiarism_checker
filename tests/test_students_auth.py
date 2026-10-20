"""Authentication and role checks for the /api/students endpoints."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import get_current_user
from database import Base, get_db
from models import Student, User, UserRole
from routers import students

STUDENT_USER_ID = 10
OTHER_USER_ID = 11

PROTECTED_REQUESTS = [
    ("post", "/api/students/"),
    ("get", "/api/students/"),
    ("get", "/api/students/1"),
    ("put", "/api/students/1"),
    ("delete", "/api/students/1"),
]

ADMIN_ONLY_REQUESTS = [
    ("post", "/api/students/"),
    ("put", "/api/students/1"),
    ("delete", "/api/students/1"),
]


@pytest.fixture(scope="module")
def app() -> FastAPI:
    """Minimal app exposing only the students router, backed by in-memory SQLite."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    seed = TestingSession()
    seed.add_all(
        [
            Student(id=1, user_id=STUDENT_USER_ID, full_name="Studenti Im", student_id="STU-10"),
            Student(id=2, user_id=OTHER_USER_ID, full_name="Studenti Tjeter", student_id="STU-11"),
        ]
    )
    seed.commit()
    seed.close()

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    application = FastAPI()
    application.include_router(students.router, prefix="/api/students")
    application.dependency_overrides[get_db] = override_get_db
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def as_user(app: FastAPI, role: UserRole, user_id: int = 1) -> None:
    """Authenticate the next requests as a user with the given role."""
    user = User(
        id=user_id,
        username=role.value,
        email=f"{role.value}@aab.edu.al",
        role=role,
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: user


@pytest.mark.parametrize("method,path", PROTECTED_REQUESTS)
def test_anonymous_is_rejected(client: TestClient, method: str, path: str) -> None:
    assert getattr(client, method)(path).status_code == 401


@pytest.mark.parametrize("method,path", PROTECTED_REQUESTS)
def test_invalid_token_is_rejected(client: TestClient, method: str, path: str) -> None:
    response = getattr(client, method)(path, headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


@pytest.mark.parametrize("role", [UserRole.student, UserRole.professor])
@pytest.mark.parametrize("method,path", ADMIN_ONLY_REQUESTS)
def test_only_admin_can_mutate(app: FastAPI, client: TestClient, role: UserRole, method: str, path: str) -> None:
    as_user(app, role)
    assert client.request(method.upper(), path, json={}).status_code == 403


def test_student_cannot_list_students(app: FastAPI, client: TestClient) -> None:
    as_user(app, UserRole.student, user_id=STUDENT_USER_ID)
    assert client.get("/api/students/").status_code == 403


@pytest.mark.parametrize("role", [UserRole.professor, UserRole.admin])
def test_professor_and_admin_can_list_students(app: FastAPI, client: TestClient, role: UserRole) -> None:
    as_user(app, role)
    response = client.get("/api/students/")
    assert response.status_code == 200
    assert len(response.json()) >= 2


def test_student_reads_only_own_profile(app: FastAPI, client: TestClient) -> None:
    as_user(app, UserRole.student, user_id=STUDENT_USER_ID)
    own = client.get("/api/students/1")
    assert own.status_code == 200
    assert own.json()["student_id"] == "STU-10"
    assert client.get("/api/students/2").status_code == 403


@pytest.mark.parametrize("role", [UserRole.professor, UserRole.admin])
def test_professor_and_admin_read_any_profile(app: FastAPI, client: TestClient, role: UserRole) -> None:
    as_user(app, role)
    assert client.get("/api/students/1").status_code == 200
    assert client.get("/api/students/2").status_code == 200


def test_admin_can_still_create_update_and_delete(app: FastAPI, client: TestClient) -> None:
    as_user(app, UserRole.admin)
    created = client.post(
        "/api/students/",
        json={"full_name": "Krijuar nga Admini", "student_id": "STU-ADMIN", "email": "adm@aab.edu.al"},
    )
    assert created.status_code == 200
    new_id = created.json()["id"]

    assert client.put(f"/api/students/{new_id}", json={"full_name": "Emri i Ri"}).status_code == 200
    assert client.get(f"/api/students/{new_id}").json()["full_name"] == "Emri i Ri"
    assert client.delete(f"/api/students/{new_id}").status_code == 200
    assert client.get(f"/api/students/{new_id}").status_code == 404
