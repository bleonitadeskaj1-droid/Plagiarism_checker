"""Main plagiarism analysis pipeline."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agent.extractors.docx_extractor import extract_docx
from agent.extractors.pdf_extractor import extract_pdf
from agent.nlp.highlighter import highlight_paragraphs
from agent.nlp.preprocessor import chunk_by_words, clean_text, split_paragraphs
from agent.nlp.similarity import compare_documents
from agent.reporter import build_report, save_report
from agent.web_search.searcher import search_paragraph

logger = logging.getLogger(__name__)


def extract_text(file_path: str | Path) -> str:
    path = Path(file_path)
    ext = path.suffix.lower().lstrip(".")
    if ext == "pdf":
        return clean_text(extract_pdf(path))
    if ext == "docx":
        return clean_text(extract_docx(path))
    if ext == "txt":
        return clean_text(path.read_text(encoding="utf-8", errors="ignore"))
    raise ValueError(f"Unsupported file type: {ext}")


def load_compare_documents(paths: list[str | Path]) -> list[dict[str, Any]]:
    docs = []
    for p in paths:
        try:
            text = extract_text(p)
            if len(text) < 50:
                continue
            docs.append({
                "title": Path(p).name,
                "text": text,
                "source_type": "internal",
                "path": str(p),
            })
        except Exception as exc:
            logger.warning("Skip compare doc %s: %s", p, exc)
    return docs


class PlagiarismAnalyzer:
    def analyze(
        self,
        thesis_id: int,
        file_path: str,
        compare_with: list[str] | None = None,
        search_web: bool = True,
        chunk_words: int = 1000,
        reports_dir: str | Path = "backend/reports",
        cache_key: str | None = None,
    ) -> dict[str, Any]:
        source_text = extract_text(file_path)
        paragraphs = split_paragraphs(source_text)
        chunks = chunk_by_words(source_text, chunk_words)

        compare_docs = load_compare_documents(compare_with or [])
        all_matches: list[dict[str, Any]] = []

        for chunk in chunks:
            if compare_docs:
                chunk_matches = compare_documents(chunk, compare_docs)
                all_matches.extend(chunk_matches)

        # Deduplicate by title keeping highest score
        best: dict[str, dict] = {}
        for m in all_matches:
            key = m.get("source_title", "")
            if key not in best or m["similarity"] > best[key]["similarity"]:
                best[key] = m
        details = sorted(best.values(), key=lambda x: x["similarity"], reverse=True)[:15]
        for i, d in enumerate(details, start=1):
            d["rank"] = i

        internal_score = max((d["similarity"] for d in details), default=0.0)

        web_refs: list[dict[str, Any]] = []
        if search_web:
            suspicious = sorted(paragraphs, key=len, reverse=True)[:8]
            try:
                from detection.engine import get_engine
                engine = get_engine()
            except Exception:
                engine = None

            for para in suspicious:
                hits = search_paragraph(para, max_results=2)
                for hit in hits:
                    snippet = hit.get("snippet") or ""
                    if engine:
                        metrics = engine.score_pair(para, snippet, rerank=False)
                        sim = float(metrics["final_score"])
                    else:
                        from agent.nlp.similarity import fuzzy_sentence_score, ngram_overlap
                        sim = max(fuzzy_sentence_score(para, snippet), ngram_overlap(para, snippet))

                    if sim < 12.0:
                        continue

                    web_refs.append({
                        "url": hit["url"],
                        "snippet": snippet,
                        "verified": hit.get("verified", True),
                        "source_type": "web",
                        "source_title": hit["url"],
                        "matched_text": para[:300],
                        "thesis_excerpt": para[:300],
                        "similarity": round(sim, 2),
                        "confidence": "high" if sim >= 50 else "medium" if sim >= 25 else "low",
                    })

        web_score = max((r.get("similarity", 0) for r in web_refs), default=0.0)
        overall = round(max(internal_score, web_score * 0.85), 2)

        highlighted = highlight_paragraphs(paragraphs, details + web_refs)

        report = build_report(
            thesis_id=thesis_id,
            score=overall,
            details=details,
            web_refs=web_refs,
            highlighted=highlighted,
            cache_key=cache_key,
        )
        report_path = save_report(report, reports_dir)
        report["report_path"] = report_path
        return report
