#!/usr/bin/env python3
"""Safe database upgrade — additive only, preserves all existing rows."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import inspect, text

from database import check_db_connection, engine, ensure_schema_extensions, init_legacy_schema


def print_table_counts() -> None:
    insp = inspect(engine)
    print("\nRow counts (no data deleted):")
    for table in sorted(insp.get_table_names()):
        with engine.connect() as conn:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        print(f"  {table}: {count}")


def main() -> int:
    print("=== Safe DB upgrade (additive only) ===")
    status = check_db_connection()
    if not status.get("ok"):
        print("ERROR: Database unavailable:", status.get("error"))
        return 1

    print(f"Connected: {status.get('url')} ({status.get('driver')})")
    print_table_counts()

    print("\nApplying schema extensions...")
    ensure_schema_extensions()
    init_legacy_schema()

    print("Done. All existing data preserved.")
    print_table_counts()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
