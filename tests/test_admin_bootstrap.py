"""Initial admin bootstrap: production requires a strong ADMIN_PASSWORD, existing admins are untouched."""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import legacy_bootstrap
from app.legacy_bootstrap import AdminBootstrapError, bootstrap_default_users
from auth import hash_password, verify_password
from database import Base
from models import User, UserRole

# Test-only value, never a real credential.
TEST_PASSWORD = "Test-Bootstrap-Pass-2026"


@pytest.fixture
def session_factory(monkeypatch: pytest.MonkeyPatch):
    """Point the bootstrap at a throwaway in-memory database."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(legacy_bootstrap, "SessionLocal", factory)
    return factory


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(legacy_bootstrap.settings, "env", "production")


@pytest.fixture
def development(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(legacy_bootstrap.settings, "env", "development")


def get_admin(session_factory) -> User | None:
    db = session_factory()
    try:
        return db.query(User).filter(User.username == "admin").first()
    finally:
        db.close()


def seed_admin(session_factory, password: str) -> str:
    db = session_factory()
    try:
        admin = User(
            username="admin",
            email="admin@aab.edu.al",
            password_hash=hash_password(password),
            full_name="Administrator AAB",
            role=UserRole.admin,
            is_active=True,
        )
        db.add(admin)
        db.commit()
        return admin.password_hash
    finally:
        db.close()


def test_production_without_admin_password_fails_safely(session_factory, production, monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    with pytest.raises(AdminBootstrapError):
        bootstrap_default_users()
    assert get_admin(session_factory) is None


@pytest.mark.parametrize("weak", ["Admin123", "admin", "short", "password"])
def test_production_rejects_weak_passwords(session_factory, production, monkeypatch, weak: str) -> None:
    monkeypatch.setenv("ADMIN_PASSWORD", weak)
    with pytest.raises(AdminBootstrapError):
        bootstrap_default_users()
    assert get_admin(session_factory) is None


def test_production_creates_admin_with_bcrypt_hash(session_factory, production, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_PASSWORD)
    bootstrap_default_users()

    admin = get_admin(session_factory)
    assert admin is not None
    assert admin.role == UserRole.admin
    assert admin.password_hash.startswith("$2b$")
    assert TEST_PASSWORD not in admin.password_hash
    assert verify_password(TEST_PASSWORD, admin.password_hash)


def test_existing_admin_password_is_never_reset(session_factory, production, monkeypatch) -> None:
    original_hash = seed_admin(session_factory, TEST_PASSWORD)
    monkeypatch.setenv("ADMIN_PASSWORD", "A-Completely-Different-Pass-2026")

    bootstrap_default_users()

    admin = get_admin(session_factory)
    assert admin.password_hash == original_hash
    assert verify_password(TEST_PASSWORD, admin.password_hash)


def test_existing_admin_boots_without_admin_password(session_factory, production, monkeypatch) -> None:
    """A provisioned production instance must not refuse to start."""
    original_hash = seed_admin(session_factory, TEST_PASSWORD)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)

    bootstrap_default_users()

    assert get_admin(session_factory).password_hash == original_hash


def test_development_falls_back_without_admin_password(session_factory, development, monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    bootstrap_default_users()

    admin = get_admin(session_factory)
    assert admin is not None
    assert admin.password_hash.startswith("$2b$")


def test_development_uses_admin_password_when_set(session_factory, development, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_PASSWORD)
    bootstrap_default_users()

    assert verify_password(TEST_PASSWORD, get_admin(session_factory).password_hash)


def test_development_fallback_is_not_accepted_in_production(session_factory, production, monkeypatch) -> None:
    """The development default must never become a production credential."""
    monkeypatch.setenv("ADMIN_PASSWORD", legacy_bootstrap._DEV_ADMIN_PASSWORD)
    with pytest.raises(AdminBootstrapError):
        bootstrap_default_users()
    assert get_admin(session_factory) is None


def test_password_is_absent_from_logs_and_errors(session_factory, production, monkeypatch, caplog) -> None:
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_PASSWORD)
    with caplog.at_level(logging.DEBUG):
        bootstrap_default_users()
    assert TEST_PASSWORD not in caplog.text

    weak = "Admin123"
    monkeypatch.setenv("ADMIN_PASSWORD", weak)
    db = session_factory()
    try:
        db.query(User).delete()
        db.commit()
    finally:
        db.close()

    caplog.clear()
    with caplog.at_level(logging.DEBUG), pytest.raises(AdminBootstrapError) as exc:
        bootstrap_default_users()
    assert weak not in str(exc.value)
    assert weak not in caplog.text
