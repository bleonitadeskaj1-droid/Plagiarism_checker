"""Email domain rules for student and professor portals."""

from __future__ import annotations

STUDENT_EMAIL_SUFFIX = "@universitetiaab.com"

# Domain-i zyrtar i email-it institucional të profesorëve (exact match, pa substring).
# I dokumentuar në README dhe i përdorur si placeholder në ballina.html / profesor.html.
PROFESSOR_EMAIL_DOMAINS = frozenset({"aab-edu.net"})

# Domain legacy i llogarive ekzistuese të profesorëve (p.sh. emri@aab-edu).
# Pranohet VETËM për llogari që ekzistojnë tashmë — kyçje dhe sinkronizim roli —
# kurrë për regjistrime të reja apo krijim llogarie nga admini.
LEGACY_PROFESSOR_EMAIL_DOMAINS = frozenset({"aab-edu"})

STUDENT_EMAIL_MESSAGE = (
    "Studentët mund të kyçen dhe regjistrohen vetëm me email që përfundon me "
    "@universitetiaab.com"
)

PROFESSOR_EMAIL_MESSAGE = (
    "Profesorët mund të kyçen dhe regjistrohen vetëm me email institucional të AAB-së "
    "me domain @aab-edu.net (p.sh. emri@aab-edu.net)"
)

INVALID_REGISTER_EMAIL_MESSAGE = (
    "Email i pavlefshëm. Studentët regjistrohen me @universitetiaab.com, "
    "profesorët me @aab-edu.net."
)


def _normalize(email: str) -> str:
    return (email or "").strip().lower()


def email_domain(email: str) -> str:
    """Domain-i i vetëm i email-it, ose '' nëse adresa nuk ka formë të vlefshme."""
    normalized = _normalize(email)
    if normalized.count("@") != 1:
        return ""
    local, domain = normalized.split("@", 1)
    if not local or not domain or any(ch.isspace() for ch in normalized):
        return ""
    return domain


def is_student_email(email: str) -> bool:
    return _normalize(email).endswith(STUDENT_EMAIL_SUFFIX)


def is_professor_register_email(email: str) -> bool:
    """Exact match kundrejt domain-it zyrtar — rregulli për llogari të reja."""
    return email_domain(email) in PROFESSOR_EMAIL_DOMAINS


def is_professor_email(email: str) -> bool:
    """Exact match kundrejt domain-it zyrtar ose atij legacy — për llogari ekzistuese."""
    domain = email_domain(email)
    return domain in PROFESSOR_EMAIL_DOMAINS or domain in LEGACY_PROFESSOR_EMAIL_DOMAINS


def assert_student_email(email: str) -> None:
    from fastapi import HTTPException

    if not is_student_email(email):
        raise HTTPException(status_code=400, detail=STUDENT_EMAIL_MESSAGE)


def assert_professor_email(email: str) -> None:
    """Validim për llogari të reja profesori (regjistrim ose krijim nga admini)."""
    from fastapi import HTTPException

    if not is_professor_register_email(email):
        raise HTTPException(status_code=400, detail=PROFESSOR_EMAIL_MESSAGE)


def resolve_register_role(email: str):
    """Pick student/professor role from email domain — reject everything else."""
    from fastapi import HTTPException
    from models import UserRole

    if is_student_email(email):
        return UserRole.student
    if is_professor_register_email(email):
        return UserRole.professor
    raise HTTPException(status_code=400, detail=INVALID_REGISTER_EMAIL_MESSAGE)


def role_for_registration(email: str, register_as: str):
    """Resolve role from explicit register_as + email domain (must match)."""
    from fastapi import HTTPException
    from models import UserRole

    if register_as == "student":
        assert_student_email(email)
        return UserRole.student
    if register_as == "professor":
        assert_professor_email(email)
        return UserRole.professor
    raise HTTPException(status_code=400, detail=INVALID_REGISTER_EMAIL_MESSAGE)
