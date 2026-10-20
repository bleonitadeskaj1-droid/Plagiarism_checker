"""
Plagiarism Detection Service - Local analysis service using PlagiarismAgent.

This service centralizes plagiarism analysis, document extraction,
result persistence, and compatibility with the existing FastAPI endpoints.
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any, Dict, List, Optional

from confidential import decrypt_content
from document_processor import DocumentProcessor
from sqlalchemy.orm import joinedload
from sqlalchemy import or_
from services.thesis_text import clear_thesis_text_cache, get_thesis_text
from services.admin_settings_service import get_plagiarism_threshold
from models import (
    ConfidentialDocument,
    DocStatus,
    PlagiarismMatch,
    PlagiarismResult,
    Review,
    SubmissionWorkflowStatus,
    Thesis,
    ThesisStatus,
    UploadedFile,
)
from plagiarism_agent import PlagiarismAgent

logger = logging.getLogger(__name__)


_AUTO_REVIEW_PREFIXES = (
    "Tema u aprovua automatikisht",
    "Tema kërkon rishikim: ngjashmëria",
)


def _is_auto_review(review: Optional[Review]) -> bool:
    if review is None:
        return False
    comments = (review.comments or "").strip()
    return comments.startswith(_AUTO_REVIEW_PREFIXES)


def apply_analysis_verdict(db, thesis: Thesis, overall_score: float) -> dict:
    """Pas analizës: ruaj skorin. Vendimin final e jep gjithmonë profesori.

    Edhe në 0% plagjiaturë tema mbetet në pritje derisa profesori aprovon,
    kërkon rishikim ose refuzon.
    """
    threshold = get_plagiarism_threshold(db)
    score = float(overall_score or 0)

    thesis.status = ThesisStatus.flagged if score > threshold else ThesisStatus.completed

    review = db.query(Review).filter(Review.thesis_id == thesis.id).first()
    if _is_auto_review(review):
        db.delete(review)
        thesis.workflow_status = SubmissionWorkflowStatus.in_process
    elif review is not None:
        review.plagiarism_percentage = score
    elif thesis.workflow_status not in (
        SubmissionWorkflowStatus.approved,
        SubmissionWorkflowStatus.rejected,
        SubmissionWorkflowStatus.needs_revision,
    ):
        thesis.workflow_status = SubmissionWorkflowStatus.in_process

    return {"auto_approved": False, "overall_score": score, "threshold": threshold}


class PlagiarismDetectionService:
    """Local plagiarism service layer using PlagiarismAgent."""

    MAX_CORPUS_DOCS = 50
    MAX_DOC_CHARS = 150_000
    ANALYSIS_TIMEOUT_SEC = 300

    def __init__(
        self,
        vector_db_path: Optional[str] = None,
        reports_path: Optional[str] = None,
        enable_rag: bool = False,
        enable_caching: bool = False,
    ):
        self.vector_db_path = Path(vector_db_path) if vector_db_path else Path("./vector_db")
        self.reports_path = Path(reports_path) if reports_path else Path("./reports")
        self.enable_rag = enable_rag
        self.enable_caching = enable_caching

        self.agent = PlagiarismAgent()
        self.processor = DocumentProcessor()

        self._cache: Dict[str, Any] = {}

        logger.info("PlagiarismDetectionService initialized with PlagiarismAgent.")

    def analyze_text(
        self,
        text: str,
        thesis_title: str,
        internal_documents: Optional[List[Dict[str, str]]] = None,
        confidential_documents: Optional[List[Dict[str, str]]] = None,
        generate_report: bool = False,
        search_web: bool = False,
        language: Optional[str] = None,
    ) -> Dict[str, Any]:
        start = time.perf_counter()
        internal_documents = internal_documents or []
        confidential_documents = confidential_documents or []

        if not text or not text.strip():
            return {"error": "Text is empty", "success": False}

        try:
            analysis = self.agent.analyze_with_confidential(
                thesis_text=text,
                thesis_title=thesis_title,
                public_theses=internal_documents,
                conf_docs=confidential_documents,
                search_web=search_web,
            )

            elapsed_ms = int((time.perf_counter() - start) * 1000)
            response = {
                "overall_score": float(analysis.get("overall_score", 0.0)),
                "internal_score": float(analysis.get("internal_score", 0.0)),
                "confidential_score": float(analysis.get("confidential_score", 0.0)),
                "web_score": float(analysis.get("web_score", 0.0)),
                "ai_likelihood_score": float(analysis.get("ai_likelihood_score", 0.0)),
                "risk_level": analysis.get("risk_level", "unknown"),
                "summary": analysis.get("summary", ""),
                "recommendations": analysis.get("recommendations", ""),
                "matches": analysis.get("flagged_sections", []),
                "internal_sources": analysis.get("internal_sources", []),
                "web_sources": analysis.get("web_sources", []),
                "report_path": None,
                "confidence_score": float(analysis.get("overall_score", 0.0)),
                "language": language or analysis.get("metadata", {}).get("language", "unknown"),
                "execution_time_ms": elapsed_ms,
                "analysis_id": None,
            }

            if generate_report:
                response["report_path"] = None

            return response

        except Exception as exc:
            logger.error("Text analysis failed: %s", exc, exc_info=True)
            return {"error": str(exc), "success": False}

    def analyze_document(
        self,
        document_path: Path,
        thesis_title: Optional[str] = None,
        internal_documents: Optional[List[Dict[str, str]]] = None,
        confidential_documents: Optional[List[Dict[str, str]]] = None,
        generate_report: bool = False,
        search_web: bool = False,
    ) -> Dict[str, Any]:
        if not document_path.exists():
            return {"error": "Document file not found", "success": False}

        try:
            text, metadata = self.processor.extract_text(document_path)
        except Exception as exc:
            logger.error("Document extraction failed: %s", exc, exc_info=True)
            return {"error": str(exc), "success": False}

        if not text.strip():
            return {"error": "Document contains no extractable text", "success": False}

        result = self.analyze_text(
            text=text,
            thesis_title=thesis_title or document_path.stem,
            internal_documents=internal_documents,
            confidential_documents=confidential_documents,
            generate_report=generate_report,
            search_web=search_web,
            language=metadata.get("language", "unknown"),
        )

        result["document_metadata"] = metadata
        return result

    def _truncate_doc_text(self, text: str) -> str:
        """Keep representative samples from long docs (start + middle + end)."""
        text = (text or "").strip()
        if len(text) <= self.MAX_DOC_CHARS:
            return text
        part = self.MAX_DOC_CHARS // 3
        mid = max(0, len(text) // 2 - part // 2)
        return (
            text[:part]
            + "\n\n"
            + text[mid : mid + part]
            + "\n\n"
            + text[-part:]
        )

    def mark_analysis_error(self, db, thesis_id: int, result_id: int, message: str) -> None:
        """Mark result as failed so UI stops polling."""
        try:
            result = db.query(PlagiarismResult).filter(PlagiarismResult.id == result_id).first()
            thesis = db.query(Thesis).filter(Thesis.id == thesis_id).first()
            if result:
                result.status = "error"
                result.ai_analysis = json.dumps({"error": message}, ensure_ascii=False)
            if thesis:
                thesis.status = ThesisStatus.pending
            db.commit()
        except Exception as exc:
            logger.error("Could not mark analysis error: %s", exc)
            db.rollback()

    def load_analysis_plan(
        self,
        db,
        thesis_id: int,
        result_id: int,
        compare_confidential: bool = True,
        compare_internal: bool = True,
        search_web: bool = True,
        department_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Quick DB read — no PDF extraction (keeps connection time minimal)."""
        thesis = (
            db.query(Thesis)
            .options(joinedload(Thesis.uploaded_files))
            .filter(Thesis.id == thesis_id)
            .first()
        )
        result = db.query(PlagiarismResult).filter(PlagiarismResult.id == result_id).first()
        if not thesis:
            raise ValueError("Thesis not found")
        if not result:
            raise ValueError("Result record not found")

        thesis_content = (thesis.content or "").strip()
        thesis_files = [
            {"file_path": uf.file_path}
            for uf in (thesis.uploaded_files or [])
        ]

        internal_candidates: List[Dict[str, Any]] = []
        if compare_internal:
            for candidate in (
                db.query(Thesis)
                .options(joinedload(Thesis.uploaded_files))
                .filter(
                    Thesis.id != thesis_id,
                    Thesis.workflow_status != SubmissionWorkflowStatus.rejected,
                )
                .order_by(Thesis.is_archive.desc(), Thesis.id.desc())
                .limit(self.MAX_CORPUS_DOCS)
                .all()
            ):
                internal_candidates.append(
                    {
                        "id": candidate.id,
                        "title": candidate.title,
                        "content": (candidate.content or "").strip(),
                        "file_path": candidate.file_path,
                        "uploaded_files": [
                            {"file_path": uf.file_path}
                            for uf in (candidate.uploaded_files or [])
                        ],
                        "is_archive": bool(getattr(candidate, "is_archive", False)),
                    }
                )

        confidential_docs: List[Dict[str, Any]] = []
        if compare_confidential:
            confidential_query = db.query(ConfidentialDocument).filter(
                ConfidentialDocument.status == DocStatus.active
            )
            if department_filter:
                confidential_query = confidential_query.filter(
                    ConfidentialDocument.department == department_filter
                )
            for doc in confidential_query.limit(20).all():
                confidential_docs.append(
                    {
                        "id": doc.id,
                        "title": doc.title,
                        "encrypted_content": doc.encrypted_content,
                    }
                )

        return {
            "thesis_id": thesis_id,
            "result_id": result_id,
            "thesis_title": thesis.title,
            "thesis_content": thesis_content,
            "thesis_file_path": thesis.file_path,
            "thesis_uploaded_files": thesis_files,
            "internal_candidates": internal_candidates,
            "confidential_docs": confidential_docs,
            "search_web": search_web,
        }

    def _resolve_text_from_plan(self, plan: Dict[str, Any]) -> str:
        content = (plan.get("thesis_content") or "").strip()
        if content:
            return content
        stub = Thesis(
            id=plan["thesis_id"],
            title=plan.get("thesis_title") or "",
            content="",
            file_path=plan.get("thesis_file_path"),
        )
        stub.uploaded_files = [
            UploadedFile(file_path=f["file_path"])
            for f in (plan.get("thesis_uploaded_files") or [])
        ]
        return get_thesis_text(stub, self.processor, use_cache=False)

    def build_payload_from_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Load file texts and build analysis payload — no DB connection."""
        clear_thesis_text_cache()
        thesis_content = self._resolve_text_from_plan(plan)
        if not thesis_content:
            raise ValueError("Tema nuk ka tekst të lexueshëm për analizë")

        internal_documents: List[Dict[str, Any]] = []
        for cand in plan.get("internal_candidates") or []:
            text = (cand.get("content") or "").strip()
            if not text:
                stub = Thesis(
                    id=cand["id"],
                    title=cand.get("title") or "",
                    content="",
                    file_path=cand.get("file_path"),
                )
                stub.uploaded_files = [
                    UploadedFile(file_path=f["file_path"])
                    for f in (cand.get("uploaded_files") or [])
                ]
                text = get_thesis_text(stub, self.processor)
            text = self._truncate_doc_text(text)
            if not text:
                continue
            internal_documents.append(
                {
                    "id": cand["id"],
                    "title": cand["title"],
                    "content": text,
                    "is_archive": bool(cand.get("is_archive")),
                }
            )

        logger.info(
            "Internal corpus for thesis %s: %s docs (%s archive)",
            plan["thesis_id"],
            len(internal_documents),
            sum(1 for d in internal_documents if d.get("is_archive")),
        )

        confidential_documents: List[Dict[str, Any]] = []
        for doc in plan.get("confidential_docs") or []:
            try:
                plain_text = self._truncate_doc_text(decrypt_content(doc["encrypted_content"]))
                confidential_documents.append(
                    {
                        "id": doc["id"],
                        "title": doc["title"],
                        "source_url": None,
                        "content": plain_text,
                    }
                )
            except Exception as exc:
                logger.warning("Skipping confidential document %s: %s", doc.get("id"), exc)

        return {
            "thesis_id": plan["thesis_id"],
            "result_id": plan["result_id"],
            "thesis_title": plan["thesis_title"],
            "thesis_content": thesis_content,
            "internal_documents": internal_documents,
            "confidential_documents": confidential_documents,
            "search_web": plan.get("search_web", True),
        }

    def gather_analysis_payload(
        self,
        db,
        thesis_id: int,
        result_id: int,
        compare_confidential: bool = True,
        compare_internal: bool = True,
        search_web: bool = True,
        department_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Load all inputs — DB phase is short; PDF extraction runs without DB."""
        plan = self.load_analysis_plan(
            db,
            thesis_id=thesis_id,
            result_id=result_id,
            compare_confidential=compare_confidential,
            compare_internal=compare_internal,
            search_web=search_web,
            department_filter=department_filter,
        )
        return self.build_payload_from_plan(plan)

    def execute_analysis(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """CPU/network heavy step — no DB connection held."""
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                self.agent.analyze_with_confidential,
                thesis_text=payload["thesis_content"],
                thesis_title=payload["thesis_title"],
                public_theses=payload["internal_documents"],
                conf_docs=payload["confidential_documents"],
                search_web=bool(payload.get("search_web")),
            )
            try:
                return future.result(timeout=self.ANALYSIS_TIMEOUT_SEC)
            except FuturesTimeoutError as exc:
                raise TimeoutError(
                    f"Analiza kaloi {self.ANALYSIS_TIMEOUT_SEC}s. "
                    "Provo pa kërkim web ose me dokument më të shkurtër."
                ) from exc

    def persist_analysis_result(
        self,
        db,
        thesis_id: int,
        result_id: int,
        analysis: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Save analysis outputs to DB."""
        thesis = db.query(Thesis).filter(Thesis.id == thesis_id).first()
        result = db.query(PlagiarismResult).filter(PlagiarismResult.id == result_id).first()
        if not thesis or not result:
            raise ValueError("Thesis or result not found")

        result.overall_score = float(analysis.get("overall_score", 0.0))
        result.internal_score = float(analysis.get("internal_score", 0.0))
        result.confidential_score = float(analysis.get("confidential_score", 0.0))
        result.web_score = float(analysis.get("web_score", 0.0))
        result.ai_analysis = json.dumps(analysis, ensure_ascii=False)
        result.status = "completed"

        db.query(PlagiarismMatch).filter(PlagiarismMatch.result_id == result.id).delete(synchronize_session=False)

        flagged_sections = analysis.get("flagged_sections", [])
        matches = []
        for section in flagged_sections:
            match_thesis_id = section.get("thesis_id")
            source_type = section.get("source_type", "internal")
            source_url = section.get("source_url")
            if source_type == "internal" and match_thesis_id and not source_url:
                source_url = f"internal://thesis/{match_thesis_id}"

            matches.append(
                PlagiarismMatch(
                    result_id=result.id,
                    source_type=source_type,
                    conf_source_id=section.get("conf_source_id"),
                    source_url=source_url,
                    source_title=section.get("source_title", section.get("source", "")),
                    original_text=section.get("matched_text") or section.get("original_text", ""),
                    similarity_score=float(section.get("similarity", 0.0)),
                    paragraph_index=section.get("paragraph_index", 0),
                )
            )

        db.add_all(matches)

        overall_score = float(analysis.get("overall_score", 0.0))
        verdict = apply_analysis_verdict(db, thesis, overall_score)
        analysis["auto_approved"] = verdict["auto_approved"]

        db.commit()
        return analysis

    def process_analysis_result(
        self,
        thesis_id: int,
        result_id: int,
        db,
        compare_confidential: bool = True,
        compare_internal: bool = True,
        search_web: bool = True,
        department_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Legacy single-session entry — prefer gather/execute/persist via run_analysis."""
        payload = self.gather_analysis_payload(
            db, thesis_id, result_id,
            compare_confidential=compare_confidential,
            compare_internal=compare_internal,
            search_web=search_web,
            department_filter=department_filter,
        )
        analysis = self.execute_analysis(payload)
        return self.persist_analysis_result(db, thesis_id, result_id, analysis)

    def save_vector_db(self, path: Optional[str] = None) -> Path:
        save_path = Path(path) if path else self.vector_db_path
        logger.warning("Vector DB support is not available in local PlagiarismDetectionService")
        return save_path

    def load_vector_db(self, path: Optional[str] = None) -> None:
        load_path = Path(path) if path else self.vector_db_path
        logger.warning("Vector DB loading is not available in local PlagiarismDetectionService: %s", load_path)

    def get_vector_db_stats(self) -> Dict[str, Any]:
        return {"enabled": False, "message": "No vector DB configured"}

    def clear_cache(self) -> None:
        self._cache.clear()
        logger.info("Local plagiarism service cache cleared")

    def get_system_stats(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "service": "plagiarism_agent",
            "caching_enabled": self.enable_caching,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def compare_theses(self, thesis_a: Thesis, thesis_b: Thesis) -> Dict[str, Any]:
        """Compare two theses by extracted text (local, no external API)."""
        text_a = get_thesis_text(thesis_a, self.processor)
        text_b = get_thesis_text(thesis_b, self.processor)
        if not text_a.strip() or not text_b.strip():
            return {"error": "Njëra ose të dyja temat nuk kanë tekst të lexueshëm", "success": False}
        return {
            "success": True,
            **self.agent.compare_two_texts(text_a, text_b, thesis_a.title, thesis_b.title),
            "thesis_a_id": thesis_a.id,
            "thesis_b_id": thesis_b.id,
        }
