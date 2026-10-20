"""Recompute plagiarism % for existing results — exclude lidhëza/function words.

Usage (from project root):
  .\\.venv\\Scripts\\python.exe scripts\\recompute_content_coverage.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import SessionLocal, check_db_connection
from document_processor import DocumentProcessor
from models import PlagiarismResult, Thesis
from plagiarism_service import apply_analysis_verdict
from detection.stats import compute_match_statistics, compute_unique_coverage, soften_similar_scores
from services.thesis_text import get_thesis_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("recompute_coverage")


def _load_analysis(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def recompute_one(db, result: PlagiarismResult, processor: DocumentProcessor) -> dict | None:
    thesis = db.query(Thesis).filter(Thesis.id == result.thesis_id).first()
    if not thesis:
        return None

    analysis = _load_analysis(result.ai_analysis)
    if analysis.get("error"):
        return None

    thesis_text = get_thesis_text(thesis, processor, use_cache=True)
    if not thesis_text or len(thesis_text.strip()) < 40:
        logger.warning("Thesis %s: no text — skip", thesis.id)
        return None

    highlights = analysis.get("line_highlights") or []
    matches = []
    for key in ("flagged_sections", "internal_sources", "web_sources"):
        items = analysis.get(key) or []
        if isinstance(items, list):
            matches.extend(items)

    soften_similar_scores(highlights)
    soften_similar_scores(analysis.get("highlighted_document") if isinstance(analysis.get("highlighted_document"), list) else None)
    for key in ("flagged_sections", "internal_sources", "web_sources", "source_documents", "match_fragments"):
        if isinstance(analysis.get(key), list):
            soften_similar_scores(analysis[key])

    coverage = compute_unique_coverage(thesis_text, highlights, matches)
    match_stats = compute_match_statistics(highlights, thesis_text)
    match_stats["coverage"] = coverage

    old_overall = float(result.overall_score or 0)
    new_overall = float(coverage["overall_pct"])
    new_internal = float(coverage["internal_pct"])
    new_web = float(coverage["web_pct"])

    # Keep confidential channel from previous analysis if present
    old_conf = float(result.confidential_score or analysis.get("confidential_score") or 0)

    analysis["overall_score"] = new_overall
    analysis["internal_score"] = new_internal
    analysis["web_score"] = new_web
    analysis["coverage"] = coverage
    analysis["match_statistics"] = match_stats
    meta = analysis.get("metadata") if isinstance(analysis.get("metadata"), dict) else {}
    meta["scoring_method"] = "weighted_content_word_coverage"
    meta["analysis_method"] = "weighted content-word coverage (similar text discounted; lidhëzat excluded; AI excluded)"
    meta["recomputed_content_words"] = True
    analysis["metadata"] = meta

    # Refresh summary line if present
    title = thesis.title or analysis.get("metadata", {}).get("source_title") or f"Tema {thesis.id}"
    analysis["summary"] = (
        f"Analiza për '{title}' gjeti {new_overall}% plagjiaturë totale "
        f"(mbulim unik, pa lidhëza: {coverage['unique_plagiarized_words']}/"
        f"{coverage['total_words']} fjalë përmbajtjeje). "
        f"Databaza e brendshme: {new_internal}% · Internet: {new_web}%."
    )

    result.overall_score = new_overall
    result.internal_score = new_internal
    result.web_score = new_web
    result.confidential_score = old_conf
    result.ai_analysis = json.dumps(analysis, ensure_ascii=False)

    verdict = apply_analysis_verdict(db, thesis, new_overall)
    db.add(result)
    db.commit()

    return {
        "thesis_id": thesis.id,
        "result_id": result.id,
        "title": (thesis.title or "")[:60],
        "old": round(old_overall, 2),
        "new": round(new_overall, 2),
        "internal": new_internal,
        "web": new_web,
        "content_words": coverage["total_words"],
        "excluded": coverage.get("excluded_function_words", 0),
        "auto_approved": verdict.get("auto_approved"),
    }


def main() -> int:
    status = check_db_connection()
    if not status.get("ok"):
        logger.error("Database unavailable: %s", status.get("error"))
        return 1

    db = SessionLocal()
    processor = DocumentProcessor()
    updated = 0
    skipped = 0
    try:
        rows = (
            db.query(PlagiarismResult)
            .filter(PlagiarismResult.status == "completed")
            .order_by(PlagiarismResult.id.desc())
            .all()
        )
        # Keep latest result per thesis
        seen_thesis: set[int] = set()
        latest: list[PlagiarismResult] = []
        for r in rows:
            if r.thesis_id in seen_thesis:
                continue
            seen_thesis.add(r.thesis_id)
            latest.append(r)

        logger.info("Recomputing content-word coverage for %s theses…", len(latest))
        for r in latest:
            try:
                info = recompute_one(db, r, processor)
                if not info:
                    skipped += 1
                    continue
                updated += 1
                logger.info(
                    "thesis=%s «%s» %s%% → %s%% (content_words=%s, excluded_func=%s)",
                    info["thesis_id"],
                    info["title"],
                    info["old"],
                    info["new"],
                    info["content_words"],
                    info["excluded"],
                )
            except Exception as exc:
                db.rollback()
                skipped += 1
                logger.exception("Failed result_id=%s: %s", r.id, exc)

        logger.info("Done. updated=%s skipped=%s", updated, skipped)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
