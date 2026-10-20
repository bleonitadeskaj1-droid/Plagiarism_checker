"""Highlight suspicious paragraphs."""

from __future__ import annotations

from typing import Any


def highlight_paragraphs(paragraphs: list[str], matches: list[dict[str, Any]], threshold: float = 20.0) -> list[dict[str, Any]]:
    highlighted = []
    for idx, paragraph in enumerate(paragraphs):
        best = 0.0
        best_match = None
        for match in matches:
            excerpt = match.get("matched_text") or match.get("text") or ""
            if not excerpt:
                continue
            if excerpt[:80] in paragraph or paragraph[:80] in excerpt:
                score = float(match.get("similarity", 0))
                if score > best:
                    best = score
                    best_match = match
        if best >= threshold:
            highlighted.append({
                "paragraph_index": idx,
                "text": paragraph,
                "similarity": best,
                "source_title": best_match.get("source_title") if best_match else None,
                "source_url": best_match.get("source_url") if best_match else None,
                "confidence": best_match.get("confidence", "medium") if best_match else "low",
            })
    return highlighted
