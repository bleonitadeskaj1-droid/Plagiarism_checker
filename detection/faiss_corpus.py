"""
FAISS semantic prefilter for large internal/confidential corpora.

Used by the main PlagiarismAgent pipeline to shortlist candidates before
expensive ensemble scoring.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# When corpus exceeds this, FAISS shortlists before score_batch
FAISS_PREFILTER_THRESHOLD = int(os.getenv("FAISS_PREFILTER_THRESHOLD", "20"))
FAISS_TOP_K = int(os.getenv("FAISS_TOP_K", "40"))
MODEL_NAME = os.getenv(
    "FAISS_EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)

_model = None
_model_failed = False


def _load_model():
    global _model, _model_failed
    if _model_failed:
        return None
    if _model is not None:
        return _model
    if os.getenv("DISABLE_ML_MODELS", "").lower() in {"1", "true", "yes"}:
        _model_failed = True
        return None
    try:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading FAISS embedding model: %s", MODEL_NAME)
        _model = SentenceTransformer(MODEL_NAME)
        return _model
    except Exception as exc:
        logger.warning("FAISS model unavailable: %s", exc)
        _model_failed = True
        return None


def _embed(texts: list[str]) -> np.ndarray | None:
    model = _load_model()
    if model is None:
        return None
    try:
        vectors = model.encode(
            [t[:6000] for t in texts],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)
    except Exception as exc:
        logger.warning("FAISS embed failed: %s", exc)
        return None


def semantic_prefilter(
    query_text: str,
    documents: list[dict[str, Any]],
    *,
    top_k: int | None = None,
    min_docs: int | None = None,
) -> list[dict[str, Any]]:
    """
    Return top_k most semantically similar documents.
    Falls back to the full list if FAISS/ML is unavailable or corpus is small.
    """
    threshold = min_docs if min_docs is not None else FAISS_PREFILTER_THRESHOLD
    k = top_k if top_k is not None else FAISS_TOP_K

    if not query_text or not documents:
        return documents
    if len(documents) <= threshold:
        return documents

    try:
        import faiss
    except ImportError:
        logger.debug("faiss-cpu not installed — skipping semantic prefilter")
        return documents

    contents = [(doc.get("content") or "") for doc in documents]
    if not any(contents):
        return documents

    doc_vecs = _embed(contents)
    query_vecs = _embed([query_text])
    if doc_vecs is None or query_vecs is None:
        return documents

    dim = doc_vecs.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(doc_vecs)

    k_eff = min(max(k, 1), len(documents))
    scores, indices = index.search(query_vecs, k_eff)

    selected: list[dict[str, Any]] = []
    seen: set[int] = set()
    for score, idx in zip(scores[0], indices[0]):
        i = int(idx)
        if i < 0 or i >= len(documents) or i in seen:
            continue
        seen.add(i)
        doc = dict(documents[i])
        doc["_faiss_score"] = float(score)
        selected.append(doc)

    if not selected:
        return documents

    logger.info(
        "FAISS prefilter: %s → %s candidates (top_k=%s)",
        len(documents),
        len(selected),
        k_eff,
    )
    return selected
