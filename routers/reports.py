from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from auth import get_current_user
from access_control import assert_thesis_access
from database import get_db
from models import PlagiarismResult, Thesis, User
from ai_agent import PlagiarismAgent

router = APIRouter()
agent = PlagiarismAgent()


class GenerateReportRequest(BaseModel):
    result_id: int


@router.post("/generate")
def generate_report(
    req: GenerateReportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = db.query(PlagiarismResult).filter(PlagiarismResult.id == req.result_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")

    thesis = db.query(Thesis).filter(Thesis.id == result.thesis_id).first()
    if not thesis:
        raise HTTPException(status_code=404, detail="Thesis not found")
    assert_thesis_access(db, thesis, current_user)
    title = thesis.title if thesis else 'Tema'

    # Try to generate a summary via AI agent (or mock)
    try:
        analysis = {}
        if result.ai_analysis:
            import json as _json
            try:
                analysis = _json.loads(result.ai_analysis)
            except Exception:
                analysis = {}

        report_text = agent.generate_report_summary(analysis or {}, title)
    except Exception as e:
        report_text = f"Gabim gjatë gjenerimit të raportit: {str(e)}"

    return {"result_id": result.id, "report": report_text}


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

    page = visible[skip: skip + limit]
    return [
        {
            "id": r.id,
            "thesis_id": r.thesis_id,
            "status": r.status,
            "overall_score": float(r.overall_score or 0),
        }
        for r in page
    ]
