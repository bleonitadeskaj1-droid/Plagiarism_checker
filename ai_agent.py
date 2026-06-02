import json
import re
from typing import Dict, Iterable, List, Optional

import nltk
import numpy as np
from nltk.stem import SnowballStemmer
from nltk.tokenize import RegexpTokenizer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


class PlagiarismAgent:
    def __init__(self):
        self.tokenizer = RegexpTokenizer(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]{3,}")
        self.stemmer = SnowballStemmer("english")

    def analyze_with_confidential(self, thesis_text, thesis_title, public_theses, conf_docs, search_web=True):
        public_matches = self._compare_documents(
            source_text=thesis_text,
            documents=public_theses,
            source_type="internal",
        )
        confidential_matches = self._compare_documents(
            source_text=thesis_text,
            documents=conf_docs,
            source_type="confidential",
        )

        flagged_sections = sorted(
            public_matches + confidential_matches,
            key=lambda item: item.get("similarity", 0),
            reverse=True,
        )

        internal_score = self._max_similarity(public_matches)
        confidential_score = self._max_similarity(confidential_matches)
        web_score = 0.0
        overall_score = max(internal_score, confidential_score, web_score)

        return {
            "overall_score": round(overall_score, 2),
            "internal_score": round(internal_score, 2),
            "confidential_score": round(confidential_score, 2),
            "web_score": round(web_score, 2),
            "risk_level": self._risk_level(overall_score),
            "summary": self._summary(thesis_title, overall_score, len(flagged_sections), search_web),
            "flagged_sections": flagged_sections,
            "recommendations": self._recommendations(overall_score, flagged_sections),
        }

    def analyze_plagiarism(self, thesis_text, thesis_title, existing_theses, search_web=True):
        return self.analyze_with_confidential(
            thesis_text=thesis_text,
            thesis_title=thesis_title,
            public_theses=existing_theses,
            conf_docs=[],
            search_web=search_web,
        )

    def generate_report_summary(self, result, thesis_title):
        return (
            f"Raport i shkurtër për {thesis_title}: "
            f"{result.get('overall_score', 0)}% ngjashmëri totale, "
            f"{result.get('internal_score', 0)}% nga baza interne dhe "
            f"{result.get('confidential_score', 0)}% nga dokumentet konfidenciale."
        )

    def _compare_documents(self, source_text: str, documents: Optional[Iterable[Dict]], source_type: str) -> List[Dict]:
        documents = [doc for doc in (documents or []) if (doc.get("content") or "").strip()]
        if not (source_text or "").strip() or not documents:
            return []

        corpus = [source_text] + [doc.get("content", "") for doc in documents]
        processed_corpus = [self._preprocess_text(text) for text in corpus]

        if not processed_corpus[0] or not any(processed_corpus[1:]):
            return []

        try:
            vectorizer = TfidfVectorizer(
                lowercase=False,
                token_pattern=None,
                tokenizer=str.split,
                norm="l2",
                sublinear_tf=True,
            )
            tfidf_matrix = vectorizer.fit_transform(processed_corpus)
            similarities = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:]).flatten()
        except ValueError:
            return []

        matches = []
        for doc, similarity in zip(documents, similarities):
            percentage = float(np.clip(similarity * 100.0, 0.0, 100.0))

            title = doc.get("title") or f"Document {doc.get('id', '')}".strip()
            match = {
                "text": self._extract_representative_excerpt(source_text),
                "source": title,
                "source_type": source_type,
                "source_title": title,
                "source_url": doc.get("source_url") or doc.get("url"),
                "original_text": self._extract_representative_excerpt(source_text),
                "similarity": round(percentage, 2),
                "paragraph_index": 0,
                "reason": "TF-IDF cosine similarity",
            }

            if source_type == "confidential":
                match["conf_source_id"] = doc.get("id")

            matches.append(match)

        return sorted(matches, key=lambda item: item["similarity"], reverse=True)

    def _preprocess_text(self, text: str) -> str:
        text = (text or "").lower()
        tokens = self.tokenizer.tokenize(text)
        normalized = []

        for token in tokens:
            if token.isdigit():
                continue
            normalized.append(self.stemmer.stem(token))

        return " ".join(normalized)

    def _extract_representative_excerpt(self, text: str, max_length: int = 200) -> str:
        clean_text = re.sub(r"\s+", " ", (text or "")).strip()
        if len(clean_text) <= max_length:
            return clean_text
        return clean_text[:max_length].rsplit(" ", 1)[0]

    def _max_similarity(self, matches: List[Dict]) -> float:
        if not matches:
            return 0.0
        return float(np.max([match.get("similarity", 0.0) for match in matches]))

    def _risk_level(self, score: float) -> str:
        if score >= 70:
            return "critical"
        if score >= 50:
            return "high"
        if score >= 25:
            return "medium"
        return "low"

    def _summary(self, thesis_title: str, score: float, match_count: int, search_web: bool) -> str:
        web_note = "Krahasimi web nuk u ekzekutua nga ky agent lokal." if search_web else "Krahasimi web ishte i çaktivizuar."
        return (
            f"Analiza për '{thesis_title}' gjeti {round(score, 2)}% ngjashmëri maksimale "
            f"në {match_count} dokument(e) të krahasuara. {web_note}"
        )

    def _recommendations(self, score: float, matches: List[Dict]) -> str:
        if not matches:
            return "Nuk u gjetën ngjashmëri të rëndësishme me dokumentet në bazë."
        if score >= 50:
            return "Rishikoni me kujdes burimet me ngjashmërinë më të lartë dhe verifikoni citimet."
        if score >= 25:
            return "Kontrolloni seksionet e ngjashme dhe sigurohuni që referencat janë të plota."
        return "Ngjashmëria është e ulët; rekomandohet vetëm kontroll normal akademik."

    def _parse_json(self, text):
        try:
            text = re.sub(r"```json\s*", "", text)
            text = re.sub(r"```\s*", "", text).strip()
            match = re.search(r"\{.*\}", text, re.DOTALL)
            return json.loads(match.group() if match else text)
        except Exception:
            return self._error_result("JSON parse failed")

    def _error_result(self, msg):
        return {
            "overall_score": 0,
            "internal_score": 0,
            "confidential_score": 0,
            "web_score": 0,
            "risk_level": "unknown",
            "summary": f"Gabim: {msg}",
            "flagged_sections": [],
            "recommendations": "Provoni përsëri.",
        }
