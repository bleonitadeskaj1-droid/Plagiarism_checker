"""Verify web references with strict checks."""

from __future__ import annotations

from agent.web_search.searcher import extract_snippet, verify_url


def verify_reference(url: str, expected_text: str) -> dict:
    ok = verify_url(url)
    if not ok:
        return {"url": url, "verified": False, "reason": "URL not reachable"}
    words = set(w.lower() for w in expected_text.split() if len(w) > 3)
    snippet = extract_snippet(url, words)
    if not snippet:
        return {"url": url, "verified": False, "reason": "No matching snippet found"}
    overlap = len(words & set(snippet.lower().split()))
    if overlap < 3:
        return {"url": url, "verified": False, "reason": "Snippet mismatch", "snippet": snippet[:200]}
    return {"url": url, "verified": True, "snippet": snippet[:500], "confidence": "high" if overlap >= 6 else "medium"}
