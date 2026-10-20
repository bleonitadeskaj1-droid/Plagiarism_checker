import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from access_control import assert_thesis_access
from auth import get_current_user
from database import get_db
from models import PlagiarismResult, Thesis, User
from plagiarism_agent import PlagiarismAgent

logger = logging.getLogger(__name__)
router = APIRouter()
agent = PlagiarismAgent()
REPORT_DIR = Path("uploads/reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)


class GenerateReportRequest(BaseModel):
    result_id: int


def _analysis_payload(result: PlagiarismResult) -> dict:
    if not result.ai_analysis:
        return {
            "overall_score": float(result.overall_score or 0),
            "internal_score": float(result.internal_score or 0),
            "confidential_score": float(result.confidential_score or 0),
            "risk_level": result.risk_level or "low",
            "matches": [],
        }
    try:
        return json.loads(result.ai_analysis)
    except Exception:
        return {"overall_score": float(result.overall_score or 0), "matches": []}


def _get_result_or_404(db: Session, result_id: int, current_user: User) -> tuple[PlagiarismResult, Thesis]:
    result = db.query(PlagiarismResult).filter(PlagiarismResult.id == result_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")
    thesis = db.query(Thesis).filter(Thesis.id == result.thesis_id).first()
    if not thesis:
        raise HTTPException(status_code=404, detail="Thesis not found")
    assert_thesis_access(db, thesis, current_user)
    return result, thesis


@router.post("/generate")
def generate_report(
    req: GenerateReportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result, thesis = _get_result_or_404(db, req.result_id, current_user)
    analysis = _analysis_payload(result)
    try:
        report_text = agent.generate_report_summary(analysis, thesis.title)
    except Exception as exc:
        logger.error("Report summary failed: %s", exc)
        report_text = f"Gabim gjatë gjenerimit të raportit: {exc}"
    return {"result_id": result.id, "report": report_text}


@router.get("/{result_id}/pdf")
def download_report_pdf(
    result_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate and download a PDF plagiarism report."""
    result, thesis = _get_result_or_404(db, result_id, current_user)
    analysis = _analysis_payload(result)

    try:
        from report_generator import PlagiarismReportGenerator

        generator = PlagiarismReportGenerator(output_path=str(REPORT_DIR))
        pdf_path = generator.generate_report(
            thesis_title=thesis.title,
            analysis_result=analysis,
            student_info={"name": thesis.student.full_name if thesis.student else thesis.title},
            filename=f"report_{result_id}.pdf",
        )
        return FileResponse(
            path=str(pdf_path),
            media_type="application/pdf",
            filename=f"raport_plagjiature_{result_id}.pdf",
        )
    except ImportError as exc:
        logger.error("PDF dependencies missing: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="PDF generator nuk është i disponueshëm. Instalo: pip install reportlab matplotlib seaborn",
        ) from exc
    except Exception as exc:
        logger.exception("PDF generation failed for result_id=%s", result_id)
        raise HTTPException(status_code=500, detail=f"Gjenerimi i PDF dështoi: {exc}") from exc


@router.get("/")
def list_reports(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    all_results = db.query(PlagiarismResult).order_by(PlagiarismResult.analyzed_at.desc()).all()
    visible = []
    for result in all_results:
        thesis = db.query(Thesis).filter(Thesis.id == result.thesis_id).first()
        if not thesis:
            continue
        try:
            assert_thesis_access(db, thesis, current_user)
            visible.append(result)
        except HTTPException:
            continue

    page = visible[skip : skip + limit]
    return [
        {
            "id": r.id,
            "thesis_id": r.thesis_id,
            "status": r.status,
            "overall_score": float(r.overall_score or 0),
        }
        for r in page
    ]
