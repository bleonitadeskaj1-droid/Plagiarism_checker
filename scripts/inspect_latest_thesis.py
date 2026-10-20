"""Inspect the latest thesis plagiarism result."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

env = ROOT / ".env"
if env.exists():
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from sqlalchemy import text
from database import SessionLocal


def main() -> None:
    db = SessionLocal()
    try:
        print("pr columns:", [r[0] for r in db.execute(text("SHOW COLUMNS FROM plagiarism_results")).fetchall()])
        rows = db.execute(
            text(
                """
                SELECT t.id, t.title, t.student_id, t.created_at, t.file_path,
                       pr.id AS result_id, pr.overall_score, pr.internal_score,
                       pr.web_score, pr.confidential_score, pr.risk_level,
                       pr.status, pr.analyzed_at
                FROM theses t
                LEFT JOIN plagiarism_results pr ON pr.thesis_id = t.id
                ORDER BY t.id DESC
                LIMIT 8
                """
            )
        ).mappings().all()
        print("=== LATEST THESES ===")
        for r in rows:
            print(dict(r))

        if not rows:
            return

        tid = int(rows[0]["id"])
        print(f"\n=== DETAIL thesis {tid} ===")
        pr = db.execute(
            text("SELECT * FROM plagiarism_results WHERE thesis_id=:tid ORDER BY id DESC LIMIT 1"),
            {"tid": tid},
        ).mappings().first()
        if not pr:
            print("No result")
            thesis = db.execute(
                text("SELECT id, title, LEFT(content, 500) AS c FROM theses WHERE id=:tid"),
                {"tid": tid},
            ).mappings().first()
            print(thesis)
            return

        for k, v in dict(pr).items():
            if k == "ai_analysis" and v:
                print(f"ai_analysis: str len={len(v)}")
            elif isinstance(v, str) and len(v) > 200:
                print(f"{k}: str len={len(v)}")
            else:
                print(f"{k}: {v}")

        raw = pr.get("ai_analysis")
        if raw:
            try:
                data = json.loads(raw)
            except Exception as e:
                print("ai_analysis not JSON", e)
                print(raw[:500])
                data = None
            if isinstance(data, dict):
                print("ai_analysis keys:", sorted(data.keys()))
                for key in (
                    "overall_score",
                    "unique_word_coverage",
                    "summary",
                    "risk_level",
                    "match_statistics",
                    "scores",
                ):
                    if key in data:
                        print(f"  {key}:", data[key] if not isinstance(data[key], (list, dict)) else json.dumps(data[key], ensure_ascii=False)[:400])

                hl = data.get("line_highlights") or data.get("highlighted_document") or []
                if isinstance(hl, list):
                    flagged = [h for h in hl if isinstance(h, dict) and h.get("match_type")]
                    by_type: dict[str, int] = {}
                    for h in flagged:
                        mt = h.get("match_type") or "?"
                        by_type[mt] = by_type.get(mt, 0) + 1
                    print(f"highlights total={len(hl)} flagged={len(flagged)} by_type={by_type}")
                    for h in flagged[:20]:
                        print(
                            f"  L{h.get('line_number')} {h.get('match_type')} "
                            f"sim={h.get('similarity')} src={str(h.get('source_title') or '')[:40]!r} "
                            f"text={str(h.get('text') or '')[:100]!r}"
                        )

                matches = data.get("matches") or data.get("internal_matches") or []
                if isinstance(matches, list) and matches:
                    print(f"matches count={len(matches)}")
                    for m in matches[:10]:
                        if isinstance(m, dict):
                            print(
                                f"  {m.get('source_type')} sim={m.get('similarity') or m.get('similarity_score')} "
                                f"title={str(m.get('source_title') or '')[:60]!r}"
                            )

        # DB matches
        matches_db = db.execute(
            text(
                """
                SELECT source_type, source_title, similarity_score,
                       LEFT(original_text, 120) AS excerpt
                FROM plagiarism_matches WHERE result_id=:rid
                ORDER BY similarity_score DESC LIMIT 15
                """
            ),
            {"rid": pr["id"]},
        ).mappings().all()
        print(f"\nDB matches: {len(matches_db)}")
        for m in matches_db:
            print(dict(m))

        thesis = db.execute(
            text("SELECT id, title, CHAR_LENGTH(content) AS clen, LEFT(content, 900) AS preview FROM theses WHERE id=:tid"),
            {"tid": tid},
        ).mappings().first()
        print("\n=== THESIS ===")
        print({k: thesis[k] for k in thesis if k != "preview"})
        print(thesis["preview"])
        print("...")
    finally:
        db.close()


if __name__ == "__main__":
    main()
