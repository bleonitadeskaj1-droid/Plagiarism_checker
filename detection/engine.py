"""
Unified high-accuracy plagiarism detection engine.

Combines:
- Multilingual semantic embeddings (sentence-transformers)
- Cross-encoder reranking for paraphrase detection
- Chunk-level alignment for localized copy-paste
- TF-IDF, Jaccard shingles, SequenceMatcher (lexical baseline)
- RapidFuzz fuzzy matching at sentence/fragment level
"""

from __future__ import annotations

import logging
import os
import re
from difflib import SequenceMatcher
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

# Multilingual models (Albanian + English). Default MiniLM stays memory-safe;
# set PLAGIARISM_BI_ENCODER=sentence-transformers/paraphrase-multilingual-mpnet-base-v2 for higher accuracy.
BI_ENCODER_MODEL = os.getenv(
    "PLAGIARISM_BI_ENCODER",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
CROSS_ENCODER_MODEL = os.getenv(
    "PLAGIARISM_CROSS_ENCODER",
    "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
)

# Ensemble weights (sum = 1.0) — higher cross-encoder weight for paraphrase
WEIGHT_SEMANTIC = 0.40
WEIGHT_CROSS = 0.22
WEIGHT_TFIDF = 0.14
WEIGHT_JACCARD = 0.09
WEIGHT_SEQUENCE = 0.08
WEIGHT_FUZZY = 0.07

_bi_encoder = None
_bi_encoder_failed = False
_cross_encoder = None
_cross_encoder_failed = False


def _load_bi_encoder():
    global _bi_encoder, _bi_encoder_failed
    if _bi_encoder_failed:
        return None
    if _bi_encoder is not None:
        return _bi_encoder
    import os
    if os.getenv("DISABLE_ML_MODELS", "").lower() in {"1", "true", "yes"}:
        logger.info("ML models disabled via DISABLE_ML_MODELS")
        _bi_encoder_failed = True
        return None
    try:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading bi-encoder: %s", BI_ENCODER_MODEL)
        _bi_encoder = SentenceTransformer(BI_ENCODER_MODEL)
        return _bi_encoder
    except MemoryError as exc:
        logger.warning("Bi-encoder OOM (%s); using lexical scoring only.", exc)
        _bi_encoder_failed = True
        return None
    except Exception as exc:
        logger.warning("Bi-encoder unavailable (%s); falling back to lexical scoring.", exc)
        _bi_encoder_failed = True
        return None


def _load_cross_encoder():
    global _cross_encoder, _cross_encoder_failed
    if _cross_encoder_failed:
        return None
    if _cross_encoder is not None:
        return _cross_encoder
    import os
    if os.getenv("DISABLE_ML_MODELS", "").lower() in {"1", "true", "yes"}:
        _cross_encoder_failed = True
        return None
    try:
        from sentence_transformers import CrossEncoder

        logger.info("Loading cross-encoder: %s", CROSS_ENCODER_MODEL)
        _cross_encoder = CrossEncoder(CROSS_ENCODER_MODEL)
        return _cross_encoder
    except MemoryError as exc:
        logger.warning("Cross-encoder OOM (%s); skipping reranking.", exc)
        _cross_encoder_failed = True
        return None
    except Exception as exc:
        logger.warning("Cross-encoder unavailable (%s); skipping reranking.", exc)
        _cross_encoder_failed = True
        return None


def _normalize(text: str) -> str:
    """Whitespace normalize + Albanian diacritic folding for lexical robustness."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    try:
        from agent.nlp.preprocessor import fold_albanian
        return fold_albanian(text)
    except Exception:
        return text


def _sample_windows(text: str, window: int = 18_000, max_windows: int = 3) -> list[str]:
    """Return up to 3 overlapping windows so long docs are compared fairly."""
    normalized = _normalize(text)
    if not normalized:
        return []
    if len(normalized) <= window:
        return [normalized]
    if max_windows <= 1:
        return [normalized[:window]]
    step = max(1, (len(normalized) - window) // (max_windows - 1))
    windows: list[str] = []
    for i in range(max_windows):
        start = min(i * step, max(0, len(normalized) - window))
        chunk = normalized[start : start + window]
        if chunk and chunk not in windows:
            windows.append(chunk)
    return windows or [normalized[:window]]


def _max_window_score(source: str, target: str, scorer) -> float:
    src_windows = _sample_windows(source)
    tgt_windows = _sample_windows(target)
    best = 0.0
    for sw in src_windows:
        for tw in tgt_windows:
            best = max(best, float(scorer(sw, tw)))
    return best


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\b\w+\b", _normalize(text).lower())


def _split_chunks(text: str, min_words: int = 80, max_words: int = 220) -> list[str]:
    normalized = _normalize(text)
    if not normalized:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n{2,}", normalized) if p.strip()]
    if not paragraphs:
        paragraphs = [normalized]

    chunks: list[str] = []
    current: list[str] = []
    word_count = 0

    for para in paragraphs:
        para_words = len(re.findall(r"\b\w+\b", para))
        if current and word_count + para_words > max_words:
            if word_count >= min_words:
                chunks.append(" ".join(current))
            current = [para]
            word_count = para_words
        else:
            current.append(para)
            word_count += para_words

    if current:
        chunks.append(" ".join(current))

    return chunks or [normalized]


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", _normalize(text))
    return [p.strip() for p in parts if len(p.strip()) >= 30]


class SimilarityEngine:
    """Multi-signal plagiarism scoring engine."""

    SHINGLE_SIZE = 5

    def score_pair(self, source: str, target: str, rerank: bool = True) -> dict[str, Any]:
        """Score similarity between two document texts (0–100)."""
        source = source or ""
        target = target or ""
        if not source.strip() or not target.strip():
            return self._empty_score()

        semantic = self._semantic_chunk_score(source, target)
        cross = self._cross_encoder_score(source, target) if rerank else 0.0
        tfidf = _max_window_score(source, target, self._tfidf_score_pair)
        jaccard = _max_window_score(source, target, self._jaccard_score_pair)
        sequence = _max_window_score(source, target, self._sequence_score_pair)
        fuzzy = self._fuzzy_sentence_score(source, target)

        # If ML unavailable, redistribute semantic/cross weights to lexical
        if semantic <= 0 and cross <= 0:
            final = round(
                tfidf * 0.40 + jaccard * 0.30 + sequence * 0.20 + fuzzy * 0.10,
                2,
            )
        else:
            final = round(
                semantic * WEIGHT_SEMANTIC
                + cross * WEIGHT_CROSS
                + tfidf * WEIGHT_TFIDF
                + jaccard * WEIGHT_JACCARD
                + sequence * WEIGHT_SEQUENCE
                + fuzzy * WEIGHT_FUZZY,
                2,
            )

        # Boost when multiple signals agree strongly
        strong_signals = sum(1 for s in (semantic, tfidf, jaccard, sequence, fuzzy) if s >= 60)
        if strong_signals >= 2:
            final = round(min(100.0, final * 1.05), 2)

        thesis_excerpt, source_excerpt = self._best_matching_fragments(source, target)

        return {
            "final_score": final,
            "semantic_score": round(semantic, 2),
            "cross_encoder_score": round(cross, 2),
            "tfidf_score": round(tfidf, 2),
            "jaccard_score": round(jaccard, 2),
            "sequence_score": round(sequence, 2),
            "fuzzy_score": round(fuzzy, 2),
            "thesis_excerpt": thesis_excerpt,
            "source_excerpt": source_excerpt,
            "confidence": self._confidence(final, semantic, cross, tfidf),
            "method": "ensemble:semantic+cross+tfidf+jaccard+sequence+fuzzy",
        }

    def score_batch(
        self,
        source: str,
        documents: list[dict[str, Any]],
        rerank_top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Score source against multiple candidate documents."""
        if not source.strip() or not documents:
            return []

        # Fast lexical pre-filter to avoid encoding every document fully
        prelim: list[tuple[int, dict, float]] = []
        for idx, doc in enumerate(documents):
            content = doc.get("content") or doc.get("text") or ""
            if not content.strip():
                continue
            quick = max(
                _max_window_score(source, content, self._tfidf_score_pair),
                _max_window_score(source, content, self._jaccard_score_pair),
            )
            prelim.append((idx, doc, quick))

        prelim.sort(key=lambda x: x[2], reverse=True)
        candidates = prelim[: max(rerank_top_k * 3, 12)]

        results: list[dict[str, Any]] = []
        for idx, doc, _ in candidates:
            content = doc.get("content") or doc.get("text") or ""
            # Lexical-only pass first — semantic/cross only on top matches
            metrics = self.score_pair(source, content, rerank=False)
            metrics["doc_index"] = idx
            metrics["doc"] = doc
            results.append(metrics)

        results.sort(key=lambda r: r["final_score"], reverse=True)

        # Cross-encoder rerank top candidates only (skip if ML disabled)
        if results and _load_cross_encoder() is not None:
            top = results[:rerank_top_k]
            for item in top:
                content = item["doc"].get("content") or item["doc"].get("text") or ""
                cross = self._cross_encoder_score(source, content)
                if cross > 0:
                    item["cross_encoder_score"] = cross
                    item["final_score"] = round(
                        min(
                            100.0,
                            item["final_score"] * 0.85 + cross * 0.15,
                        ),
                        2,
                    )

        # Semantic boost for top matches (memory-safe: top 5, min score 5)
        if results and _load_bi_encoder() is not None:
            for item in results[:5]:
                if item["final_score"] < 5:
                    continue
                content = item["doc"].get("content") or item["doc"].get("text") or ""
                semantic = self._semantic_chunk_score(source, content)
                if semantic > 0:
                    item["semantic_score"] = semantic
                    item["final_score"] = round(
                        min(100.0, item["final_score"] * 0.72 + semantic * 0.28),
                        2,
                    )

        results.sort(key=lambda r: r["final_score"], reverse=True)
        return results

    def _semantic_chunk_score(self, source: str, target: str) -> float:
        model = _load_bi_encoder()
        if model is None:
            return 0.0

        chunks_a = _split_chunks(source)
        chunks_b = _split_chunks(target)
        if not chunks_a or not chunks_b:
            return 0.0

        try:
            emb_a = model.encode(chunks_a[:6], normalize_embeddings=True, show_progress_bar=False)
            emb_b = model.encode(chunks_b[:6], normalize_embeddings=True, show_progress_bar=False)
            matrix = cosine_similarity(emb_a, emb_b)
            matrix = np.clip(matrix, 0.0, 1.0)

            matched: list[float] = []
            used_rows: set[int] = set()
            used_cols: set[int] = set()
            work = matrix.copy()

            for _ in range(min(work.shape[0], work.shape[1], 8)):
                best_idx = np.unravel_index(np.argmax(work), work.shape)
                score = float(work[best_idx])
                if score < 0.40:
                    break
                row, col = best_idx
                if row in used_rows or col in used_cols:
                    work[row, :] = -1.0
                    work[:, col] = -1.0
                    continue
                used_rows.add(row)
                used_cols.add(col)
                matched.append(score)
                work[row, :] = -1.0
                work[:, col] = -1.0

            if not matched:
                return 0.0

            avg = float(np.mean(matched)) * 100.0
            coverage = min(len(matched) / max(len(chunks_a), len(chunks_b)), 1.0)
            return round(avg * 0.72 + coverage * 100.0 * 0.28, 2)
        except Exception as exc:
            logger.warning("Semantic chunk scoring failed: %s", exc)
            return 0.0

    def _cross_encoder_score(self, source: str, target: str) -> float:
        model = _load_cross_encoder()
        if model is None:
            return 0.0

        # Compare best-matching paragraph pairs
        src_paras = _split_chunks(source, min_words=40, max_words=180)[:6]
        tgt_paras = _split_chunks(target, min_words=40, max_words=180)[:6]
        if not src_paras or not tgt_paras:
            src_paras = [_normalize(source)[:1500]]
            tgt_paras = [_normalize(target)[:1500]]

        pairs = []
        for sp in src_paras:
            for tp in tgt_paras:
                pairs.append((sp[:512], tp[:512]))

        if not pairs:
            return 0.0

        try:
            scores = model.predict(pairs[:12])
            best = float(np.max(scores))
            # Cross-encoder outputs logits; sigmoid to 0–100
            normalized = 100.0 / (1.0 + np.exp(-best))
            return round(float(np.clip(normalized, 0.0, 100.0)), 2)
        except Exception as exc:
            logger.warning("Cross-encoder scoring failed: %s", exc)
            return 0.0

    def _tfidf_score_pair(self, source: str, target: str) -> float:
        s, t = _normalize(source), _normalize(target)
        if not s or not t:
            return 0.0
        try:
            vec = TfidfVectorizer(
                lowercase=True,
                ngram_range=(1, 2),
                sublinear_tf=True,
                min_df=1,
                max_df=0.95,
            )
            matrix = vec.fit_transform([s, t])
            score = cosine_similarity(matrix[0:1], matrix[1:])[0, 0]
            return round(float(np.clip(score, 0.0, 1.0)) * 100.0, 2)
        except Exception:
            return 0.0

    def _tfidf_score(self, source: str, target: str) -> float:
        return _max_window_score(source, target, self._tfidf_score_pair)

    def _jaccard_score_pair(self, source: str, target: str) -> float:
        tokens_a = _tokenize(source)
        tokens_b = _tokenize(target)
        n = self.SHINGLE_SIZE
        if len(tokens_a) < n or len(tokens_b) < n:
            return 0.0
        shingles_a = {" ".join(tokens_a[i : i + n]) for i in range(len(tokens_a) - n + 1)}
        shingles_b = {" ".join(tokens_b[i : i + n]) for i in range(len(tokens_b) - n + 1)}
        if not shingles_a or not shingles_b:
            return 0.0
        intersection = shingles_a & shingles_b
        union = shingles_a | shingles_b
        return round(len(intersection) / len(union) * 100.0, 2)

    def _jaccard_score(self, source: str, target: str) -> float:
        return _max_window_score(source, target, self._jaccard_score_pair)

    def _sequence_score_pair(self, source: str, target: str) -> float:
        s, t = _normalize(source)[:12000], _normalize(target)[:12000]
        if not s or not t:
            return 0.0
        return round(SequenceMatcher(None, s, t).ratio() * 100.0, 2)

    def _sequence_score(self, source: str, target: str) -> float:
        return _max_window_score(source, target, self._sequence_score_pair)

    def _fuzzy_sentence_score(self, source: str, target: str) -> float:
        try:
            from rapidfuzz import fuzz
        except ImportError:
            return 0.0

        src_sents = _split_sentences(source)[:40]
        tgt_sents = _split_sentences(target)[:40]
        if not src_sents or not tgt_sents:
            return 0.0

        best_scores: list[float] = []
        for ss in src_sents:
            row_best = max(fuzz.token_sort_ratio(ss, ts) for ts in tgt_sents)
            if row_best >= 55:
                best_scores.append(row_best)

        if not best_scores:
            return 0.0

        avg = sum(best_scores) / len(best_scores)
        coverage = min(len(best_scores) / max(len(src_sents), 1), 1.0)
        return round(avg * 0.75 + coverage * 100.0 * 0.25, 2)

    def _best_matching_fragments(self, source: str, target: str, max_len: int = 280) -> tuple[str, str]:
        src_paras = _split_chunks(source, min_words=25, max_words=200)
        tgt_paras = _split_chunks(target, min_words=25, max_words=200)
        if not src_paras or not tgt_paras:
            return self._excerpt(source, max_len), self._excerpt(target, max_len)

        best_score = 0.0
        best_pair = ("", "")
        for sp in src_paras[:30]:
            for tp in tgt_paras[:30]:
                score = SequenceMatcher(None, sp.lower(), tp.lower()).ratio()
                if score > best_score:
                    best_score = score
                    best_pair = (sp, tp)

        if best_pair[0]:
            return self._excerpt(best_pair[0], max_len), self._excerpt(best_pair[1], max_len)
        return self._excerpt(source, max_len), self._excerpt(target, max_len)

    @staticmethod
    def _excerpt(text: str, max_len: int = 280) -> str:
        clean = _normalize(text)
        if len(clean) <= max_len:
            return clean
        cutoff = clean[:max_len].rfind(" ")
        return clean[: cutoff or max_len]

    @staticmethod
    def _confidence(final: float, semantic: float, cross: float, tfidf: float) -> str:
        ml_active = semantic > 0 or cross > 0
        if final >= 70 and (semantic >= 55 or tfidf >= 60):
            return "very_high"
        if final >= 50 and ml_active:
            return "high"
        if final >= 25:
            return "medium"
        if final > 0:
            return "low"
        return "none"

    @staticmethod
    def _empty_score() -> dict[str, Any]:
        return {
            "final_score": 0.0,
            "semantic_score": 0.0,
            "cross_encoder_score": 0.0,
            "tfidf_score": 0.0,
            "jaccard_score": 0.0,
            "sequence_score": 0.0,
            "fuzzy_score": 0.0,
            "thesis_excerpt": "",
            "source_excerpt": "",
            "confidence": "none",
            "method": "empty",
        }


_engine: SimilarityEngine | None = None


def get_engine() -> SimilarityEngine:
    global _engine
    if _engine is None:
        _engine = SimilarityEngine()
    return _engine


def score_document_pair(source: str, target: str) -> dict[str, Any]:
    return get_engine().score_pair(source, target)
