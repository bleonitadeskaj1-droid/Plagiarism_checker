"""Read-only inspect of local SQLite users (no passwords printed)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parent.parent / "data" / "plagiarism_checker.db"
con = sqlite3.connect(db)
cur = con.cursor()
print("tables:", [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1")])
print("users schema:")
for row in cur.execute("PRAGMA table_info(users)"):
    print(" ", row)
print("users:")
for row in cur.execute(
    "SELECT id, username, email, role, is_active, length(password_hash) FROM users"
):
    print(" ", row)
con.close()
