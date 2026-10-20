import json
import logging
from typing import Any, Dict, List

from fastapi import HTTPException
from sqlalchemy.orm import Session

from confidential import decrypt_content
from models import (
    ConfidentialDocument,
    DocStatus,
    PlagiarismMatch,
    PlagiarismResult,
    Thesis,
    ThesisStatus,
    UserRole,
)
from plagiarism_agent import PlagiarismAgent

logger = logging.getLogger(__name__)


def run_plagiarism_analysis(thesis_id: int, db: Session) -> Dict[str, Any]:
    thesis = db.query(Thesis).filter(Thesis.id == thesis_id).first()
    if not thesis:
        raise HTTPException(status_code=404, detail="Thesis not found")

    if not thesis.content or not thesis.content.strip():
        raise HTTPException(status_code=400, detail="Thesis content is required for analysis")

    thesis.status = ThesisStatus.analyzing
    db.commit()

    public_theses = [
        {"id": t.id, "title": t.title, "content": t.content}
        for t in db.query(Thesis)
        .filter(Thesis.id != thesis_id, Thesis.content != None, Thesis.status == ThesisStatus.completed)
        .all()
    ]

    confidential_documents = []
    for doc in db.query(ConfidentialDocument).filter(ConfidentialDocument.status == DocStatus.active).all():
        try:
            plain_text = decrypt_content(doc.encrypted_content)
        except Exception as exc:
            logger.warning("Unable to decrypt confidential doc %s: %s", doc.id, exc)
            continue
        confidential_documents.append(
            {
                "id": doc.id,
                "title": doc.title,
                "content": plain_text,
            }
        )

    agent = PlagiarismAgent()
    analysis = agent.analyze_with_confidential(
        thesis_text=thesis.content,
        thesis_title=thesis.title,
        public_theses=public_theses,
        conf_docs=confidential_documents,
        search_web=False,
    )

    result = PlagiarismResult(
        thesis_id=thesis.id,
        overall_score=analysis.get("overall_score", 0.0),
        internal_score=analysis.get("internal_score", 0.0),
        confidential_score=analysis.get("confidential_score", 0.0),
        web_score=analysis.get("web_score", 0.0),
        ai_analysis=json.dumps(analysis, ensure_ascii=False),
        status="completed",
    )
    db.add(result)
    db.commit()
    db.refresh(result)

    for section in analysis.get("flagged_sections", []):
        match = PlagiarismMatch(
            result_id=result.id,
            source_type=section.get("source_type", "internal"),
            conf_source_id=section.get("conf_source_id"),
            source_title=section.get("source_title", section.get("source", "")),
            source_url=section.get("source_url"),
            original_text=section.get("original_text", ""),
            similarity_score=section.get("similarity", 0.0),
            paragraph_index=section.get("paragraph_index", 0),
        )
        db.add(match)

    overall_score = float(analysis.get("overall_score", 0.0))
    thesis.status = ThesisStatus.flagged if overall_score >= 50.0 else ThesisStatus.completed
    db.commit()

    return {
        "result_id": result.id,
        "thesis_id": thesis.id,
        "overall_score": float(result.overall_score or 0.0),
        "internal_score": float(result.internal_score or 0.0),
        "confidential_score": float(result.confidential_score or 0.0),
        "web_score": float(result.web_score or 0.0),
        "risk_level": analysis.get("risk_level", "unknown"),
        "summary": analysis.get("summary", ""),
        "recommendations": analysis.get("recommendations", ""),
        "status": result.status,
        "matches": analysis.get("flagged_sections", []),
    }


def get_thesis_results(thesis_id: int, db: Session, current_user: Any) -> Dict[str, Any]:
    thesis = db.query(Thesis).filter(Thesis.id == thesis_id).first()
    if not thesis:
        raise HTTPException(status_code=404, detail="Thesis not found")

    if getattr(current_user, "role", None) == UserRole.student:
        if thesis.submitted_by_user_id != getattr(current_user, "id", None):
            raise HTTPException(status_code=403, detail="Access denied for this thesis")

    result = (
        db.query(PlagiarismResult)
        .filter(PlagiarismResult.thesis_id == thesis_id)
        .order_by(PlagiarismResult.analyzed_at.desc())
        .first()
    )

    if not result:
        raise HTTPException(status_code=404, detail="Plagiarism result not found")

    matches = (
        db.query(PlagiarismMatch)
        .filter(PlagiarismMatch.result_id == result.id)
        .all()
    )

    parsed_ai = {}
    if result.ai_analysis:
        try:
            parsed_ai = json.loads(result.ai_analysis)
        except Exception:
            parsed_ai = {}

    return {
        "result_id": result.id,
        "thesis_id": thesis_id,
        "status": result.status,
        "overall_score": float(result.overall_score or 0.0),
        "internal_score": float(result.internal_score or 0.0),
        "confidential_score": float(result.confidential_score or 0.0),
        "web_score": float(result.web_score or 0.0),
        "risk_level": parsed_ai.get("risk_level", "unknown"),
        "summary": parsed_ai.get("summary", ""),
        "recommendations": parsed_ai.get("recommendations", ""),
        "matches": [
            {
                "id": m.id,
                "source_type": m.source_type,
                "conf_source_id": m.conf_source_id,
                "source_title": m.source_title,
                "source_url": m.source_url,
                "original_text": m.original_text,
                "similarity_score": float(m.similarity_score or 0.0),
                "paragraph_index": m.paragraph_index,
            }
            for m in matches
        ],
        "analyzed_at": result.analyzed_at.isoformat() if result.analyzed_at else None,
    }
