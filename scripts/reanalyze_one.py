"""Re-analyze a single thesis id with current detection code."""
from __future__ import annotations

import json
import logging
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

from database import SessionLocal, check_db_connection
from models import PlagiarismResult, Thesis, ThesisStatus
from plagiarism_service import PlagiarismDetectionService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("reanalyze_one")


def main() -> int:
    tid = int(sys.argv[1]) if len(sys.argv) > 1 else 68
    status = check_db_connection()
    if not status.get("ok"):
        logger.error("DB unavailable: %s", status.get("error"))
        return 1

    db = SessionLocal()
    svc = PlagiarismDetectionService()
    try:
        thesis = db.query(Thesis).filter(Thesis.id == tid).first()
        if not thesis:
            logger.error("Thesis %s not found", tid)
            return 1
        result = PlagiarismResult(thesis_id=tid, status="pending")
        thesis.status = ThesisStatus.analyzing
        db.add(result)
        db.commit()
        db.refresh(result)
        analysis = svc.process_analysis_result(
            thesis_id=tid,
            result_id=result.id,
            db=db,
            compare_confidential=True,
            compare_internal=True,
            search_web=False,
        )
        overall = float(analysis.get("overall_score") or 0)
        stats = analysis.get("match_statistics") or {}
        hl = [h for h in (analysis.get("line_highlights") or []) if h.get("match_type")]
        by: dict[str, int] = {}
        for h in hl:
            if h.get("is_secondary"):
                continue
            by[h["match_type"]] = by.get(h["match_type"], 0) + 1
        logger.info("thesis=%s overall=%.2f by_type=%s flagged_primary=%s", tid, overall, by, sum(by.values()))
        logger.info("summary=%s", (analysis.get("summary") or "")[:350])
        for h in hl:
            if h.get("is_secondary"):
                continue
            print(
                f"L{h.get('line_number')} {h.get('match_type')} {h.get('similarity')} "
                f"| {str(h.get('source_title') or '')[:40]} | {str(h.get('text') or '')[:90]}"
            )
        Path("scripts/_reanalyze_one_out.json").write_text(
            json.dumps(
                {
                    "overall_score": overall,
                    "by_type": by,
                    "match_statistics": stats,
                    "summary": analysis.get("summary"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return 0
    except Exception:
        logger.exception("failed")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
