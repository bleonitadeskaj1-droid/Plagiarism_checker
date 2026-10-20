"""Vendos 'Shkenca Kompjuterike' për të gjithë profesorët ekzistues."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from database import SessionLocal, engine
from departments import PROFESSOR_DEPARTMENT, backfill_professor_departments


def main() -> None:
    result = backfill_professor_departments(engine)
    print(f"Backfill u krye: {result}")

    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT u.username, u.full_name, u.department AS user_dept, "
                "p.department AS prof_dept "
                "FROM users u "
                "LEFT JOIN professors p ON p.user_id = u.id "
                "WHERE u.role = 'professor' "
                "ORDER BY u.username"
            )
        ).fetchall()
        print(f"\nProfesorë ({len(rows)}) — departamenti: {PROFESSOR_DEPARTMENT}")
        for row in rows:
            m = row._mapping
            print(f"  • {m['username']} ({m['full_name']}) — user: {m['user_dept']!r}, prof: {m['prof_dept']!r}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
