"""Agent tools for text analysis and plagiarism detection."""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.services.plagiarism_service import get_plagiarism_service
from app.services.text_processing import (
    count_words,
    detect_language_simple,
    extract_key_sentences,
)

logger = logging.getLogger(__name__)


def analyze_text(text: str) -> dict[str, Any]:
    """Extract entities, key sentences, language, and word count."""
    words = count_words(text)
    language = detect_language_simple(text)
    key_sentences = extract_key_sentences(text)
    entities = list({m.group() for m in re.finditer(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", text[:5000])})
    return {
        "word_count": words,
        "language": language,
        "key_sentences": key_sentences,
        "entities": entities[:10],
    }


def search_similar_documents(session: Session, text: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Search existing documents for semantic similarity."""
    service = get_plagiarism_service()
    matches = service.search_similar(session, text, top_k=top_k)
    return [
        {
            "document_id": m.document_id,
            "title": m.title,
            "similarity": m.similarity,
            "excerpt": m.excerpt,
        }
        for m in matches
    ]


def compare_texts(text1: str, text2: str) -> dict[str, Any]:
    """Return similarity score, identical paragraphs, and report."""
    service = get_plagiarism_service()
    return service.compare_texts(text1, text2)


def search_web_sources(query: str) -> list[dict[str, Any]]:
    """Optional web search for potential plagiarism sources."""
    try:
        from langchain_community.tools import DuckDuckGoSearchRun

        search = DuckDuckGoSearchRun()
        result = search.run(query)
        return [{"source": "web", "query": query, "snippet": result[:500]}]
    except Exception as exc:
        logger.warning("Web search unavailable: %s", exc)
        return [{"source": "web", "query": query, "snippet": "Web search is not configured."}]
