"""Departamentet — profesorë (Shkenca Kompjuterike) vs drejtime studentësh."""

from __future__ import annotations

from typing import Iterable, Optional

PROFESSOR_DEPARTMENT = "Shkenca Kompjuterike"
PROFESSOR_DEPARTMENTS: list[str] = [PROFESSOR_DEPARTMENT]

STUDENT_SPECIALIZATIONS: list[str] = [
    "Programim",
    "Programim dhe AI",
    "Programim dhe Multimedia",
    "Programim i Lojërave Kompjuterike",
    "Siguri Kibernetike",
    "Specializim në Inxhinieri Softuerike",
]

# Alias për API / statistika (drejtimet e studentëve)
DEPARTMENTS: list[str] = STUDENT_SPECIALIZATIONS

_STUDENT_ALIASES: dict[str, str] = {
    "informatikë": "Programim",
    "informatike": "Programim",
    "informatik": "Programim",
    "shkenca kompjuterike": "Programim",
    "cs": "Programim",
    "programim i lojerave kompjuterike": "Programim i Lojërave Kompjuterike",
    "specializim ne inxhinieri softuerike": "Specializim në Inxhinieri Softuerike",
    "specializim në inxhinierinë softuerike": "Specializim në Inxhinieri Softuerike",
}


def _match_key(value: str) -> str:
    value = value.strip().lower().replace("ë", "e").replace("ç", "c")
    return "".join(ch for ch in value if ch.isalnum())


def normalize_professor_department(value: Optional[str]) -> Optional[str]:
    """Profesorët janë në departamentin Shkenca Kompjuterike."""
    if value is None or not str(value).strip():
        return None
    return PROFESSOR_DEPARTMENT


def is_valid_professor_department(value: Optional[str]) -> bool:
    if not value or not str(value).strip():
        return False
    normalized = normalize_professor_department(value)
    return normalized == PROFESSOR_DEPARTMENT


def normalize_student_specialization(value: Optional[str]) -> Optional[str]:
    """Drejtimi / specializimi i studentit."""
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip()
    for spec in STUDENT_SPECIALIZATIONS:
        if spec.lower() == raw.lower():
            return spec
    alias = _STUDENT_ALIASES.get(raw.lower())
    if alias:
        return alias
    key = _match_key(raw)
    for spec in STUDENT_SPECIALIZATIONS:
        if key == _match_key(spec):
            return spec
    return raw


def is_valid_student_specialization(value: Optional[str]) -> bool:
    if not value or not str(value).strip():
        return False
    return normalize_student_specialization(value) in STUDENT_SPECIALIZATIONS


def is_valid_department(value: Optional[str]) -> bool:
    """Alias — drejtim studentor (për compat)."""
    return is_valid_student_specialization(value)


def normalize_department(value: Optional[str]) -> Optional[str]:
    """Alias — drejtim studentor (për compat / statistika)."""
    return normalize_student_specialization(value)


def departments_equal(a: Optional[str], b: Optional[str]) -> bool:
    left = normalize_student_specialization(a)
    right = normalize_student_specialization(b)
    if not left or not right:
        return False
    return left == right


def department_from_professor(professor) -> Optional[str]:
    """Departamenti i profesorit — gjithmonë Shkenca Kompjuterike."""
    if professor is None:
        return None
    return PROFESSOR_DEPARTMENT


def thesis_specialization(thesis) -> Optional[str]:
    """Drejtimi i temës — nga studenti (thesis.department)."""
    if thesis is None:
        return None
    return normalize_student_specialization(getattr(thesis, "department", None))


def build_department_stats(rows: Iterable[tuple[Optional[str], int]]) -> list[dict]:
    counts = {spec: 0 for spec in STUDENT_SPECIALIZATIONS}
    for department, count in rows:
        canonical = normalize_student_specialization(department)
        if canonical in counts:
            counts[canonical] += int(count or 0)
    max_count = max(counts.values()) or 1
    return [
        {
            "name": name,
            "count": counts[name],
            "percent": round((counts[name] / max_count) * 100) if max_count else 0,
        }
        for name in STUDENT_SPECIALIZATIONS
    ]


def backfill_professor_departments(bind) -> dict:
    """Vendos Shkenca Kompjuterike për të gjithë profesorët ekzistues."""
    from sqlalchemy import text

    users_updated = 0
    profiles_updated = 0
    with bind.begin() as connection:
        users_result = connection.execute(
            text(
                "UPDATE users SET department = :dept "
                "WHERE role = 'professor' "
                "AND (department IS NULL OR TRIM(department) = '' OR department <> :dept)"
            ),
            {"dept": PROFESSOR_DEPARTMENT},
        )
        users_updated = users_result.rowcount or 0

        profiles_result = connection.execute(
            text(
                "UPDATE professors SET department = :dept "
                "WHERE department IS NULL OR TRIM(department) = '' OR department <> :dept"
            ),
            {"dept": PROFESSOR_DEPARTMENT},
        )
        profiles_updated = profiles_result.rowcount or 0

    return {
        "users_updated": users_updated,
        "profiles_updated": profiles_updated,
        "department": PROFESSOR_DEPARTMENT,
    }
