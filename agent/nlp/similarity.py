"""Similarity engines: TF-IDF, n-grams, semantic embeddings, fuzzy matching."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from agent.nlp.preprocessor import normalize

# Allow importing detection engine from project root
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

NGRAM_RE = re.compile(r"\b[\w']+\b", re.UNICODE)

BI_ENCODER_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_semantic_model = None
_semantic_model_failed = False


def _get_semantic_model():
    global _semantic_model, _semantic_model_failed
    if _semantic_model_failed:
        return None
    if _semantic_model is not None:
        return _semantic_model
    try:
        from sentence_transformers import SentenceTransformer

        _semantic_model = SentenceTransformer(BI_ENCODER_MODEL)
        return _semantic_model
    except Exception:
        _semantic_model_failed = True
        return None


def tfidf_cosine(source: str, targets: list[str]) -> list[float]:
    if not source.strip() or not any(t.strip() for t in targets):
        return [0.0] * len(targets)
    corpus = [normalize(source)] + [normalize(t) for t in targets]
    try:
        vec = TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True)
        matrix = vec.fit_transform(corpus)
        scores = cosine_similarity(matrix[0:1], matrix[1:]).flatten()
        return [round(float(s * 100), 2) for s in scores]
    except Exception:
        return [0.0] * len(targets)


def ngram_overlap(source: str, target: str, n: int = 5) -> float:
    src_tokens = NGRAM_RE.findall(source.lower())
    tgt_tokens = NGRAM_RE.findall(target.lower())
    if len(src_tokens) < n or len(tgt_tokens) < n:
        return 0.0
    src_ngrams = {" ".join(src_tokens[i : i + n]) for i in range(len(src_tokens) - n + 1)}
    tgt_ngrams = {" ".join(tgt_tokens[i : i + n]) for i in range(len(tgt_tokens) - n + 1)}
    if not src_ngrams or not tgt_ngrams:
        return 0.0
    overlap = len(src_ngrams & tgt_ngrams) / max(len(src_ngrams), 1)
    return round(overlap * 100, 2)


def fuzzy_sentence_score(source: str, target: str) -> float:
    try:
        from rapidfuzz import fuzz
    except ImportError:
        return 0.0

    src_sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", source) if len(s.strip()) >= 30][:20]
    tgt_sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", target) if len(s.strip()) >= 30][:20]
    if not src_sents or not tgt_sents:
        return 0.0

    hits = [max(fuzz.token_sort_ratio(ss, ts) for ts in tgt_sents) for ss in src_sents]
    strong = [h for h in hits if h >= 55]
    if not strong:
        return 0.0
    return round(sum(strong) / len(strong), 2)


def semantic_similarity(source: str, targets: list[str]) -> list[float]:
    model = _get_semantic_model()
    if model is None:
        return [0.0] * len(targets)
    try:
        embeddings = model.encode([source[:8000]] + [t[:8000] for t in targets], normalize_embeddings=True)
        src_emb = embeddings[0:1]
        tgt_emb = embeddings[1:]
        scores = cosine_similarity(src_emb, tgt_emb).flatten()
        return [round(float(max(0, s) * 100), 2) for s in scores]
    except Exception:
        return [0.0] * len(targets)


def combined_score(tfidf: float, ngram: float, semantic: float, fuzzy: float = 0.0) -> float:
    if semantic > 0:
        return round(tfidf * 0.20 + ngram * 0.15 + semantic * 0.45 + fuzzy * 0.20, 2)
    return round(tfidf * 0.45 + ngram * 0.35 + fuzzy * 0.20, 2)


def compare_documents(source: str, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare source text against documents using the unified detection engine when available."""
    try:
        from detection.engine import get_engine

        engine = get_engine()
        docs = [{"content": d.get("text", ""), **d} for d in documents]
        scored = engine.score_batch(source, docs, rerank_top_k=3)
        results = []
        for item in scored:
            score = float(item["final_score"])
            if score < 5:
                continue
            doc = item["doc"]
            results.append({
                "rank": 0,
                "source_type": doc.get("source_type", "internal"),
                "source_title": doc.get("title", "Document"),
                "source_url": doc.get("url"),
                "matched_text": item.get("source_excerpt", source[:300]),
                "thesis_excerpt": item.get("thesis_excerpt", source[:300]),
                "similarity": score,
                "tfidf": item.get("tfidf_score", 0.0),
                "ngram": item.get("jaccard_score", 0.0),
                "semantic": item.get("semantic_score", 0.0),
                "fuzzy": item.get("fuzzy_score", 0.0),
                "confidence": item.get("confidence", "medium"),
            })
        results.sort(key=lambda x: x["similarity"], reverse=True)
        for i, item in enumerate(results, start=1):
            item["rank"] = i
        return results
    except Exception:
        pass

    # Fallback: lightweight lexical scoring
    texts = [d.get("text", "") for d in documents]
    tfidf_scores = tfidf_cosine(source, texts)
    semantic_scores = semantic_similarity(source[:8000], [t[:8000] for t in texts])

    results = []
    for idx, doc in enumerate(documents):
        ngram = ngram_overlap(source, texts[idx])
        fuzzy = fuzzy_sentence_score(source, texts[idx])
        score = combined_score(tfidf_scores[idx], ngram, semantic_scores[idx], fuzzy)
        if score < 5:
            continue
        results.append({
            "rank": 0,
            "source_type": doc.get("source_type", "internal"),
            "source_title": doc.get("title", "Document"),
            "source_url": doc.get("url"),
            "matched_text": source[:300],
            "similarity": score,
            "tfidf": tfidf_scores[idx],
            "ngram": ngram,
            "semantic": semantic_scores[idx],
            "fuzzy": fuzzy,
            "confidence": "high" if score >= 40 else "medium" if score >= 20 else "low",
        })

    results.sort(key=lambda x: x["similarity"], reverse=True)
    for i, item in enumerate(results, start=1):
        item["rank"] = i
    return results


def document_fingerprint(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()
