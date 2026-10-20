"""Full re-analysis of theses that already have results (internal + confidential, no web)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import SessionLocal, check_db_connection
from models import PlagiarismResult, Thesis, ThesisStatus
from plagiarism_service import PlagiarismDetectionService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("reanalyze")


def main() -> int:
    status = check_db_connection()
    if not status.get("ok"):
        logger.error("DB unavailable: %s", status.get("error"))
        return 1

    db = SessionLocal()
    svc = PlagiarismDetectionService()
    try:
        rows = (
            db.query(PlagiarismResult)
            .filter(PlagiarismResult.status == "completed")
            .order_by(PlagiarismResult.id.desc())
            .all()
        )
        seen: set[int] = set()
        thesis_ids: list[int] = []
        for r in rows:
            if r.thesis_id in seen:
                continue
            seen.add(r.thesis_id)
            thesis_ids.append(int(r.thesis_id))

        logger.info("Re-analyzing %s theses (internal+confidential, no web)…", len(thesis_ids))
        for tid in thesis_ids:
            thesis = db.query(Thesis).filter(Thesis.id == tid).first()
            if not thesis:
                continue
            result = PlagiarismResult(thesis_id=tid, status="pending")
            thesis.status = ThesisStatus.analyzing
            db.add(result)
            db.commit()
            db.refresh(result)
            try:
                analysis = svc.process_analysis_result(
                    thesis_id=tid,
                    result_id=result.id,
                    db=db,
                    compare_confidential=True,
                    compare_internal=True,
                    search_web=False,
                )
                logger.info(
                    "thesis=%s result=%s overall=%s%% internal=%s%%",
                    tid,
                    result.id,
                    analysis.get("overall_score"),
                    analysis.get("internal_score"),
                )
            except Exception as exc:
                logger.exception("thesis=%s failed: %s", tid, exc)
                try:
                    svc.mark_analysis_error(db, tid, result.id, str(exc))
                except Exception:
                    pass
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
