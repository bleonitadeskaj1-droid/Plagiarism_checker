import hashlib
import logging
import re
import time
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from detection.engine import get_engine
from detection.internal import quick_internal_score
from detection.aligner import build_thesis_highlights, render_highlighted_lines
from detection.stats import (
    compute_match_statistics,
    compute_unique_coverage,
    build_source_summaries,
    build_match_fragments,
)
from detection.ai_detector import estimate_ai_likelihood
from detection.faiss_corpus import semantic_prefilter
from detection.content_words import FUNCTION_WORDS, content_text

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class PlagiarismAgent:
    """Plagiarism detection — semantic + cross-encoder + lexical ensemble (local, no paid APIs)."""

    PLAGIARISM_THRESHOLDS = {
        "critical": 70.0,
        "high": 50.0,
        "medium": 25.0,
        "low": 0.0,
    }

    MAX_MATCHES = 8
    MAX_INTERNAL_MATCHES = 10
    MAX_WEB_MATCHES = 12
    WEB_SEARCH_BUDGET_SEC = 80.0
    SHINGLE_SIZE = 5
    MIN_MATCH_SCORE = 3.0
    STOPWORDS = FUNCTION_WORDS

    def __init__(self, *args, **kwargs):
        self.token_pattern = re.compile(r"[^\W_]+", re.UNICODE)
        self._engine = get_engine()
        logger.debug("PlagiarismAgent initialized (ensemble detection engine).")

    def _normalize_text(self, text: str) -> str:
        if not text:
            return ""
        return content_text(text)

    def _get_shingle_hashes(self, text: str) -> set[str]:
        tokens = self._normalize_text(text).split()
        if len(tokens) < self.SHINGLE_SIZE:
            return set()

        return {
            hashlib.md5(" ".join(tokens[i : i + self.SHINGLE_SIZE]).encode("utf-8")).hexdigest()[:8]
            for i in range(len(tokens) - self.SHINGLE_SIZE + 1)
        }

    def _jaccard_similarity(self, source_shingles: set[str], target_shingles: set[str]) -> float:
        if not source_shingles or not target_shingles:
            return 0.0

        intersection = source_shingles.intersection(target_shingles)
        union = source_shingles.union(target_shingles)
        if not union:
            return 0.0

        return round((len(intersection) / len(union)) * 100.0, 2)

    def _tfidf_similarity(self, source: str, documents: List[str]) -> List[float]:
        source_text = self._normalize_text(source)
        document_texts = [self._normalize_text(doc or "") for doc in documents]

        if not source_text or not any(document_texts):
            return [0.0 for _ in documents]

        corpus = [source_text] + document_texts
        try:
            vectorizer = TfidfVectorizer(
                lowercase=False,
                tokenizer=str.split,
                ngram_range=(1, 2),
                sublinear_tf=True,
                min_df=1,
                max_df=0.95,
            )
            matrix = vectorizer.fit_transform(corpus)
            scores = cosine_similarity(matrix[0:1], matrix[1:]).flatten()
            return [round(float(score * 100.0), 2) for score in scores]
        except Exception as exc:
            logger.warning("TF-IDF similarity failed: %s", exc)
            return [0.0 for _ in documents]

    def _combined_score(self, tfidf_score: float, jaccard_score: float, sequence_score: float = 0.0) -> float:
        if sequence_score >= 85.0:
            return round(max(sequence_score, tfidf_score * 0.6 + jaccard_score * 0.4), 2)
        return round(tfidf_score * 0.5 + jaccard_score * 0.35 + sequence_score * 0.15, 2)

    def _sequence_similarity(self, source: str, target: str) -> float:
        norm_a = self._normalize_text(source)
        norm_b = self._normalize_text(target)
        if not norm_a or not norm_b:
            return 0.0
        sample_a = norm_a[:12000]
        sample_b = norm_b[:12000]
        return round(SequenceMatcher(None, sample_a, sample_b).ratio() * 100.0, 2)

    def _safe_excerpt(self, text: str, max_length: int = 240) -> str:
        if not text:
            return ""

        clean = re.sub(r"\s+", " ", text.strip())
        if len(clean) <= max_length:
            return clean

        cutoff = clean[:max_length].rfind(" ")
        return clean[:cutoff or max_length]

    def _split_paragraphs(self, text: str, min_len: int = 40) -> List[str]:
        if not text:
            return []
        parts = re.split(r"\n{2,}|\n", text)
        return [p.strip() for p in parts if len(p.strip()) >= min_len]

    def _find_matching_snippet(self, thesis_text: str, doc_content: str) -> Tuple[str, str]:
        """Return best paragraph pair only when content actually overlaps.

        Unrelated documents must not fall back to 'first paragraph of each' —
        that produced false fragments (e.g. Plagiarism Checker intro vs Siguria).
        """
        from detection.content_words import content_tokens

        thesis_paras = self._split_paragraphs(thesis_text, min_len=35)
        doc_paras = self._split_paragraphs(doc_content, min_len=35)
        if not thesis_paras or not doc_paras:
            return "", ""

        best_score = 0.0
        best_overlap = 0.0
        best_pair = ("", "")
        for thesis_para in thesis_paras[:50]:
            t_set = set(content_tokens(thesis_para))
            if len(t_set) < 4:
                continue
            for doc_para in doc_paras[:50]:
                d_set = set(content_tokens(doc_para))
                if len(d_set) < 4:
                    continue
                overlap = len(t_set & d_set) / max(1, min(len(t_set), len(d_set)))
                if overlap < 0.40:
                    continue
                score = self._sequence_similarity(thesis_para, doc_para)
                if score > best_score or (score >= best_score - 1 and overlap > best_overlap):
                    best_score = score
                    best_overlap = overlap
                    best_pair = (thesis_para, doc_para)

        # Require a real similar paragraph, not just shared stopwords
        if best_pair[0] and (best_score >= 35.0 or best_overlap >= 0.55):
            return self._safe_excerpt(best_pair[0]), self._safe_excerpt(best_pair[1])
        return "", ""

    def _excerpt_pair_is_valid(self, thesis_excerpt: str, source_excerpt: str) -> bool:
        """True only when the two displayed fragments share real content words."""
        from detection.content_words import content_tokens

        t = set(content_tokens(thesis_excerpt or ""))
        s = set(content_tokens(source_excerpt or ""))
        if len(t) < 4 or len(s) < 4:
            return False
        return (len(t & s) / max(1, min(len(t), len(s)))) >= 0.45

    def _filter_valid_matches(self, matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Drop doc-level noise: weak % without a validated similar fragment."""
        out: List[Dict[str, Any]] = []
        for m in matches or []:
            sim = float(m.get("similarity") or m.get("similarity_score") or 0)
            thesis_ex = (m.get("thesis_excerpt") or "").strip()
            source_ex = (m.get("matched_text") or m.get("original_text") or m.get("source_excerpt") or "").strip()
            has_fragment = bool(thesis_ex and source_ex and self._excerpt_pair_is_valid(thesis_ex, source_ex))
            # Strong duplicate / near-copy, or a verified fragment pair
            if sim >= 25.0 or has_fragment:
                if not has_fragment and sim < 55.0:
                    # Medium doc score without fragment — do not invent excerpts
                    m = dict(m)
                    m["thesis_excerpt"] = ""
                    m["matched_text"] = m.get("matched_text") if sim >= 40 else ""
                    m["original_text"] = m.get("original_text") if sim >= 40 else ""
                out.append(m)
        return out

    def _estimate_ai_likelihood(self, text: str) -> float:
        """Legacy float wrapper — advanced report lives in detection.ai_detector."""
        return float(estimate_ai_likelihood(text).get("score") or 0.0)

    def _estimate_ai_report(self, text: str) -> Dict[str, Any]:
        return estimate_ai_likelihood(text)

    def _url_title(self, url: str) -> str:
        try:
            host = urlparse(url).netloc.replace("www.", "")
            return host or url
        except Exception:
            return url

    def _search_web(self, thesis_text: str) -> List[Dict[str, Any]]:
        try:
            from agent.web_search.searcher import search_text_for_sources
        except Exception as exc:
            logger.warning("Web search unavailable: %s", exc)
            return []

        all_lines = thesis_text.splitlines()
        web_matches: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()
        deadline = time.monotonic() + self.WEB_SEARCH_BUDGET_SEC

        try:
            from agent.web_search.config import get_official_providers
            if not get_official_providers():
                logger.warning("Web search skipped: no Search API key in .env")
                return []

            # Kërko në fillim / mes / fund të temës (jo vetëm fillimin)
            probe = thesis_text
            if len(thesis_text) > 20000:
                third = len(thesis_text) // 3
                probe = (
                    thesis_text[:8000]
                    + "\n"
                    + thesis_text[third: third + 6000]
                    + "\n"
                    + thesis_text[-6000:]
                )
            hits = search_text_for_sources(
                probe,
                max_queries=10,
                max_results=self.MAX_WEB_MATCHES,
                max_verify=16,
            )
            logger.info("Web search returned %d source hits", len(hits))
        except Exception as exc:
            logger.warning("Web search failed: %s", exc)
            return []

        for rank, hit in enumerate(hits, start=1):
            if time.monotonic() >= deadline:
                break
            url = hit.get("url")
            if not url or url in seen_urls:
                continue

            verify_score = float(hit.get("verify_score") or 0)
            is_exact = bool(hit.get("exact_match"))
            verified = bool(hit.get("verified"))
            word_overlap = float(hit.get("word_overlap") or 0)

            # Prano çdo hit që searcher e ka kthyer (tashmë i filtruar)
            if verify_score < 55 and not is_exact and not verified:
                continue

            seen_urls.add(url)

            thesis_phrase = (hit.get("thesis_phrase") or hit.get("query") or "").strip('"')
            snippet = (hit.get("snippet") or "").strip()
            best_para = thesis_phrase or snippet

            metrics = self._engine.score_pair(best_para[:800], snippet[:800], rerank=False)
            similarity = float(metrics["final_score"])
            if is_exact:
                similarity = max(similarity, 90.0, verify_score * 0.96)
            elif verified and verify_score >= 70:
                similarity = max(similarity, verify_score * 0.92, 55.0)
            elif verify_score >= 55:
                similarity = max(similarity, verify_score * 0.85, 45.0)
            else:
                similarity = max(similarity, verify_score * 0.75, 35.0)

            line_start = None
            thesis_excerpt = self._safe_excerpt(best_para or thesis_phrase, 500)
            locate = re.sub(r"\s+", " ", (thesis_phrase or thesis_excerpt).lower().strip())
            for i, line in enumerate(all_lines, start=1):
                line_cmp = re.sub(r"\s+", " ", line.lower().strip())
                if locate and len(locate) >= 30 and locate[:50] in line_cmp:
                    line_start = i
                    break
                if thesis_excerpt and thesis_excerpt[:50] in line:
                    line_start = i
                    break

            title = (hit.get("title") or self._url_title(url)).strip()
            provider = hit.get("provider") or "web"

            web_matches.append(
                {
                    "rank": rank,
                    "source_type": "web",
                    "source_title": title,
                    "source": url,
                    "source_url": url,
                    "matched_text": self._safe_excerpt(snippet, 500),
                    "web_snippet_full": snippet[:1200],
                    "thesis_excerpt": thesis_excerpt,
                    "thesis_phrase": thesis_phrase,
                    "original_text": self._safe_excerpt(snippet, 500),
                    "similarity": round(min(100.0, similarity), 2),
                    "line_number": line_start,
                    "semantic_score": metrics.get("semantic_score", 0.0),
                    "tfidf_score": metrics.get("tfidf_score", 0.0),
                    "fuzzy_score": metrics.get("fuzzy_score", 0.0),
                    "confidence": "high" if is_exact or (verified and verify_score >= 80) else metrics.get("confidence", "medium"),
                    "verified": verified,
                    "exact_web_match": is_exact,
                    "verify_score": verify_score,
                    "word_overlap": word_overlap,
                    "search_provider": provider,
                    "reason": (
                        f"Web ({provider}): "
                        + ("përputhje e saktë në faqe · " if is_exact else ("verifikuar në faqe · " if verified else ""))
                        + f"overlap {word_overlap:.0f}% · verifikim {verify_score:.0f}%"
                    ),
                }
            )

        return sorted(web_matches, key=lambda item: item["similarity"], reverse=True)[: self.MAX_WEB_MATCHES]

    def _build_match(
        self,
        doc: Dict[str, Any],
        tfidf_score: float,
        jaccard_score: float,
        sequence_score: float,
        source_type: str,
        rank: int,
        thesis_text: str = "",
    ) -> Optional[Dict[str, Any]]:
        similarity = self._combined_score(tfidf_score, jaccard_score, sequence_score)
        if similarity < self.MIN_MATCH_SCORE:
            return None

        title = doc.get("title") or f"Document {doc.get('id', '')}".strip()
        thesis_excerpt, source_excerpt = self._find_matching_snippet(thesis_text, doc.get("content", ""))
        if not self._excerpt_pair_is_valid(thesis_excerpt, source_excerpt) and similarity < 25.0:
            return None
        thesis_id = doc.get("id") if source_type == "internal" else None
        source_url = doc.get("source_url")
        if source_type == "internal" and thesis_id:
            source_url = f"internal://thesis/{thesis_id}"

        return {
            "rank": rank,
            "source_type": source_type,
            "source_title": title,
            "source": title,
            "source_url": source_url,
            "conf_source_id": doc.get("id") if source_type == "confidential" else None,
            "thesis_id": thesis_id,
            "is_archive": bool(doc.get("is_archive")),
            "matched_text": source_excerpt,
            "thesis_excerpt": thesis_excerpt,
            "original_text": source_excerpt,
            "similarity": similarity,
            "tfidf_score": round(tfidf_score, 2),
            "jaccard_score": round(jaccard_score, 2),
            "sequence_score": round(sequence_score, 2),
            "paragraph_index": doc.get("paragraph_index", 0),
            "reason": "Lokal: TF-IDF + Jaccard + SequenceMatcher",
        }

    def _compare_documents(self, source_text: str, documents: List[Dict[str, Any]], source_type: str) -> List[Dict[str, Any]]:
        source_text = source_text or ""
        documents = [doc for doc in documents if doc.get("content")]
        if not source_text or not documents:
            return []

        matches: List[Dict[str, Any]] = []
        engine_candidates: List[Dict[str, Any]] = []

        # Pass 1: exact hash + MinHash — catches duplicate uploads instantly
        for doc in documents:
            quick = quick_internal_score(source_text, doc.get("content", ""))
            if quick["final_score"] >= 75.0:
                thesis_id = doc.get("id") if source_type == "internal" else None
                source_url = doc.get("source_url")
                if source_type == "internal" and thesis_id:
                    source_url = f"internal://thesis/{thesis_id}"
                matches.append({
                    "rank": 0,
                    "source_type": source_type,
                    "source_title": doc.get("title") or f"Document {doc.get('id', '')}".strip(),
                    "source": doc.get("title") or "",
                    "source_url": source_url,
                    "conf_source_id": doc.get("id") if source_type == "confidential" else None,
                    "thesis_id": thesis_id,
                    "is_archive": bool(doc.get("is_archive")),
                    "matched_text": quick.get("source_excerpt") or self._safe_excerpt(doc.get("content", "")),
                    "thesis_excerpt": quick.get("thesis_excerpt") or self._safe_excerpt(source_text),
                    "original_text": quick.get("source_excerpt") or self._safe_excerpt(doc.get("content", "")),
                    "similarity": float(quick["final_score"]),
                    "exact_duplicate": bool(quick.get("exact_duplicate")),
                    "minhash_score": quick.get("minhash_score", 0.0),
                    "confidence": quick.get("confidence", "very_high"),
                    "reason": f"Duplicate detection ({quick.get('method', 'hash')}): {quick['final_score']:.1f}%",
                })
            else:
                engine_candidates.append(doc)

        # Pass 1b: FAISS semantic shortlist for large corpora (before ensemble)
        if engine_candidates:
            engine_candidates = semantic_prefilter(source_text, engine_candidates)

        # Pass 2: full ensemble for remaining documents
        if engine_candidates:
            scored = self._engine.score_batch(source_text, engine_candidates, rerank_top_k=8)
            for rank, item in enumerate(scored, start=1):
                doc = item["doc"]
                similarity = float(item["final_score"])
                if similarity < self.MIN_MATCH_SCORE:
                    continue

                title = doc.get("title") or f"Document {doc.get('id', '')}".strip()
                thesis_excerpt = item.get("thesis_excerpt") or ""
                source_excerpt = item.get("source_excerpt") or ""
                if not self._excerpt_pair_is_valid(thesis_excerpt, source_excerpt):
                    thesis_excerpt, source_excerpt = self._find_matching_snippet(
                        source_text, doc.get("content", "")
                    )
                if not self._excerpt_pair_is_valid(thesis_excerpt, source_excerpt) and similarity < 25.0:
                    continue
                thesis_id = doc.get("id") if source_type == "internal" else None
                source_url = doc.get("source_url")
                if source_type == "internal" and thesis_id:
                    source_url = f"internal://thesis/{thesis_id}"

                matches.append({
                    "rank": rank,
                    "source_type": source_type,
                    "source_title": title,
                    "source": title,
                    "source_url": source_url,
                    "conf_source_id": doc.get("id") if source_type == "confidential" else None,
                    "thesis_id": thesis_id,
                    "is_archive": bool(doc.get("is_archive")),
                    "matched_text": source_excerpt,
                    "thesis_excerpt": thesis_excerpt,
                    "original_text": source_excerpt,
                    "similarity": similarity,
                    "semantic_score": item.get("semantic_score", 0.0),
                    "cross_encoder_score": item.get("cross_encoder_score", 0.0),
                    "tfidf_score": item.get("tfidf_score", 0.0),
                    "jaccard_score": item.get("jaccard_score", 0.0),
                    "sequence_score": item.get("sequence_score", 0.0),
                    "fuzzy_score": item.get("fuzzy_score", 0.0),
                    "confidence": item.get("confidence", "medium"),
                    "paragraph_index": doc.get("paragraph_index", 0),
                    "reason": (
                        f"Ensemble: semantic {item.get('semantic_score', 0):.0f}% + "
                        f"TF-IDF {item.get('tfidf_score', 0):.0f}% + "
                        f"Jaccard {item.get('jaccard_score', 0):.0f}% + "
                        f"fuzzy {item.get('fuzzy_score', 0):.0f}%"
                    ),
                })

        return sorted(matches, key=lambda item: item["similarity"], reverse=True)[: self.MAX_INTERNAL_MATCHES]

    def compare_two_texts(
        self,
        text_a: str,
        text_b: str,
        title_a: str = "Tema A",
        title_b: str = "Tema B",
    ) -> Dict[str, Any]:
        """Direct comparison of two thesis texts — returns percentage and breakdown."""
        doc_b = {"id": None, "title": title_b, "content": text_b}
        matches = self._compare_documents(text_a, [doc_b], "internal")
        if matches:
            best = matches[0]
            score = float(best["similarity"])
            return {
                "overall_score": score,
                "internal_score": score,
                "title_a": title_a,
                "title_b": title_b,
                "tfidf_score": best.get("tfidf_score", 0),
                "jaccard_score": best.get("jaccard_score", 0),
                "sequence_score": best.get("sequence_score", 0),
                "risk_level": self._risk_level(score),
                "summary": f"Ngjashmëria midis «{title_a}» dhe «{title_b}» është {score:.1f}%.",
                "recommendations": self._recommendations(score),
            }

        metrics = self._engine.score_pair(text_a, text_b)
        score = float(metrics["final_score"])
        return {
            "overall_score": score,
            "internal_score": score,
            "title_a": title_a,
            "title_b": title_b,
            "semantic_score": metrics.get("semantic_score", 0.0),
            "tfidf_score": metrics.get("tfidf_score", 0.0),
            "jaccard_score": metrics.get("jaccard_score", 0.0),
            "sequence_score": metrics.get("sequence_score", 0.0),
            "fuzzy_score": metrics.get("fuzzy_score", 0.0),
            "risk_level": self._risk_level(score),
            "summary": f"Ngjashmëria midis «{title_a}» dhe «{title_b}» është {score:.1f}%.",
            "recommendations": self._recommendations(score),
        }

    def analyze_with_confidential(
        self,
        thesis_text,
        thesis_title,
        public_theses,
        conf_docs,
        search_web=True,
    ) -> Dict[str, Any]:
        public_matches = self._compare_documents(thesis_text, public_theses or [], "internal")
        confidential_matches = self._compare_documents(thesis_text, conf_docs or [], "confidential") if conf_docs else []
        web_matches = self._search_web(thesis_text) if search_web else []

        all_channel_matches = public_matches + confidential_matches + web_matches
        flagged_sections = sorted(
            all_channel_matches,
            key=lambda item: item["similarity"],
            reverse=True,
        )[: max(self.MAX_MATCHES + self.MAX_WEB_MATCHES, 20)]

        ai_report = self._estimate_ai_report(thesis_text)
        ai_likelihood = float(ai_report.get("score") or 0.0)

        all_docs = list(public_theses or []) + list(conf_docs or [])
        line_highlights = build_thesis_highlights(
            thesis_text, public_matches + confidential_matches, web_matches, all_docs
        )
        highlighted_document = render_highlighted_lines(line_highlights, thesis_text)
        # Weighted unique-word coverage: exact copies count fully, similar text less.
        # AI likelihood is reported separately and NEVER added into overall_score.
        coverage = compute_unique_coverage(
            thesis_text,
            line_highlights,
            public_matches + confidential_matches + web_matches,
        )
        match_statistics = compute_match_statistics(line_highlights, thesis_text)
        match_statistics["coverage"] = coverage
        source_documents = build_source_summaries(
            line_highlights,
            public_matches + confidential_matches + web_matches,
        )
        match_fragments = build_match_fragments(line_highlights)
        primary_highlights = [h for h in line_highlights if h.get("match_type") and not h.get("is_secondary")]

        overall_score = float(coverage["overall_pct"])
        internal_score = float(coverage["internal_pct"])
        # Confidential contributes to the same "database" channel as internal
        confidential_score = 0.0
        if confidential_matches:
            conf_cov = compute_unique_coverage(
                thesis_text,
                [h for h in line_highlights if h.get("source_type") == "confidential"],
                confidential_matches,
            )
            confidential_score = float(conf_cov["internal_pct"])
        web_score = float(coverage["web_pct"])
        risk_level = self._risk_level(overall_score)
        ai_detected = bool(ai_report.get("detected")) if "detected" in ai_report else ai_likelihood >= 35

        # Burime interne: vetëm me fragment të vlefshëm ose ngjashmëri të fortë
        confirmed_internal = self._filter_valid_matches([
            m for m in public_matches
            if float(m.get("similarity", 0)) >= 3
            or any(
                h.get("thesis_id") == m.get("thesis_id")
                for h in line_highlights
                if h.get("source_type") == "internal"
            )
        ])
        # Prefer sources that actually contributed highlights, then by similarity
        hl_ids = {
            h.get("thesis_id")
            for h in line_highlights
            if h.get("source_type") == "internal" and h.get("thesis_id")
        }
        confirmed_internal.sort(
            key=lambda m: (
                1 if m.get("thesis_id") in hl_ids else 0,
                float(m.get("similarity") or 0),
            ),
            reverse=True,
        )
        internal_titles = [m["source_title"] for m in confirmed_internal[:6]]
        web_matches = self._filter_valid_matches(web_matches)
        summary_parts = [
            f"Analiza për '{thesis_title}' gjeti {overall_score}% plagjiaturë totale "
            f"(mbulim unik, pa lidhëza: {coverage['unique_plagiarized_words']}/{coverage['total_words']} fjalë përmbajtjeje).",
        ]
        summary_parts.append(
            f"Databaza e brendshme: {internal_score}% · Internet: {web_score}%."
        )
        if primary_highlights:
            summary_parts.append(
                f"{match_statistics['matched_lines']} rreshta, "
                f"{match_statistics['matched_paragraphs']} paragrafë të mbuluar."
            )
        if len(source_documents) > 1:
            summary_parts.append(
                f"Ngjashmëri me {len(source_documents)} burime të ndryshme."
            )
        if internal_titles:
            summary_parts.append(
                "Burime interne të dyshimta: " + ", ".join(f"«{title}»" for title in internal_titles) + "."
            )
        if web_matches:
            summary_parts.append(f"{len(web_matches)} referencë web u gjetën.")
        if ai_detected:
            summary_parts.append(
                f"AI/parafrazim: i zbuluar ({ai_likelihood}%) — nuk shtohet në totalin e plagjiaturës."
            )

        return {
            "overall_score": overall_score,
            "internal_score": round(internal_score, 2),
            "confidential_score": round(confidential_score, 2),
            "web_score": round(web_score, 2),
            "ai_likelihood_score": ai_likelihood,
            "ai_detected": ai_detected,
            "ai_analysis": ai_report,
            "coverage": coverage,
            "risk_level": risk_level,
            "summary": " ".join(summary_parts),
            "flagged_sections": flagged_sections,
            "line_highlights": line_highlights,
            "highlighted_document": highlighted_document,
            "match_statistics": match_statistics,
            "source_documents": source_documents,
            "match_fragments": match_fragments,
            "internal_sources": confirmed_internal,
            "web_sources": web_matches,
            "recommendations": self._recommendations(overall_score),
            "metadata": {
                "source_title": thesis_title,
                "document_count": len(public_theses or []) + len(conf_docs or []),
                "internal_matches": len(public_matches),
                "confidential_matches": len(confidential_matches),
                "web_matches": len(web_matches),
                "ai_likelihood_score": ai_likelihood,
                "ai_detected": ai_detected,
                "ai_method": ai_report.get("method"),
                "ai_signals": ai_report.get("signals"),
                "scoring_method": "unique_content_word_coverage",
                "analysis_method": "unique content-word coverage (lidhëzat excluded; AI excluded from total)",
                "search_web": bool(search_web),
                "faiss_prefilter": True,
            },
        }

    def analyze_plagiarism(self, thesis_text, thesis_title, existing_theses, search_web=True):
        return self.analyze_with_confidential(
            thesis_text=thesis_text,
            thesis_title=thesis_title,
            public_theses=existing_theses or [],
            conf_docs=[],
            search_web=search_web,
        )

    def generate_report_summary(self, result: Dict[str, Any], thesis_title: str) -> str:
        overall = float(result.get("overall_score", 0.0))
        internal = float(result.get("internal_score", 0.0))
        confidential = float(result.get("confidential_score", 0.0))
        return (
            f"Raport i shkurtër për {thesis_title}: "
            f"{overall}% ngjashmëri totale, "
            f"{internal}% nga baza interne dhe "
            f"{confidential}% nga dokumentet konfidenciale."
        )

    def _risk_level(self, score: float) -> str:
        if score >= self.PLAGIARISM_THRESHOLDS["critical"]:
            return "Critical"
        if score >= self.PLAGIARISM_THRESHOLDS["high"]:
            return "High"
        if score >= self.PLAGIARISM_THRESHOLDS["medium"]:
            return "Medium"
        return "Low"

    def _recommendations(self, score: float) -> str:
        if score >= self.PLAGIARISM_THRESHOLDS["critical"]:
            return "Shumë përmbajtje e kopjuar; rishikoni menjëherë dhe kërkoni përparim të plotë të citimeve."
        if score >= self.PLAGIARISM_THRESHOLDS["high"]:
            return "Rishikoni me kujdes pjesët e dyshimta dhe siguroni citimin e saktë për çdo fragment."
        if score >= self.PLAGIARISM_THRESHOLDS["medium"]:
            return "Kontrolloni pjesët e ngjashme dhe sigurohuni që referencat të jenë të plota."
        if score > 0:
            return "Ngjashmëria është e ulët; konfirmoni vetëm referencat bazë dhe qartësisht referenconi burimet."
        return "Nuk u gjet plagjiaturë të dukshme në dokument."
