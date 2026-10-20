"""One-time copy of legacy MySQL data into local SQLite (password hashes preserved)."""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SQLITE_PATH = PROJECT_ROOT / "data" / "plagiarism_checker.db"

TABLES = [
    "users",
    "students",
    "professors",
    "theses",
    "uploaded_files",
    "reviews",
    "notifications",
    "confidential_documents",
    "plagiarism_results",
    "plagiarism_matches",
    "system_settings",
    "password_reset_tokens",
    "audit_logs",
]


def mysql_connect():
    return pymysql.connect(
        host="127.0.0.1",
        port=3306,
        user="root",
        password="",
        database="plagiarism_checker",
        charset="utf8mb4",
        connect_timeout=8,
        cursorclass=pymysql.cursors.Cursor,
    )


def copy_table(mysql_cur, sqlite_cur, table: str) -> int:
    mysql_cur.execute(f"SHOW COLUMNS FROM `{table}`")
    mysql_cols = [row[0] for row in mysql_cur.fetchall()]
    sqlite_cur.execute(f"PRAGMA table_info({table})")
    sqlite_cols = [row[1] for row in sqlite_cur.fetchall()]
    cols = [c for c in mysql_cols if c in sqlite_cols]
    if not cols:
        print(f"  skip {table}: no shared columns")
        return 0
    col_sql = ", ".join(f"`{c}`" for c in cols)
    mysql_cur.execute(f"SELECT {col_sql} FROM `{table}`")
    rows = mysql_cur.fetchall()
    sqlite_cur.execute(f"DELETE FROM {table}")
    if not rows:
        print(f"  {table}: 0 rows")
        return 0
    placeholders = ", ".join("?" for _ in cols)
    insert_cols = ", ".join(cols)

    def _adapt(value):
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, datetime):
            return value.isoformat(sep=" ", timespec="seconds")
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, set):
            return ",".join(str(v) for v in value)
        return value

    adapted = [tuple(_adapt(v) for v in row) for row in rows]
    sqlite_cur.executemany(
        f"INSERT INTO {table} ({insert_cols}) VALUES ({placeholders})",
        adapted,
    )
    print(f"  {table}: {len(rows)} rows")
    return len(rows)


def main() -> int:
    if not SQLITE_PATH.exists():
        print("SQLite file missing:", SQLITE_PATH)
        return 1
    try:
        mysql = mysql_connect()
    except Exception as exc:
        print("MySQL unavailable:", exc)
        return 2

    sqlite = sqlite3.connect(SQLITE_PATH)
    sqlite.execute("PRAGMA foreign_keys = OFF")
    mysql_cur = mysql.cursor()
    sqlite_cur = sqlite.cursor()
    print("Copying MySQL -> SQLite")
    total = 0
    try:
        for table in TABLES:
            mysql_cur.execute("SHOW TABLES LIKE %s", (table,))
            if not mysql_cur.fetchone():
                print(f"  skip {table}: not in MySQL")
                continue
            total += copy_table(mysql_cur, sqlite_cur, table)
            sqlite.commit()
        sqlite.commit()
    finally:
        mysql_cur.close()
        mysql.close()
        sqlite.close()
    print(f"Done. Copied {total} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
