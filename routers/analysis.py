# ============================================
# routers/analysis.py - Endpoints për Analizë
# ============================================

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import json

from auth import get_current_user
from access_control import assert_thesis_access, can_view_final_review
from database import get_db
from models import Thesis, PlagiarismResult, PlagiarismMatch, ThesisStatus, User, UserRole
from ai_agent import PlagiarismAgent
from routers.theses import extract_text

router = APIRouter()
agent = PlagiarismAgent()


class AnalysisRequest(BaseModel):
    thesis_id: int
    compare_internal: bool = True
    search_web: bool = True


@router.post("/start")
async def start_analysis(
    request: AnalysisRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fillon analizën e plagjiaturës për një temë."""

    # Gjej temën
    thesis = db.query(Thesis).filter(Thesis.id == request.thesis_id).first()
    if not thesis:
        raise HTTPException(status_code=404, detail="Tema nuk u gjet")
    assert_thesis_access(db, thesis, current_user)

    if current_user.role == UserRole.student:
        raise HTTPException(status_code=403, detail="Studenti nuk mund të nisë analizë")

    thesis_content = (thesis.content or "").strip()
    if not thesis_content and thesis.file_path:
        file_ext = (thesis.file_type or "").lower()
        if not file_ext and thesis.file_path:
            file_ext = thesis.file_path.rsplit(".", 1)[-1].lower() if "." in thesis.file_path else ""

        if file_ext:
            thesis_content = (extract_text(thesis.file_path, file_ext) or "").strip()
            if thesis_content:
                thesis.content = thesis_content
                db.commit()

    if not thesis_content:
        raise HTTPException(status_code=400, detail="Tema nuk ka përmbajtje të lexueshme për analizë")

    # Ndrysho statusin
    thesis.status = ThesisStatus.analyzing
    db.commit()

    # Krijo rekord rezultati
    result = PlagiarismResult(
        thesis_id=thesis.id,
        status="pending"
    )
    db.add(result)
    db.commit()
    db.refresh(result)

    # Nis analizën në background
    background_tasks.add_task(
        run_analysis,
        thesis_id=thesis.id,
        result_id=result.id,
        compare_internal=request.compare_internal,
        search_web=request.search_web
    )

    return {
        "message": "Analiza filloi",
        "thesis_id": thesis.id,
        "result_id": result.id,
        "status": "analyzing"
    }


def run_analysis(thesis_id: int, result_id: int, compare_internal: bool, search_web: bool):
    """Background task - ekzekuton analizën e plotë."""
    from database import SessionLocal

    db = SessionLocal()
    try:
        thesis = db.query(Thesis).filter(Thesis.id == thesis_id).first()
        result = db.query(PlagiarismResult).filter(PlagiarismResult.id == result_id).first()
        thesis_content = (thesis.content or "").strip() if thesis else ""

        # Merr temat ekzistuese për krahasim
        existing_theses = []
        if compare_internal:
            other_theses = db.query(Thesis).filter(
                Thesis.id != thesis_id,
                Thesis.content != None
            ).limit(50).all()

            existing_theses = [
                {"id": t.id, "title": t.title, "content": t.content[:1000]}
                for t in other_theses
            ]

        # Thirr AI agjentin
        analysis = agent.analyze_plagiarism(
            thesis_text=thesis_content,
            thesis_title=thesis.title if thesis else "",
            existing_theses=existing_theses,
            search_web=search_web
        )

        # Ruaj rezultatin
        result.overall_score = analysis.get("overall_score", 0)
        result.internal_score = analysis.get("internal_score", 0)
        result.web_score = analysis.get("web_score", 0)
        result.ai_analysis = json.dumps(analysis, ensure_ascii=False)
        result.status = "completed"

        # Ruaj ndeshjet (matches)
        for section in analysis.get("flagged_sections", []):
            match = PlagiarismMatch(
                result_id=result.id,
                source_type=section.get("source_type", "web"),
                source_url=section.get("source") if section.get("source_type") == "web" else None,
                source_title=section.get("source", ""),
                original_text=section.get("original_text") or section.get("text", ""),
                similarity_score=section.get("similarity", 0)
            )
            db.add(match)

        # Ndrysho statusin e temës
        score = float(analysis.get("overall_score", 0))
        thesis.status = ThesisStatus.flagged if score > 30 else ThesisStatus.completed

        db.commit()

    except Exception as e:
        if result:
            result.status = "error"
            result.ai_analysis = json.dumps({"error": str(e)})
            db.commit()
        if thesis:
            thesis.status = ThesisStatus.pending
            db.commit()
    finally:
        db.close()


@router.get("/result/{result_id}")
def get_result(
    result_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Merr rezultatin e analizës."""

    result = db.query(PlagiarismResult).filter(PlagiarismResult.id == result_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="Rezultati nuk u gjet")

    thesis = db.query(Thesis).filter(Thesis.id == result.thesis_id).first()
    if not thesis:
        raise HTTPException(status_code=404, detail="Tema nuk u gjet")
    assert_thesis_access(db, thesis, current_user)

    if current_user.role == UserRole.student and not can_view_final_review(thesis, current_user, db):
        raise HTTPException(status_code=403, detail="Rezultati nuk është gati ende")

    matches = db.query(PlagiarismMatch).filter(
        PlagiarismMatch.result_id == result_id
    ).all()

    ai_data = {}
    if result.ai_analysis:
        try:
            ai_data = json.loads(result.ai_analysis)
        except:
            pass

    return {
        "id": result.id,
        "thesis_id": result.thesis_id,
        "status": result.status,
        "overall_score": float(result.overall_score or 0),
        "internal_score": float(result.internal_score or 0),
        "confidential_score": float(result.confidential_score or 0),
        "web_score": float(result.web_score or 0),
        "risk_level": ai_data.get("risk_level", "unknown"),
        "summary": ai_data.get("summary", ""),
        "recommendations": ai_data.get("recommendations", ""),
        "matches": [
            {
                "id": m.id,
                "source_type": m.source_type,
                "source_title": m.source_title,
                "source_url": m.source_url,
                "original_text": m.original_text,
                "similarity_score": float(m.similarity_score or 0)
            }
            for m in matches
        ],
        "analyzed_at": result.analyzed_at.isoformat() if result.analyzed_at else None
    }


@router.get("/thesis/{thesis_id}/status")
def get_thesis_analysis_status(
    thesis_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Kontrollo statusin e analizës për një temë."""

    thesis = db.query(Thesis).filter(Thesis.id == thesis_id).first()
    if not thesis:
        raise HTTPException(status_code=404, detail="Tema nuk u gjet")
    assert_thesis_access(db, thesis, current_user)

    if current_user.role == UserRole.student and not can_view_final_review(thesis, current_user, db):
        raise HTTPException(status_code=403, detail="Tema është ende në proces")

    latest_result = db.query(PlagiarismResult).filter(
        PlagiarismResult.thesis_id == thesis_id
    ).order_by(PlagiarismResult.analyzed_at.desc()).first()

    return {
        "thesis_id": thesis_id,
        "thesis_status": thesis.status,
        "result_id": latest_result.id if latest_result else None,
        "analysis_status": latest_result.status if latest_result else "not_started"
    }
