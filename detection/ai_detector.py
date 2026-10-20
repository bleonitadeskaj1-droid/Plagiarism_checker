"""
Advanced AI-generated text detection — kept separate from classical plagiarism.

Returns a structured report that must NOT be folded into overall plagiarism %.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

AI_MARKERS_EN = (
    "furthermore", "moreover", "in conclusion", "it is important to note",
    "in today's world", "plays a crucial role", "in summary", "overall,",
    "as a result", "this essay", "this paper", "delve into", "landscape of",
    "a myriad of", "leveraging", "underscore the importance", "pivotal role",
    "it is worth noting", "in light of", "facilitate", "comprehensive overview",
)

AI_MARKERS_SQ = (
    "në përgjithësi", "duhet të theksohet", "nga ana tjetër", "në këtë kontekst",
    "në mënyrë të", "në fund të fundit", "është e rëndësishme", "përmes kësaj",
    "në ditët e sotme", "luan një rol", "si përfundim", "në këtë punim",
    "qëllimi i këtij", "ky punim synon", "në këtë kapitull", "në kuadër të",
    "bazuar në sa më sipër", "siç u përmend", "në këtë kontekst mund",
)


def estimate_ai_likelihood(text: str) -> dict[str, Any]:
    """Estimate AI-generation likelihood (0–100) with explainable signals."""
    raw = (text or "").strip()
    if len(raw) < 200:
        return {
            "score": 0.0,
            "detected": False,
            "threshold": 35.0,
            "method": "heuristic_v2",
            "signals": {"reason": "text_too_short"},
            "note": "Teksti është shumë i shkurtër për vlerësim të besueshëm AI.",
        }

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", raw) if len(s.strip()) > 12]
    if len(sentences) < 3:
        return {
            "score": 0.0,
            "detected": False,
            "threshold": 35.0,
            "method": "heuristic_v2",
            "signals": {"reason": "too_few_sentences"},
            "note": "Jo mjaftueshëm fjali për analizë AI.",
        }

    lengths = [len(s) for s in sentences]
    mean_len = sum(lengths) / len(lengths)
    variance = sum((length - mean_len) ** 2 for length in lengths) / len(lengths)
    burstiness = variance / (mean_len + 1)
    length_cv = (math.sqrt(variance) / (mean_len + 1e-6))

    score = 0.0
    signals: dict[str, Any] = {
        "sentence_count": len(sentences),
        "mean_sentence_len": round(mean_len, 1),
        "burstiness": round(burstiness, 2),
        "length_cv": round(length_cv, 3),
    }

    # Uniform sentence lengths → ChatGPT-like
    if burstiness < 120:
        add = min(32.0, (120.0 - burstiness) * 0.35)
        score += add
        signals["uniform_length_bonus"] = round(add, 1)
    if 90 <= mean_len <= 200:
        score += 10.0
        signals["mean_length_band"] = True
    if length_cv < 0.35:
        score += 8.0
        signals["low_length_cv"] = True

    lower = raw.lower()
    en_hits = sum(1 for m in AI_MARKERS_EN if m in lower)
    sq_hits = sum(1 for m in AI_MARKERS_SQ if m in lower)
    marker_bonus = min(30.0, en_hits * 5.5 + sq_hits * 6.5)
    score += marker_bonus
    signals["ai_markers_en"] = en_hits
    signals["ai_markers_sq"] = sq_hits

    words = re.findall(r"\w+", lower, flags=re.UNICODE)
    unique_ratio = len(set(words)) / max(len(words), 1)
    signals["type_token_ratio"] = round(unique_ratio, 3)
    if unique_ratio < 0.38:
        score += 16.0
    elif unique_ratio < 0.45:
        score += 9.0

    # Repeated 3-grams (template phrasing)
    if len(words) >= 60:
        trigrams = [" ".join(words[i : i + 3]) for i in range(len(words) - 2)]
        counts = Counter(trigrams)
        repeated = sum(1 for _, c in counts.items() if c >= 3)
        signals["repeated_trigrams"] = repeated
        score += min(12.0, repeated * 1.5)

    # Punctuation regularity / low exclamation / question variety
    punct = Counter(ch for ch in raw if ch in ".,;:!?")
    signals["punctuation"] = dict(punct)
    if punct.get("!", 0) + punct.get("?", 0) == 0 and len(sentences) >= 8:
        score += 5.0

    # Long fluent paragraphs without academic citations
    paras = [p for p in re.split(r"\n{2,}", raw) if len(p.strip()) > 220]
    has_cites = bool(re.search(r"\[\d+\]|\(\d{4}\)|\bet\s+al\b", raw, flags=re.I))
    signals["long_paragraphs"] = len(paras)
    signals["has_citations"] = has_cites
    if len(paras) >= 2 and not has_cites:
        score += 10.0

    # Paragraph length uniformity
    if len(paras) >= 3:
        plens = [len(p) for p in paras]
        pmean = sum(plens) / len(plens)
        pvar = sum((x - pmean) ** 2 for x in plens) / len(plens)
        pcv = math.sqrt(pvar) / (pmean + 1e-6)
        signals["paragraph_cv"] = round(pcv, 3)
        if pcv < 0.25:
            score += 6.0

    score = round(min(100.0, score), 1)
    threshold = 35.0
    detected = score >= threshold
    note = (
        f"AI i zbuluar ({score}%) — nuk shtohet në totalin e plagjiaturës klasike."
        if detected
        else f"Sinjal i ulët AI ({score}%). Rezultati mbahet i ndarë nga plagjiatura."
    )
    return {
        "score": score,
        "detected": detected,
        "threshold": threshold,
        "method": "heuristic_v2_sq_en",
        "signals": signals,
        "note": note,
    }
