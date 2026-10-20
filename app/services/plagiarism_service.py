"""Plagiarism detection service using sentence-transformers and FAISS."""

from __future__ import annotations

from difflib import SequenceMatcher
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.config import settings
from app.models.document import Document
from app.repositories.document_repo import DocumentRepository
from app.services.text_processing import extract_key_sentences, extract_text_from_file

logger = logging.getLogger(__name__)


def _require_semantic_packages() -> tuple[Any, Any]:
    """Import semantic dependencies or raise a clear error."""
    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "Semantic ML packages are required for plagiarism checks. "
            "Install with: pip install sentence-transformers torch"
        ) from exc
    return np, SentenceTransformer


def _require_faiss_package() -> Any:
    """Import FAISS dependency for optional index/search operations."""
    try:
        import faiss
    except ImportError as exc:
        raise RuntimeError(
            "FAISS package is required for vector index operations. "
            "Install with: pip install faiss-cpu"
        ) from exc
    return faiss


def compare_paragraphs(text1: str, text2: str) -> list[dict[str, str]]:
    """Find identical or near-identical paragraphs between two texts."""
    paras1 = [p.strip() for p in re.split(r"\n{2,}", text1) if p.strip()]
    paras2 = [p.strip() for p in re.split(r"\n{2,}", text2) if p.strip()]
    matches: list[dict[str, str]] = []
    seen: set[str] = set()

    for p1 in paras1:
        norm1 = re.sub(r"\s+", " ", p1.lower())
        if len(norm1) < 50:
            continue
        for p2 in paras2:
            norm2 = re.sub(r"\s+", " ", p2.lower())
            if norm1 == norm2 and norm1 not in seen:
                seen.add(norm1)
                matches.append({"source": p1[:500], "target": p2[:500]})
                break
    return matches


@dataclass
class SimilarMatch:
    document_id: int
    title: str
    similarity: float
    excerpt: str


class PlagiarismService:
    """Semantic similarity search and plagiarism reporting."""

    SEMANTIC_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    SEMANTIC_WEIGHT = 0.60
    TFIDF_WEIGHT = 0.20
    JACCARD_WEIGHT = 0.10
    SEQUENCE_WEIGHT = 0.10

    def __init__(self) -> None:
        self.model_name = self.SEMANTIC_MODEL_NAME
        self.index_dir = settings.faiss_index_dir
        self._model = None
        self._index = None
        self._doc_ids: list[int] = []
        self._metadata_path = self.index_dir / "index_meta.json"

    @property
    def model(self):
        if self._model is None:
            _, SentenceTransformer = _require_semantic_packages()
            logger.info("Loading embedding model: %s", self.model_name)
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def _embed(self, texts: list[str]):
        np, _ = _require_semantic_packages()
        vectors = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vectors, dtype=np.float32)

    def rebuild_index(self, session: Session) -> int:
        """Rebuild FAISS index from all documents in the database."""
        faiss = _require_faiss_package()
        repo = DocumentRepository(session)
        documents = repo.get_all(limit=10_000)
        self._index = faiss.IndexFlatIP(self.model.get_sentence_embedding_dimension())
        self._doc_ids = []

        if not documents:
            self._save_metadata()
            return 0

        texts = [doc.content[:8000] for doc in documents]
        vectors = self._embed(texts)
        self._index.add(vectors)
        self._doc_ids = [doc.id for doc in documents]
        self._save_metadata()
        logger.info("FAISS index rebuilt with %s documents", len(documents))
        return len(documents)

    def _save_metadata(self) -> None:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        payload = {"doc_ids": self._doc_ids, "model": self.model_name}
        self._metadata_path.write_text(json.dumps(payload), encoding="utf-8")

    def _load_index(self, session: Session) -> None:
        if self._index is not None:
            return
        faiss = _require_faiss_package()
        repo = DocumentRepository(session)
        documents = repo.get_all(limit=10_000)
        if not documents:
            dim = self.model.get_sentence_embedding_dimension()
            self._index = faiss.IndexFlatIP(dim)
            self._doc_ids = []
            return

        texts = [doc.content[:8000] for doc in documents]
        vectors = self._embed(texts)
        self._index = faiss.IndexFlatIP(vectors.shape[1])
        self._index.add(vectors)
        self._doc_ids = [doc.id for doc in documents]

    def search_similar(
        self,
        session: Session,
        text: str,
        top_k: int = 5,
        exclude_id: int | None = None,
    ) -> list[SimilarMatch]:
        """Find most similar documents using cosine similarity."""
        self._load_index(session)
        assert self._index is not None

        if self._index.ntotal == 0:
            return []

        query = self._embed([text[:8000]])
        k = min(top_k + (1 if exclude_id else 0), self._index.ntotal)
        scores, indices = self._index.search(query, k)

        repo = DocumentRepository(session)
        results: list[SimilarMatch] = []

        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._doc_ids):
                continue
            doc_id = self._doc_ids[int(idx)]
            if exclude_id and doc_id == exclude_id:
                continue
            doc = repo.get(doc_id)
            if not doc:
                continue
            results.append(
                SimilarMatch(
                    document_id=doc.id,
                    title=doc.title,
                    similarity=round(float(score) * 100, 2),
                    excerpt=doc.content[:300],
                )
            )
            if len(results) >= top_k:
                break
        return results

    def compare_texts(self, text1: str, text2: str) -> dict[str, Any]:
        """Compare two texts and return similarity metrics."""
        metrics = self._score_documents(text1, text2)
        identical = compare_paragraphs(text1, text2)
        return {
            "similarity_score": metrics["final_score"],
            "identical_paragraphs": identical,
            "report": {
                "summary": f"Similarity: {metrics['final_score']}%",
                "identical_count": len(identical),
            },
        }

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip())

    def _tokenize_for_shingles(self, text: str) -> list[str]:
        return re.findall(r"\b\w+\b", self._normalize_text(text).lower())

    def _split_into_chunks(self, text: str, min_words: int = 120, max_words: int = 250) -> list[str]:
        normalized = self._normalize_text(text)
        if not normalized:
            return []

        paragraphs = [p.strip() for p in re.split(r"\n{2,}", normalized) if p.strip()]
        if not paragraphs:
            paragraphs = [normalized]

        chunks: list[str] = []
        current_chunk: list[str] = []
        current_word_count = 0

        for paragraph in paragraphs:
            paragraph_words = len(re.findall(r"\b\w+\b", paragraph))
            if current_chunk and current_word_count + paragraph_words > max_words:
                if current_word_count >= min_words:
                    chunks.append(" ".join(current_chunk))
                current_chunk = [paragraph]
                current_word_count = paragraph_words
            else:
                current_chunk.append(paragraph)
                current_word_count += paragraph_words

        if current_chunk:
            chunks.append(" ".join(current_chunk))

        if not chunks:
            return [normalized]

        final_chunks: list[str] = []
        for chunk in chunks:
            sentences = re.split(r"(?<=[.!?])\s+", chunk)
            if len(sentences) <= 1:
                final_chunks.append(chunk)
                continue

            current_sentences: list[str] = []
            current_len = 0
            for sentence in sentences:
                sentence_len = len(re.findall(r"\b\w+\b", sentence))
                if current_sentences and current_len + sentence_len > max_words:
                    if current_len >= min_words:
                        final_chunks.append(" ".join(current_sentences))
                    current_sentences = [sentence]
                    current_len = sentence_len
                else:
                    current_sentences.append(sentence)
                    current_len += sentence_len

            if current_sentences:
                final_chunks.append(" ".join(current_sentences))

        return [chunk for chunk in final_chunks if chunk.strip()]

    def _semantic_chunk_similarity(self, text1: str, text2: str) -> tuple[float, list[tuple[int, int, float]]]:
        np, _ = _require_semantic_packages()
        chunks_a = self._split_into_chunks(text1)
        chunks_b = self._split_into_chunks(text2)
        if not chunks_a or not chunks_b:
            return 0.0, []

        embeddings_a = self.model.encode(chunks_a, convert_to_numpy=True, show_progress_bar=False)
        embeddings_b = self.model.encode(chunks_b, convert_to_numpy=True, show_progress_bar=False)
        matrix = cosine_similarity(embeddings_a, embeddings_b)
        matrix = np.clip(matrix, 0.0, 1.0)

        matched_pairs: list[tuple[int, int, float]] = []
        used_rows: set[int] = set()
        used_cols: set[int] = set()
        work = matrix.copy()

        for _ in range(min(work.shape[0], work.shape[1])):
            best_idx = np.unravel_index(np.argmax(work), work.shape)
            score = float(work[best_idx])
            if score < 0.35:
                break

            row_idx, col_idx = best_idx
            if row_idx in used_rows or col_idx in used_cols:
                work[row_idx, :] = -1.0
                work[:, col_idx] = -1.0
                continue

            used_rows.add(row_idx)
            used_cols.add(col_idx)
            matched_pairs.append((row_idx, col_idx, score))
            work[row_idx, :] = -1.0
            work[:, col_idx] = -1.0

        if not matched_pairs:
            return 0.0, []

        avg_match = float(np.mean([score for _, _, score in matched_pairs])) * 100.0
        coverage = min(len(matched_pairs) / max(len(chunks_a), len(chunks_b)), 1.0)
        semantic = round((avg_match * 0.70) + (coverage * 100.0 * 0.30), 2)
        return semantic, matched_pairs

    def _tfidf_similarity(self, text1: str, text2: str) -> float:
        np, _ = _require_semantic_packages()
        normalized_1 = self._normalize_text(text1)
        normalized_2 = self._normalize_text(text2)
        if not normalized_1 or not normalized_2:
            return 0.0
        try:
            vectorizer = TfidfVectorizer(
                lowercase=True,
                ngram_range=(1, 2),
                sublinear_tf=True,
                min_df=1,
                max_df=0.95,
            )
            matrix = vectorizer.fit_transform([normalized_1, normalized_2])
            score = cosine_similarity(matrix[0:1], matrix[1:])[0, 0]
            return round(float(np.clip(score, 0.0, 1.0)) * 100.0, 2)
        except Exception as exc:
            logger.warning("TF-IDF similarity failed: %s", exc)
            return 0.0

    def _jaccard_similarity(self, text1: str, text2: str, shingle_size: int = 4) -> float:
        tokens_a = self._tokenize_for_shingles(text1)
        tokens_b = self._tokenize_for_shingles(text2)
        if not tokens_a or not tokens_b:
            return 0.0

        shingles_a = {tuple(tokens_a[i:i + shingle_size]) for i in range(len(tokens_a) - shingle_size + 1)}
        shingles_b = {tuple(tokens_b[i:i + shingle_size]) for i in range(len(tokens_b) - shingle_size + 1)}
        if not shingles_a or not shingles_b:
            return 0.0

        intersection = shingles_a.intersection(shingles_b)
        union = shingles_a.union(shingles_b)
        if not union:
            return 0.0
        return round((len(intersection) / len(union)) * 100.0, 2)

    def _sequence_similarity(self, text1: str, text2: str) -> float:
        normalized_1 = self._normalize_text(text1)
        normalized_2 = self._normalize_text(text2)
        if not normalized_1 or not normalized_2:
            return 0.0
        sample_1 = normalized_1[:12000]
        sample_2 = normalized_2[:12000]
        return round(SequenceMatcher(None, sample_1, sample_2).ratio() * 100.0, 2)

    def _combined_similarity(self, semantic: float, tfidf: float, jaccard: float, sequence: float) -> float:
        return round(
            (
                self.SEMANTIC_WEIGHT * (semantic / 100.0)
                + self.TFIDF_WEIGHT * (tfidf / 100.0)
                + self.JACCARD_WEIGHT * (jaccard / 100.0)
                + self.SEQUENCE_WEIGHT * (sequence / 100.0)
            )
            * 100.0,
            2,
        )

    def _score_documents(self, text1: str, text2: str) -> dict[str, Any]:
        semantic_score, chunk_pairs = self._semantic_chunk_similarity(text1, text2)
        tfidf_score = self._tfidf_similarity(text1, text2)
        jaccard_score = self._jaccard_similarity(text1, text2)
        sequence_score = self._sequence_similarity(text1, text2)
        final_score = self._combined_similarity(
            semantic=semantic_score,
            tfidf=tfidf_score,
            jaccard=jaccard_score,
            sequence=sequence_score,
        )
        return {
            "semantic_score": semantic_score,
            "tfidf_score": tfidf_score,
            "jaccard_score": jaccard_score,
            "sequence_score": sequence_score,
            "final_score": final_score,
            "chunk_pairs": chunk_pairs,
        }

    def _resolve_document_text(self, document: Document) -> str:
        content = (document.content or "").strip()
        if content:
            return content

        if not document.file_path:
            return ""
        file_path = Path(document.file_path)
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        if not file_path.exists():
            return ""

        ext = (document.file_type or file_path.suffix.lstrip(".")).lower()
        try:
            extracted = extract_text_from_file(file_path, ext)
        except Exception as exc:
            logger.warning("Text extraction during run_check failed for document %s: %s", document.id, exc)
            return ""
        return self._normalize_text(extracted)

    def run_check(self, session: Session, document: Document) -> dict[str, Any]:
        """Run full plagiarism check for a document."""
        source_text = self._resolve_document_text(document)
        if not source_text:
            raise RuntimeError("Document content is empty or cannot be extracted")

        repo = DocumentRepository(session)
        candidates = [doc for doc in repo.get_all(limit=10_000) if doc.id != document.id]

        detailed_matches: list[dict[str, Any]] = []
        for candidate in candidates:
            candidate_text = self._resolve_document_text(candidate)
            if not candidate_text:
                continue

            metrics = self._score_documents(source_text, candidate_text)
            detailed_matches.append(
                {
                    "document_id": candidate.id,
                    "title": candidate.title,
                    "similarity": metrics["final_score"],
                    "excerpt": candidate_text[:300],
                    "semantic_score": metrics["semantic_score"],
                    "tfidf_score": metrics["tfidf_score"],
                    "jaccard_score": metrics["jaccard_score"],
                    "sequence_score": metrics["sequence_score"],
                }
            )

        detailed_matches.sort(key=lambda item: item["similarity"], reverse=True)
        top_matches = detailed_matches[:5]
        top_score = top_matches[0]["similarity"] if top_matches else 0.0
        risk = self._risk_level(top_score)

        match_payload = [
            {
                "document_id": m["document_id"],
                "title": m["title"],
                "similarity": m["similarity"],
                "excerpt": m["excerpt"],
            }
            for m in top_matches
        ]

        report = {
            "document_id": document.id,
            "title": document.title,
            "word_count": document.word_count,
            "similarity_score": top_score,
            "risk_level": risk,
            "matches": match_payload,
            "key_sentences": extract_key_sentences(source_text),
        }

        return {
            "similarity_score": top_score,
            "risk_level": risk,
            "matches": match_payload,
            "report": report,
            "summary": f"Top similarity {top_score}% — risk {risk}",
        }

    @staticmethod
    def _risk_level(score: float) -> str:
        if score >= 70:
            return "high"
        if score >= 40:
            return "medium"
        return "low"


_plagiarism_service: PlagiarismService | None = None


def get_plagiarism_service() -> PlagiarismService:
    """Return singleton plagiarism service."""
    global _plagiarism_service
    if _plagiarism_service is None:
        _plagiarism_service = PlagiarismService()
    return _plagiarism_service
