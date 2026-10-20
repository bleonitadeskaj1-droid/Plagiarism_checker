"""Debug false-positive highlights on latest thesis."""
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
from detection.aligner import (
    _score_line_pair,
    _classify_match,
    _similarity_for_type,
    _word_tokens,
    _content_normalized,
    _is_header_only,
)
from detection.content_words import content_tokens


def main() -> None:
    db = SessionLocal()
    try:
        pr = db.execute(
            text("SELECT ai_analysis FROM plagiarism_results WHERE thesis_id=68 ORDER BY id DESC LIMIT 1")
        ).scalar()
        data = json.loads(pr)
        hl = [h for h in data.get("line_highlights") or [] if h.get("match_type")]
        # unique by line primary
        prim = {}
        for h in hl:
            if h.get("is_secondary"):
                continue
            ln = h["line_number"]
            if ln not in prim or float(h.get("similarity") or 0) > float(prim[ln].get("similarity") or 0):
                prim[ln] = h
        print("PRIMARY flagged lines:", len(prim))
        for ln, h in sorted(prim.items()):
            text_l = h.get("text") or ""
            src = h.get("source_text") or ""
            scores = _score_line_pair(text_l, src) if src else {}
            print("\n=== L", ln, h.get("match_type"), h.get("similarity"), "===")
            print("thesis:", text_l[:160])
            print("source_title:", h.get("source_title"))
            print("source:", src[:160])
            print("content_tokens thesis:", content_tokens(text_l))
            print("content_tokens source:", content_tokens(src[:300]))
            print("is_header:", _is_header_only(text_l), "n_content:", len(_word_tokens(text_l)))
            if scores:
                mt = _classify_match(
                    scores["ratio"],
                    scores["sort_ratio"],
                    scores["word_overlap"] / 100,
                    scores.get("full_ratio", 0),
                    scores.get("full_sort_ratio", 0),
                )
                print("rescore:", {k: round(v, 1) for k, v in scores.items()}, "->", mt, _similarity_for_type(mt, scores) if mt else None)

        # also check thesis 67
        print("\n\n======== THESIS 67 ========")
        pr67 = db.execute(
            text("SELECT overall_score, ai_analysis FROM plagiarism_results WHERE thesis_id=67 ORDER BY id DESC LIMIT 1")
        ).mappings().first()
        print("score", pr67["overall_score"])
        d67 = json.loads(pr67["ai_analysis"])
        print("summary", d67.get("summary", "")[:300])
        print("by_type", (d67.get("match_statistics") or {}).get("by_match_type"))
        print("coverage", (d67.get("match_statistics") or {}).get("coverage"))
        thesis = db.execute(text("SELECT LEFT(content, 600) FROM theses WHERE id=67")).scalar()
        print("preview:", thesis)
    finally:
        db.close()


if __name__ == "__main__":
    main()
