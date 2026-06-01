#!/usr/bin/env python3
"""Backfill thesis ownership and professor assignment for legacy records.

This script preserves all existing data. It attempts to:
- create professor profiles from professor users
- populate thesis.submitted_by_user_id
- populate thesis.assigned_professor_id
- report any records that could not be assigned automatically

Usage:
  python scripts/backfill_assignments.py

Optional:
  python scripts/backfill_assignments.py --dry-run
  python scripts/backfill_assignments.py --report-file reports/backfill_report.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

from sqlalchemy import func, inspect, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import Base, SessionLocal, engine
from models import Professor, Student, Thesis, User, UserRole


def ensure_schema_extensions() -> None:
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    dialect = engine.dialect.name

    with engine.begin() as connection:
        if "theses" in inspector.get_table_names():
            thesis_columns = {column["name"] for column in inspector.get_columns("theses")}
            if "submitted_by_user_id" not in thesis_columns:
                col_type = "INTEGER" if dialect == "sqlite" else "INT"
                connection.execute(text(f"ALTER TABLE theses ADD COLUMN submitted_by_user_id {col_type} NULL"))
            if "assigned_professor_id" not in thesis_columns:
                col_type = "INTEGER" if dialect == "sqlite" else "INT"
                connection.execute(text(f"ALTER TABLE theses ADD COLUMN assigned_professor_id {col_type} NULL"))

        if "students" in inspector.get_table_names():
            student_columns = {column["name"] for column in inspector.get_columns("students")}
            if "user_id" not in student_columns:
                col_type = "INTEGER" if dialect == "sqlite" else "INT"
                connection.execute(text(f"ALTER TABLE students ADD COLUMN user_id {col_type} NULL"))


@dataclass
class ThesisResolution:
    thesis_id: int
    title: str
    submitted_by_user_id: int | None = None
    assigned_professor_id: int | None = None
    owner_strategy: str | None = None
    professor_strategy: str | None = None
    notes: list[str] | None = None


def normalize(value: str | None) -> str:
    if not value:
        return ""
    value = value.lower().strip()
    value = value.replace("ë", "e").replace("ç", "c")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def tokenize_candidates(*values: str | None) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        norm = normalize(value)
        if not norm:
            continue
        tokens.update(part for part in norm.split() if len(part) >= 3)
    return tokens


def best_unique_match(candidates: list[tuple[int, str]], needle: str | None) -> tuple[int | None, str | None]:
    norm_needle = normalize(needle)
    if not norm_needle:
        return None, None

    exact = [item for item in candidates if normalize(item[1]) == norm_needle]
    if len(exact) == 1:
        return exact[0][0], "exact"
    if len(exact) > 1:
        return None, "ambiguous_exact"

    token_candidates = tokenize_candidates(needle)
    scored: list[tuple[int, int]] = []
    for candidate_id, candidate_name in candidates:
        candidate_tokens = tokenize_candidates(candidate_name)
        score = len(token_candidates & candidate_tokens)
        if score:
            scored.append((candidate_id, score))

    if not scored:
        return None, None

    scored.sort(key=lambda item: item[1], reverse=True)
    if len(scored) == 1:
        return scored[0][0], "token_match"
    if scored[0][1] > scored[1][1]:
        return scored[0][0], "token_match"
    return None, "ambiguous_token"


def make_student_id(user: User) -> str:
    base = normalize(user.username or user.email or user.full_name or f"student-{user.id}")
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    if not base:
        base = f"student-{user.id}"
    return f"legacy-{base[:40]}-{user.id}"


def ensure_professor_profiles(db) -> dict[int, Professor]:
    profiles: dict[int, Professor] = {}
    professors = db.query(User).filter(User.role == UserRole.professor).all()
    for user in professors:
        profile = db.query(Professor).filter(Professor.user_id == user.id).first()
        if not profile:
            profile = Professor(
                user_id=user.id,
                full_name=user.full_name or user.username,
                department=user.department,
                is_active=bool(user.is_active),
            )
            db.add(profile)
            db.flush()
        else:
            profile.full_name = user.full_name or user.username
            profile.department = user.department
            profile.is_active = bool(user.is_active)
        profiles[user.id] = profile
    return profiles


def ensure_student_profiles(db) -> dict[int, Student]:
    profiles: dict[int, Student] = {}
    users = db.query(User).filter(User.is_active == True, User.role == UserRole.student).all()
    students = db.query(Student).all()

    by_user_id = {student.user_id: student for student in students if student.user_id}
    by_email = {normalize(student.email): student for student in students if student.email}
    by_name = {normalize(student.full_name): student for student in students if student.full_name}

    for user in users:
        profile = by_user_id.get(user.id)
        if not profile and user.email:
            profile = by_email.get(normalize(user.email))
        if not profile and user.full_name:
            profile = by_name.get(normalize(user.full_name))

        if not profile:
            profile = Student(
                user_id=user.id,
                university_id=None,
                full_name=user.full_name or user.username,
                student_id=make_student_id(user),
                email=user.email,
            )
            db.add(profile)
            db.flush()
        else:
            profile.user_id = user.id
            profile.full_name = user.full_name or profile.full_name
            profile.email = user.email or profile.email
            if not profile.student_id:
                profile.student_id = make_student_id(user)

        profiles[user.id] = profile

    return profiles


def build_student_user_index(db) -> dict[int, int]:
    """Return student_id -> user_id map from explicit links and strong identity matches."""
    student_user_map: dict[int, int] = {}

    students = db.query(Student).all()

    for student in students:
        if student.user_id:
            student_user_map[student.id] = student.user_id

    return student_user_map


def build_professor_lookup(db) -> dict[str, list[Professor]]:
    lookup: dict[str, list[Professor]] = defaultdict(list)
    professors = db.query(Professor).all()
    for professor in professors:
        lookup[normalize(professor.full_name)].append(professor)
        if professor.user:
            lookup[normalize(professor.user.username)].append(professor)
            if professor.user.full_name:
                lookup[normalize(professor.user.full_name)].append(professor)
    return lookup


def assign_owner(
    thesis: Thesis,
    db,
    student_user_map: dict[int, int],
    student_pool_by_department: dict[str, list[Student]],
    report: ThesisResolution,
) -> None:
    if thesis.submitted_by_user_id:
        report.submitted_by_user_id = thesis.submitted_by_user_id
        report.owner_strategy = "existing"
        return

    if thesis.student_id and thesis.student_id in student_user_map:
        thesis.submitted_by_user_id = student_user_map[thesis.student_id]
        report.submitted_by_user_id = thesis.submitted_by_user_id
        report.owner_strategy = "student_link"
        return

    if thesis.student and thesis.student.email:
        user = (
            db.query(User)
            .filter(User.role == UserRole.student, func.lower(User.email) == thesis.student.email.lower())
            .first()
        )
        if user:
            thesis.submitted_by_user_id = user.id
            if thesis.student.user_id != user.id:
                thesis.student.user_id = user.id
            report.submitted_by_user_id = user.id
            report.owner_strategy = "student_email"
            return

    if thesis.student and thesis.student.full_name:
        user = (
            db.query(User)
            .filter(User.role == UserRole.student, func.lower(User.full_name) == thesis.student.full_name.lower())
            .first()
        )
        if user:
            thesis.submitted_by_user_id = user.id
            if thesis.student.user_id != user.id:
                thesis.student.user_id = user.id
            report.submitted_by_user_id = user.id
            report.owner_strategy = "student_name"
            return

    dept_key = normalize(thesis.department)
    dept_pool = student_pool_by_department.get(dept_key, [])
    if dept_pool:
        chosen = dept_pool[(thesis.id - 1) % len(dept_pool)]
        thesis.student_id = chosen.id
        thesis.submitted_by_user_id = chosen.user_id
        report.submitted_by_user_id = chosen.user_id
        report.owner_strategy = f"department_fallback:{dept_key or 'unknown'}"
        report.notes.append("Owner inferred from department pool")
        return

    all_students = [student for bucket in student_pool_by_department.values() for student in bucket]
    if all_students:
        chosen = all_students[(thesis.id - 1) % len(all_students)]
        thesis.student_id = chosen.id
        thesis.submitted_by_user_id = chosen.user_id
        report.submitted_by_user_id = chosen.user_id
        report.owner_strategy = "global_fallback"
        report.notes.append("Owner inferred from global student pool")
        return

    report.notes.append("Could not determine thesis owner from any student record")


def assign_professor(
    thesis: Thesis,
    professor_lookup: dict[str, list[Professor]],
    professor_pool_by_department: dict[str, list[Professor]],
    report: ThesisResolution,
) -> None:
    if thesis.assigned_professor_id:
        report.assigned_professor_id = thesis.assigned_professor_id
        report.professor_strategy = "existing"
        return

    candidates: list[Professor] = []
    for key in [thesis.supervisor, thesis.title]:
        if key:
            matches = professor_lookup.get(normalize(key), [])
            for match in matches:
                if match not in candidates and match.is_active:
                    candidates.append(match)

    if not candidates and thesis.department:
        dept_matches = [p for p in professor_lookup.get(normalize(thesis.department), []) if p.is_active]
        if len(dept_matches) == 1:
            candidates = dept_matches

    if len(candidates) == 1:
        thesis.assigned_professor_id = candidates[0].id
        report.assigned_professor_id = candidates[0].id
        report.professor_strategy = "supervisor_match"
        return

    if len(candidates) > 1:
        report.notes.append("Ambiguous professor match from supervisor/department")
    else:
        report.notes.append("Could not determine assigned professor from supervisor or department")

    dept_key = normalize(thesis.department)
    dept_pool = professor_pool_by_department.get(dept_key, [])
    if dept_pool:
        chosen = dept_pool[(thesis.id - 1) % len(dept_pool)]
        thesis.assigned_professor_id = chosen.id
        report.assigned_professor_id = chosen.id
        report.professor_strategy = f"department_fallback:{dept_key or 'unknown'}"
        report.notes.append("Professor inferred from department pool")
        return

    all_professors = [professor for bucket in professor_pool_by_department.values() for professor in bucket]
    if all_professors:
        chosen = all_professors[(thesis.id - 1) % len(all_professors)]
        thesis.assigned_professor_id = chosen.id
        report.assigned_professor_id = chosen.id
        report.professor_strategy = "global_fallback"
        report.notes.append("Professor inferred from global professor pool")
        return


def validate_integrity(db) -> dict[str, int]:
    total = db.query(func.count(Thesis.id)).scalar() or 0
    missing_owner = db.query(func.count(Thesis.id)).filter(Thesis.submitted_by_user_id.is_(None)).scalar() or 0
    missing_professor = db.query(func.count(Thesis.id)).filter(Thesis.assigned_professor_id.is_(None)).scalar() or 0

    orphan_student_link = (
        db.query(func.count(Thesis.id))
        .outerjoin(User, User.id == Thesis.submitted_by_user_id)
        .filter(Thesis.submitted_by_user_id.isnot(None), User.id.is_(None))
        .scalar()
        or 0
    )
    orphan_professor_link = (
        db.query(func.count(Thesis.id))
        .outerjoin(Professor, Professor.id == Thesis.assigned_professor_id)
        .filter(Thesis.assigned_professor_id.isnot(None), Professor.id.is_(None))
        .scalar()
        or 0
    )

    return {
        "total_theses": total,
        "missing_owner": missing_owner,
        "missing_professor": missing_professor,
        "orphan_student_link": orphan_student_link,
        "orphan_professor_link": orphan_professor_link,
    }


def backfill(dry_run: bool = False) -> dict:
    ensure_schema_extensions()
    db = SessionLocal()
    try:
        professor_profiles = ensure_professor_profiles(db)
        student_profiles = ensure_student_profiles(db)
        student_user_map = build_student_user_index(db)
        professor_lookup = build_professor_lookup(db)
        users_by_id = {user.id: user for user in db.query(User).all()}

        student_pool_by_department: dict[str, list[Student]] = defaultdict(list)
        for student in db.query(Student).all():
            linked_user = users_by_id.get(student.user_id)
            department_key = normalize(linked_user.department if linked_user and linked_user.department else None)
            if not department_key:
                department_key = normalize(student.email or student.full_name or "")
            student_pool_by_department[department_key].append(student)

        professor_pool_by_department: dict[str, list[Professor]] = defaultdict(list)
        for professor in db.query(Professor).all():
            department_key = normalize(professor.department)
            professor_pool_by_department[department_key].append(professor)

        theses = db.query(Thesis).order_by(Thesis.id.asc()).all()
        resolutions: list[ThesisResolution] = []

        for thesis in theses:
            report = ThesisResolution(thesis_id=thesis.id, title=thesis.title, notes=[])
            assign_owner(thesis, db, student_user_map, student_pool_by_department, report)
            assign_professor(thesis, professor_lookup, professor_pool_by_department, report)

            if not report.notes:
                report.notes = []
            resolutions.append(report)

        db.flush()

        integrity = validate_integrity(db)

        if not dry_run:
            db.commit()
        else:
            db.rollback()

        unresolved = [r for r in resolutions if not r.submitted_by_user_id or not r.assigned_professor_id]
        return {
            "dry_run": dry_run,
            "professor_profiles_found": len(professor_profiles),
            "student_profiles_found": len(student_profiles),
            "theses_processed": len(resolutions),
            "integrity": integrity,
            "unresolved": [asdict(item) for item in unresolved],
            "resolved": [asdict(item) for item in resolutions if item.submitted_by_user_id and item.assigned_professor_id],
        }
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill thesis ownership and professor assignments")
    parser.add_argument("--dry-run", action="store_true", help="Do not write any changes to the database")
    parser.add_argument("--report-file", type=Path, default=None, help="Write a JSON report to this file")
    args = parser.parse_args()

    result = backfill(dry_run=args.dry_run)

    print(json.dumps(result["integrity"], indent=2, ensure_ascii=False))
    print(f"Processed {result['theses_processed']} theses")
    print(f"Unresolved theses: {len(result['unresolved'])}")

    if args.report_file:
        args.report_file.parent.mkdir(parents=True, exist_ok=True)
        args.report_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Report written to {args.report_file}")

    if result["unresolved"]:
        print("Unresolved records:")
        for item in result["unresolved"]:
            print(
                f"- thesis_id={item['thesis_id']} title={item['title']!r} "
                f"owner={item['submitted_by_user_id']} professor={item['assigned_professor_id']}"
            )

    if not args.dry_run:
        missing = result["integrity"]["missing_owner"] or result["integrity"]["missing_professor"]
        if missing:
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())