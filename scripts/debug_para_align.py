"""Why does thesis 68 paragraph-align against unrelated docs?"""
from __future__ import annotations

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
    _split_paragraphs,
    _score_line_pair,
    _classify_match,
    _similarity_for_type,
    align_lines_against_document,
    align_sentences_against_document,
    _align_paragraphs_against_document,
    _align_from_excerpts,
)
from detection.content_words import content_tokens


def main() -> None:
    db = SessionLocal()
    thesis = db.execute(text("SELECT content FROM theses WHERE id=68")).scalar()
    other = db.execute(text("SELECT id, title, content FROM theses WHERE id=52 OR title LIKE '%mediave%' LIMIT 1")).mappings().first()
    if not other:
        other = db.execute(
            text("SELECT id, title, content FROM theses WHERE title LIKE '%Efektet%' LIMIT 1")
        ).mappings().first()
    print("other", other["id"], other["title"], "len", len(other["content"] or ""))

    src_paras = _split_paragraphs(thesis)[:10]
    tgt_paras = _split_paragraphs(other["content"] or "")[:10]
    print("src paras", len(src_paras), "tgt", len(tgt_paras))
    for i, sp in enumerate(src_paras[:5]):
        best = None
        for tp in tgt_paras:
            scores = _score_line_pair(sp["normalized"][:2500], tp["normalized"][:2500])
            mt = _classify_match(
                scores["ratio"], scores["sort_ratio"], scores["word_overlap"] / 100,
                scores.get("full_ratio", 0), scores.get("full_sort_ratio", 0),
            )
            sim = _similarity_for_type(mt, scores) if mt else 0
            if mt and (best is None or sim > best[0]):
                best = (sim, mt, scores, tp["text"][:100])
        print(f"\nP{i} thesis={sp['text'][:90]!r}")
        print(" content", content_tokens(sp["text"])[:12])
        if best:
            print(" BEST", best[1], best[0], {k: round(v,1) for k,v in best[2].items()})
            print(" tgt", best[3])
        else:
            print(" BEST None")

    meta = {"source_type": "internal", "source_title": other["title"], "thesis_id": other["id"]}
    print("\n--- paragraph align count", len(_align_paragraphs_against_document(thesis, other["content"], meta)))
    print("--- line align", len(align_lines_against_document(thesis, other["content"], meta)))
    print("--- sent align", len(align_sentences_against_document(thesis, other["content"], meta)))

    # fake excerpt match like detection engine might produce
    fake_matches = [{
        "source_type": "internal",
        "source_title": other["title"],
        "thesis_id": other["id"],
        "similarity": 4.81,
        "thesis_excerpt": thesis[:200],
        "source_excerpt": (other["content"] or "")[:200],
    }]
    ex = _align_from_excerpts(thesis, fake_matches)
    print("--- excerpt align", len(ex))
    for h in ex[:5]:
        print(h["line_number"], h["match_type"], h["similarity"], h["text"][:80])

    db.close()


if __name__ == "__main__":
    main()
