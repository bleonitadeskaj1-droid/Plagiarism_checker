# ============================================
# routers/analysis.py - Endpoints për Analizë
# ============================================

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel
from typing import Optional
import json
import logging

from auth import get_current_user
from access_control import assert_thesis_access, can_view_final_review
from database import get_db
from document_processor import DocumentProcessor
from services.thesis_text import get_thesis_text
from models import Thesis, PlagiarismResult, PlagiarismMatch, ThesisStatus, User, UserRole
from plagiarism_service import PlagiarismDetectionService
from pathlib import Path
from agent.web_search.config import get_web_search_status, get_official_providers, require_api_providers

router = APIRouter()
logger = logging.getLogger(__name__)
service = PlagiarismDetectionService()
processor = DocumentProcessor()


class AnalysisRequest(BaseModel):
    thesis_id: int
    compare_confidential: bool = True
    compare_internal: bool = True
    search_web: bool = False
    department_filter: Optional[str] = None


class CompareRequest(BaseModel):
    thesis_id_a: int
    thesis_id_b: int


@router.get("/web-search/status")
def web_search_status():
    """Status i kërkimit web (API keys / SearXNG fallback)."""
    return get_web_search_status()


def _parse_thesis_id(source_url: Optional[str]) -> Optional[int]:
    if not source_url or not source_url.startswith("internal://thesis/"):
        return None
    try:
        return int(source_url.rsplit("/", 1)[-1])
    except (TypeError, ValueError):
        return None


def _serialize_matches(result: PlagiarismResult, ai_data: dict) -> list:
    enriched = ai_data.get("flagged_sections")
    if not enriched:
        enriched = (ai_data.get("internal_sources") or []) + (ai_data.get("web_sources") or [])
    if enriched:
        payload = []
        for section in enriched:
            if section.get("source_type") == "confidential":
                continue
            thesis_id = section.get("thesis_id") or _parse_thesis_id(section.get("source_url"))
            payload.append(
                {
                    "id": section.get("id"),
                    "source_type": section.get("source_type"),
                    "source_title": section.get("source_title") or section.get("source"),
                    "source_url": section.get("source_url"),
                    "thesis_id": thesis_id,
                    "is_archive": bool(section.get("is_archive")),
                    "original_text": section.get("matched_text") or section.get("original_text"),
                    "thesis_excerpt": section.get("thesis_excerpt"),
                    "matched_text": section.get("matched_text") or section.get("original_text"),
                    "web_snippet_full": section.get("web_snippet_full"),
                    "thesis_phrase": section.get("thesis_phrase"),
                    "word_overlap": section.get("word_overlap"),
                    "verify_score": section.get("verify_score"),
                    "search_provider": section.get("search_provider"),
                    "similarity_score": float(section.get("similarity") or section.get("similarity_score") or 0.0),
                    "verified": section.get("verified"),
                    "line_number": section.get("line_number"),
                    "source_line_number": section.get("source_line_number"),
                    "source_text": section.get("source_text"),
                    "match_type": section.get("match_type"),
                }
            )
        return payload

    payload = []
    for match in result.matches:
        if match.source_type == "confidential":
            continue
        payload.append(
            {
                "id": match.id,
                "source_type": match.source_type,
                "source_title": match.source_title,
                "source_url": match.source_url,
                "thesis_id": _parse_thesis_id(match.source_url),
                "is_archive": False,
                "original_text": match.original_text,
                "thesis_excerpt": None,
                "matched_text": match.original_text,
                "similarity_score": float(match.similarity_score or 0.0),
                "verified": None,
            }
        )
    return payload


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

    thesis_content = get_thesis_text(thesis, processor)
    if thesis_content and not (thesis.content or "").strip():
        thesis.content = thesis_content
        db.commit()

    if not thesis_content:
        raise HTTPException(status_code=400, detail="Tema nuk ka përmbajtje të lexueshme për analizë")

    if request.search_web and require_api_providers() and not get_official_providers():
        raise HTTPException(
            status_code=400,
            detail=(
                "Kërkimi web kërkon Search API key. Vendosni në .env një nga: "
                "SERPER_API_KEY (https://serper.dev/), BRAVE_SEARCH_API_KEY, "
                "SERPAPI_KEY, TAVILY_API_KEY, BING_SEARCH_API_KEY — "
                "ose çaktivizoni 'Kërko në internet'."
            ),
        )

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
        compare_confidential=request.compare_confidential,
        compare_internal=request.compare_internal,
        search_web=request.search_web,
        department_filter=request.department_filter,
    )

    return {
        "message": "Analiza filloi",
        "thesis_id": thesis.id,
        "result_id": result.id,
        "status": "analyzing"
    }


def run_analysis(thesis_id: int, result_id: int, compare_confidential: bool, compare_internal: bool, search_web: bool, department_filter: Optional[str] = None):
    """Background task — short DB read, analyze without DB, then save."""
    from database import SessionLocal

    plan = None
    db = SessionLocal()
    try:
        plan = service.load_analysis_plan(
            db,
            thesis_id=thesis_id,
            result_id=result_id,
            compare_confidential=compare_confidential,
            compare_internal=compare_internal,
            search_web=search_web,
            department_filter=department_filter,
        )
    except Exception as exc:
        logger.error("Analysis load failed: %s", exc, exc_info=True)
        try:
            service.mark_analysis_error(db, thesis_id, result_id, str(exc))
        except Exception:
            pass
        return
    finally:
        db.close()

    payload = None
    try:
        payload = service.build_payload_from_plan(plan)
    except Exception as exc:
        logger.error("Analysis corpus load failed: %s", exc, exc_info=True)
        db = SessionLocal()
        try:
            service.mark_analysis_error(db, thesis_id, result_id, str(exc))
        finally:
            db.close()
        return

    try:
        logger.info("Analysis running for thesis %s (web=%s)", thesis_id, search_web)
        analysis = service.execute_analysis(payload)
    except Exception as exc:
        logger.error("Plagiarism analysis failed: %s", exc, exc_info=True)
        db = SessionLocal()
        try:
            service.mark_analysis_error(db, thesis_id, result_id, str(exc))
        finally:
            db.close()
        return

    db = SessionLocal()
    try:
        service.persist_analysis_result(db, thesis_id, result_id, analysis)
        logger.info("Analysis completed for thesis %s result %s", thesis_id, result_id)
    except Exception as exc:
        logger.error("Analysis save failed: %s", exc, exc_info=True)
        try:
            service.mark_analysis_error(db, thesis_id, result_id, str(exc))
        except Exception:
            pass
    finally:
        db.close()


@router.post("/compare")
def compare_two_theses(
    request: CompareRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Krahaso dy tema lokalisht (TF-IDF + Jaccard + SequenceMatcher)."""
    if current_user.role == UserRole.student:
        raise HTTPException(status_code=403, detail="Studenti nuk mund të krahasojë tema")

    thesis_a = db.query(Thesis).filter(Thesis.id == request.thesis_id_a).first()
    thesis_b = db.query(Thesis).filter(Thesis.id == request.thesis_id_b).first()
    if not thesis_a or not thesis_b:
        raise HTTPException(status_code=404, detail="Njëra ose të dyja temat nuk u gjetën")

    assert_thesis_access(db, thesis_a, current_user)
    result = service.compare_theses(thesis_a, thesis_b)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/result/{result_id}")
def get_result(
    result_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Merr rezultatin e analizës."""

    result = db.query(PlagiarismResult).options(
        joinedload(PlagiarismResult.thesis),
        joinedload(PlagiarismResult.matches),
    ).filter(PlagiarismResult.id == result_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="Rezultati nuk u gjet")

    thesis = result.thesis
    if not thesis:
        raise HTTPException(status_code=404, detail="Tema nuk u gjet")
    assert_thesis_access(db, thesis, current_user)

    if current_user.role == UserRole.student and not can_view_final_review(thesis, current_user, db):
        raise HTTPException(status_code=403, detail="Rezultati nuk është gati ende")

    error_detail = None
    if result.status == "error" and result.ai_analysis:
        try:
            error_detail = json.loads(result.ai_analysis).get("error")
        except Exception:
            pass

    matches = result.matches

    ai_data = {}
    if result.ai_analysis:
        try:
            ai_data = json.loads(result.ai_analysis)
        except Exception:
            pass

    wf = thesis.workflow_status.value if getattr(thesis.workflow_status, "value", None) else thesis.workflow_status
    score = float(result.overall_score or 0)
    serialized_matches = _serialize_matches(result, ai_data)

    return {
        "id": result.id,
        "thesis_id": result.thesis_id,
        "status": result.status,
        "error": error_detail,
        "overall_score": score,
        "internal_score": float(result.internal_score or 0),
        "confidential_score": float(result.confidential_score or 0),
        "web_score": float(result.web_score or 0),
        "ai_likelihood_score": float(ai_data.get("ai_likelihood_score") or ai_data.get("metadata", {}).get("ai_likelihood_score") or 0),
        "ai_detected": bool(ai_data.get("ai_detected") or float(ai_data.get("ai_likelihood_score") or 0) >= 35),
        "coverage": ai_data.get("coverage") or (ai_data.get("match_statistics") or {}).get("coverage") or {},
        "risk_level": ai_data.get("risk_level", "unknown"),
        "summary": ai_data.get("summary", ""),
        "recommendations": ai_data.get("recommendations", ""),
        "auto_approved": False,
        "workflow_status": wf,
        "matches": serialized_matches,
        "internal_sources": [m for m in serialized_matches if m.get("source_type") == "internal"],
        "web_sources": [m for m in serialized_matches if m.get("source_type") == "web"],
        "line_highlights": ai_data.get("line_highlights") or [],
        "highlighted_document": ai_data.get("highlighted_document") or [],
        "match_statistics": ai_data.get("match_statistics") or {},
        "source_documents": ai_data.get("source_documents") or [],
        "match_fragments": ai_data.get("match_fragments") or [],
        "analyzed_at": result.analyzed_at.isoformat() if result.analyzed_at else None
    }


@router.get("/thesis/{thesis_id}/highlights")
def get_thesis_highlights(
    thesis_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Kthen rreshtat e theksuar për temën (nga analiza e fundit, ose rillogarit nëse mungojnë)."""
    from detection.aligner import build_thesis_highlights, render_highlighted_lines
    from detection.stats import compute_match_statistics, build_source_summaries, build_match_fragments
    from services.thesis_text import get_thesis_text
    from models import Thesis as ThesisModel, SubmissionWorkflowStatus
    from sqlalchemy import or_

    thesis = db.query(Thesis).filter(Thesis.id == thesis_id).first()
    if not thesis:
        raise HTTPException(status_code=404, detail="Tema nuk u gjet")
    assert_thesis_access(db, thesis, current_user)

    if current_user.role == UserRole.student and not can_view_final_review(thesis, current_user, db):
        raise HTTPException(status_code=403, detail="Rezultati nuk është gati ende")

    latest = (
        db.query(PlagiarismResult)
        .filter(PlagiarismResult.thesis_id == thesis_id, PlagiarismResult.status == "completed")
        .order_by(PlagiarismResult.analyzed_at.desc())
        .first()
    )
    if not latest or not latest.ai_analysis:
        return {"thesis_id": thesis_id, "line_highlights": [], "highlighted_document": [], "web_sources": []}

    try:
        ai_data = json.loads(latest.ai_analysis)
    except Exception:
        ai_data = {}

    line_highlights = ai_data.get("line_highlights") or []
    highlighted_document = ai_data.get("highlighted_document") or []
    web = ai_data.get("web_sources") or [
        m for m in (ai_data.get("flagged_sections") or []) if m.get("source_type") == "web"
    ]
    internal = ai_data.get("internal_sources") or [
        m for m in (ai_data.get("flagged_sections") or []) if m.get("source_type") == "internal"
    ]
    if not internal and not web:
        internal = [m for m in (ai_data.get("flagged_sections") or []) if m.get("source_type") != "web"]
        web = [m for m in (ai_data.get("flagged_sections") or []) if m.get("source_type") == "web"]

    has_cached_highlights = any(h.get("match_type") for h in line_highlights)
    if has_cached_highlights:
        if not highlighted_document:
            thesis_text = get_thesis_text(thesis, processor) or thesis.content or ""
            if thesis_text.strip():
                highlighted_document = render_highlighted_lines(line_highlights, thesis_text)
        return {
            "thesis_id": thesis_id,
            "result_id": latest.id,
            "line_highlights": line_highlights,
            "highlighted_document": highlighted_document,
            "match_statistics": ai_data.get("match_statistics") or {},
            "source_documents": ai_data.get("source_documents") or [],
            "match_fragments": ai_data.get("match_fragments") or [],
            "web_sources": web,
        }

    # Rillogarit vetëm kur mungojnë theksimet (shmang ngarkimin e 60 PDF-ve çdo klik)
    thesis_text = get_thesis_text(thesis, processor) or thesis.content or ""
    if thesis_text.strip():
        from models import Thesis as ThesisModel

        all_matches = (ai_data.get("flagged_sections") or []) + internal + web
        seen_ids: set = set()
        unique_internal = []
        matched_thesis_ids: set[int] = set()
        for m in all_matches:
            if m.get("source_type") == "web":
                continue
            tid = m.get("thesis_id") or _parse_thesis_id(m.get("source_url"))
            if tid:
                matched_thesis_ids.add(tid)
            key = tid or m.get("source_title")
            if key in seen_ids:
                continue
            seen_ids.add(key)
            if tid and not m.get("thesis_id"):
                m = {**m, "thesis_id": tid}
            unique_internal.append(m)

        docs = []
        doc_ids_loaded: set[int] = set()
        if matched_thesis_ids:
            for cand in (
                db.query(ThesisModel)
                .filter(ThesisModel.id.in_(matched_thesis_ids), ThesisModel.id != thesis_id)
                .all()
            ):
                text = get_thesis_text(cand, processor) or cand.content or ""
                if text.strip():
                    docs.append({"id": cand.id, "title": cand.title, "content": text})
                    doc_ids_loaded.add(cand.id)

        if not docs and unique_internal:
            for cand in (
                db.query(ThesisModel)
                .filter(
                    ThesisModel.id != thesis_id,
                    ThesisModel.workflow_status != SubmissionWorkflowStatus.rejected,
                )
                .order_by(ThesisModel.is_archive.desc(), ThesisModel.id.desc())
                .limit(12)
                .all()
            ):
                if cand.id in doc_ids_loaded:
                    continue
                text = get_thesis_text(cand, processor) or cand.content or ""
                if text.strip():
                    docs.append({"id": cand.id, "title": cand.title, "content": text})

        line_highlights = build_thesis_highlights(
            thesis_text, unique_internal, web, docs
        )
        highlighted_document = render_highlighted_lines(line_highlights, thesis_text)
        match_statistics = compute_match_statistics(line_highlights, thesis_text)
        source_documents = build_source_summaries(line_highlights, all_matches)
        match_fragments = build_match_fragments(line_highlights)

        try:
            ai_data["line_highlights"] = line_highlights
            ai_data["highlighted_document"] = highlighted_document
            ai_data["match_statistics"] = match_statistics
            ai_data["source_documents"] = source_documents
            ai_data["match_fragments"] = match_fragments
            latest.ai_analysis = json.dumps(ai_data, ensure_ascii=False)
            db.commit()
        except Exception as exc:
            logger.warning("Could not persist recomputed highlights: %s", exc)
            db.rollback()

    return {
        "thesis_id": thesis_id,
        "result_id": latest.id,
        "line_highlights": line_highlights,
        "highlighted_document": highlighted_document,
        "match_statistics": ai_data.get("match_statistics") or {},
        "source_documents": ai_data.get("source_documents") or [],
        "match_fragments": ai_data.get("match_fragments") or [],
        "web_sources": web,
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
