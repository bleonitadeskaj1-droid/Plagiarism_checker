"""P0 #4 — validimi i domain-it të email-it të profesorëve (exact match, jo substring)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from auth_email import (
    LEGACY_PROFESSOR_EMAIL_DOMAINS,
    PROFESSOR_EMAIL_DOMAINS,
    assert_professor_email,
    email_domain,
    is_professor_email,
    is_professor_register_email,
    is_student_email,
    role_for_registration,
)
from models import UserRole

VALID_REGISTER_EMAILS = [
    "emri@aab-edu.net",
    "Emri.Mbiemri@AAB-EDU.NET",
    "  emri@aab-edu.net  ",
]

# Domain-e që kalonin me substring matching ("aab-edu" in domain) dhe tani duhet të refuzohen.
MALICIOUS_EMAILS = [
    "attacker@evil-aab-edu.com",
    "attacker@aab-edu.net.evil.com",
    "attacker@aab-edu-fake.net",
    "attacker@myaab-edu.net",
    "attacker@aab-edu.com",
    "attacker@sub.aab-edu.net",
    "attacker@notaab-edu",
    "attacker@aab-edu.net.co",
    "attacker@xn--aab-edu.net",
    "attacker@aab-edu.nettt",
]


@pytest.mark.parametrize("email", VALID_REGISTER_EMAILS)
def test_official_domain_is_accepted_for_registration(email):
    assert is_professor_register_email(email) is True
    assert is_professor_email(email) is True


@pytest.mark.parametrize("email", MALICIOUS_EMAILS)
def test_lookalike_domains_are_rejected(email):
    assert is_professor_register_email(email) is False
    assert is_professor_email(email) is False


@pytest.mark.parametrize("email", MALICIOUS_EMAILS)
def test_lookalike_domains_raise_http_400(email):
    with pytest.raises(HTTPException) as exc:
        assert_professor_email(email)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("email", MALICIOUS_EMAILS)
def test_lookalike_domains_cannot_register_as_professor(email):
    with pytest.raises(HTTPException) as exc:
        role_for_registration(email, "professor")
    assert exc.value.status_code == 400


def test_legitimate_professor_registration_still_resolves_to_professor_role():
    assert role_for_registration("emri@aab-edu.net", "professor") == UserRole.professor


def test_legacy_domain_works_for_existing_accounts_but_not_for_registration():
    """Llogaritë ekzistuese (emri@aab-edu) kyçen; regjistrimet e reja refuzohen."""
    legacy = "enginmelekoglu@aab-edu"
    assert is_professor_email(legacy) is True
    assert is_professor_register_email(legacy) is False
    with pytest.raises(HTTPException):
        role_for_registration(legacy, "professor")


@pytest.mark.parametrize(
    "email",
    ["", "   ", "no-at-sign", "@aab-edu.net", "a@@aab-edu.net", "a@b@aab-edu.net", None],
)
def test_malformed_addresses_are_rejected(email):
    assert is_professor_email(email) is False
    assert is_professor_register_email(email) is False


def test_email_domain_extracts_last_domain_only():
    assert email_domain("a@aab-edu.net") == "aab-edu.net"
    assert email_domain("a@b@aab-edu.net") == ""
    assert email_domain("A@AAB-EDU.NET") == "aab-edu.net"


def test_student_domain_is_not_a_professor_domain():
    assert is_student_email("student@universitetiaab.com") is True
    assert is_professor_email("student@universitetiaab.com") is False
    assert is_professor_register_email("student@universitetiaab.com") is False


def test_student_registration_rejects_professor_domain():
    with pytest.raises(HTTPException) as exc:
        role_for_registration("emri@aab-edu.net", "student")
    assert exc.value.status_code == 400


def test_admin_domain_is_not_treated_as_professor():
    assert is_professor_email("admin@aab.edu.al") is False
    assert is_professor_register_email("admin@aab.edu.al") is False


def test_allowlists_contain_only_project_domains():
    """Asnjë domain i shpikur — vetëm ato që ekzistojnë në projekt."""
    assert PROFESSOR_EMAIL_DOMAINS == frozenset({"aab-edu.net"})
    assert LEGACY_PROFESSOR_EMAIL_DOMAINS == frozenset({"aab-edu"})


# ── Regjistrimi end-to-end përmes POST /api/auth/register ──────────────────────


@pytest.fixture
def register_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from database import Base, get_db
    from routers import auth_router

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
    application.include_router(auth_router.router, prefix="/api/auth")
    application.dependency_overrides[get_db] = override_get_db
    with TestClient(application) as client:
        yield client


def _payload(email: str, username: str) -> dict:
    return {
        "username": username,
        "email": email,
        "password": "Profesor-Test-2026",
        "full_name": "Profesor Test",
        "department": "Shkenca Kompjuterike",
        "register_as": "professor",
    }


def test_registration_accepts_official_professor_domain(register_client):
    response = register_client.post(
        "/api/auth/register", json=_payload("profesor.ri@aab-edu.net", "profesorri")
    )
    assert response.status_code == 200, response.text
    assert response.json()["user"]["role"] == "professor"


@pytest.mark.parametrize("email", MALICIOUS_EMAILS)
def test_registration_rejects_lookalike_domains(register_client, email):
    response = register_client.post("/api/auth/register", json=_payload(email, "attacker"))
    assert response.status_code == 400, response.text


def test_registration_rejects_legacy_domain_for_new_accounts(register_client):
    response = register_client.post(
        "/api/auth/register", json=_payload("i.ri@aab-edu", "iri")
    )
    assert response.status_code == 400, response.text
