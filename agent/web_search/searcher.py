"""Web search for suspicious thesis text — phrase-accurate source URLs."""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from agent.web_search.providers import SearchHit, web_search

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
CONNECT_TIMEOUT = 4
READ_TIMEOUT = 7
FETCH_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)
SEARCH_BUDGET_SEC = 55.0

_STOPWORDS = frozenset({
    "the", "and", "for", "that", "with", "this", "from", "are", "was", "were", "have", "has",
    "dhe", "që", "qe", "por", "një", "nje", "për", "per", "nga", "ose", "si", "në", "ne",
    "te", "të", "eshte", "është", "janë", "jane", "duhet", "mund", "shumë", "shume", "cdo", "çdo",
    "into", "also", "than", "then", "when", "which", "their", "there", "been", "will", "can",
})

_BLOCKED_HOST_SUFFIXES = (
    "facebook.com", "instagram.com", "twitter.com", "x.com", "tiktok.com",
    "pinterest.com", "webcache.googleusercontent.com", "translate.google.com",
    "youtube.com", "youtu.be", "reddit.com",
)

_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "source",
})


@dataclass(frozen=True)
class SearchQuery:
    thesis_phrase: str
    search_query: str


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", (text or "").lower())
    # strip diacritics for Albanian matching resilience
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def _content_words(text: str) -> list[str]:
    return [
        w for w in re.findall(r"[\w\u00c0-\u024f]+", _normalize(text))
        if len(w) > 2 and w not in _STOPWORDS
    ]


def _word_overlap_ratio(a: str, b: str) -> float:
    wa = set(_content_words(a))
    wb = set(_content_words(b))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa)


def _strip_quotes(text: str) -> str:
    return re.sub(r'^["\']+|["\']+$', "", (text or "").strip())


def is_blocked_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower().replace("www.", "")
        return any(host == s or host.endswith("." + s) for s in _BLOCKED_HOST_SUFFIXES)
    except Exception:
        return True


def normalize_url_key(url: str) -> str:
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower().replace("www.", "")
        path = parsed.path.rstrip("/") or "/"
        return f"{parsed.scheme}://{host}{path}".lower()
    except Exception:
        return (url or "").lower().rstrip("/")


def canonicalize_url(url: str) -> str:
    return url


def _short_quote(phrase: str, max_words: int = 10) -> str:
    sentence = phrase
    for part in re.split(r"(?<=[.!?])\s+", phrase):
        part = part.strip()
        if len(_content_words(part)) >= 5:
            sentence = part
            break
    words = re.findall(r"[\w\u00c0-\u024f']+", sentence)
    if len(words) < 5:
        words = re.findall(r"[\w\u00c0-\u024f']+", phrase)
    if len(words) < 5:
        return ""
    best = ""
    best_score = -1
    window = min(max_words, len(words))
    for i in range(0, max(1, len(words) - window + 1)):
        chunk = words[i: i + window]
        score = len([w for w in chunk if w.lower() not in _STOPWORDS and len(w) > 3])
        if score > best_score:
            best_score = score
            best = " ".join(chunk)
    return f'"{best}"' if best else ""


def build_search_queries(text: str, max_queries: int = 12) -> list[SearchQuery]:
    queries: list[SearchQuery] = []
    seen: set[str] = set()

    def _add(thesis_phrase: str, search_query: str) -> None:
        phrase = re.sub(r"\s+", " ", thesis_phrase.strip())
        sq = re.sub(r"\s+", " ", (search_query or "").strip())
        if len(phrase) < 28 or len(sq) < 10:
            return
        key = _normalize(sq)[:140]
        if key in seen:
            return
        seen.add(key)
        queries.append(SearchQuery(thesis_phrase=phrase[:500], search_query=sq[:200]))

    candidates: list[str] = []
    for block in re.split(r"\n{2,}", text or ""):
        block = re.sub(r"\s+", " ", block.strip())
        if 40 <= len(block) <= 600:
            candidates.append(block)
    for line in (text or "").splitlines():
        line = re.sub(r"\s+", " ", line.strip())
        if 40 <= len(line) <= 420:
            candidates.append(line)
    for sent in re.split(r"(?<=[.!?])\s+", text or ""):
        sent = re.sub(r"\s+", " ", sent.strip())
        if 40 <= len(sent) <= 420:
            candidates.append(sent)

    compact = re.sub(r"\s+", " ", (text or "").strip())
    words_all = compact.split()
    if len(words_all) >= 30:
        for i in range(0, min(len(words_all) - 20, 500), 16):
            window = " ".join(words_all[i: i + 24])
            if len(window) >= 45:
                candidates.append(window)

    candidates.sort(key=lambda s: (len(_content_words(s)), len(s)), reverse=True)

    for phrase in candidates:
        quote = _short_quote(phrase, max_words=9)
        if quote:
            _add(phrase, quote)
        content = _content_words(phrase)
        if len(content) >= 5:
            _add(phrase, " ".join(content[:10]))
        if len(content) >= 8:
            _add(phrase, " ".join(content[:14]))
        if len(queries) >= max_queries * 2:
            break

    return queries[:max_queries]


def verify_url(url: str) -> bool:
    return not is_blocked_url(url)


def _fetch_page_text(url: str) -> tuple[str, str]:
    try:
        resp = requests.get(
            url,
            timeout=FETCH_TIMEOUT,
            allow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        )
        if resp.status_code != 200:
            return "", url
        final_url = resp.url or url
        html = resp.text or ""
        if len(html) < 40:
            return "", final_url
        try:
            import trafilatura
            extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
            if extracted and len(extracted) > 60:
                return re.sub(r"\s+", " ", extracted), final_url
        except Exception:
            pass
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
        return text, final_url
    except Exception as exc:
        logger.debug("Fetch failed %s: %s", url[:80], exc)
        return "", url


def find_passage_on_page(page_text: str, thesis_phrase: str) -> tuple[str, float, bool] | None:
    if not page_text or not thesis_phrase:
        return None

    phrase = _strip_quotes(thesis_phrase)
    page_norm = _normalize(page_text)
    phrase_norm = _normalize(phrase)

    for probe in (phrase_norm, phrase_norm[:140], phrase_norm[:90]):
        if len(probe) >= 24 and probe in page_norm:
            idx = page_norm.index(probe)
            raw = page_text[max(0, idx - 20): idx + len(probe) + 40]
            return raw.strip()[:900], 100.0 if probe == phrase_norm else 94.0, True

    try:
        from rapidfuzz import fuzz
    except ImportError:
        return None

    best_score = 0.0
    best_sent = ""
    phrase_words = set(_content_words(phrase))

    chunks = re.split(r"(?<=[.!?])\s+", page_text)
    if len(chunks) < 3:
        # long pages without punctuation — sliding windows
        words = page_text.split()
        chunks = [" ".join(words[i: i + 35]) for i in range(0, max(1, len(words) - 20), 20)]

    for sent in chunks:
        sent = sent.strip()
        if len(sent) < 25:
            continue
        sent_norm = _normalize(sent)
        partial = float(fuzz.partial_ratio(phrase_norm[:280], sent_norm[:400]))
        token_set = float(fuzz.token_set_ratio(phrase_norm[:280], sent_norm[:400]))
        score = max(partial * 0.98, token_set * 0.95)
        overlap = len(phrase_words & set(_content_words(sent))) / max(len(phrase_words), 1)
        if overlap >= 0.40:
            score = max(score, 62 + overlap * 35)
        if score > best_score:
            best_score = score
            best_sent = sent

    if best_score >= 62 and _word_overlap_ratio(phrase, best_sent) >= 0.32:
        return best_sent.strip()[:900], best_score, best_score >= 90
    return None


def _snippet_verify_score(thesis_phrase: str, snippet: str) -> tuple[float, bool, float]:
    phrase = _strip_quotes(thesis_phrase)
    try:
        from rapidfuzz import fuzz
        score = float(fuzz.partial_ratio(_normalize(phrase[:320]), _normalize(snippet)))
        token = float(fuzz.token_set_ratio(_normalize(phrase[:320]), _normalize(snippet)))
        score = max(score, token * 0.97)
    except ImportError:
        score = _word_overlap_ratio(phrase, snippet) * 100
    overlap = _word_overlap_ratio(phrase, snippet)
    is_exact = score >= 88 and overlap >= 0.55
    return score, is_exact, overlap


def verify_hit(hit: SearchHit, thesis_phrase: str, *, fetch_page: bool = True) -> dict[str, Any] | None:
    """
    Verify source. FAST PATH: Serper/Google snippet match first (no network hang).
    Optional page fetch upgrades confidence when it works quickly.
    """
    phrase = _strip_quotes(thesis_phrase)
    if len(phrase) < 24:
        return None
    if is_blocked_url(hit.url):
        return None

    # --- FAST PATH: snippet from Search API ---
    snippet_result = None
    if hit.snippet and len(hit.snippet) >= 30:
        verify_score, is_exact, overlap = _snippet_verify_score(phrase, hit.snippet)
        # Also compare against shorter windows of thesis phrase (Google truncates snippets)
        if verify_score < 70:
            for window_words in (12, 9, 7):
                words = phrase.split()
                if len(words) < window_words:
                    continue
                mid = " ".join(words[:window_words])
                s2, e2, o2 = _snippet_verify_score(mid, hit.snippet)
                if s2 > verify_score:
                    verify_score, is_exact, overlap = s2, e2, o2
        if is_exact or (verify_score >= 58 and overlap >= 0.30) or (verify_score >= 70):
            snippet_result = {
                "url": hit.url,
                "title": (hit.title or "").strip() or urlparse(hit.url).netloc.replace("www.", ""),
                "snippet": hit.snippet[:1200],
                "verified": bool(is_exact or verify_score >= 82),
                "verify_score": round(verify_score, 1),
                "word_overlap": round(overlap * 100, 1),
                "exact_match": is_exact,
                "provider": hit.provider,
                "search_snippet": hit.snippet[:400],
                "thesis_phrase": phrase,
                "snippet_only": True,
            }

    # --- SLOW PATH: fetch page only if needed / to upgrade ---
    if fetch_page and (not snippet_result or not snippet_result.get("verified")):
        page_text, final_url = _fetch_page_text(hit.url)
        if page_text:
            passage_result = find_passage_on_page(page_text, phrase)
            if passage_result:
                passage, verify_score, is_exact = passage_result
                word_overlap = _word_overlap_ratio(phrase, passage)
                if is_exact or (verify_score >= 60 and word_overlap >= 0.30):
                    return {
                        "url": final_url,
                        "title": (hit.title or "").strip() or urlparse(final_url).netloc.replace("www.", ""),
                        "snippet": passage[:1200],
                        "verified": True,
                        "verify_score": round(verify_score, 1),
                        "word_overlap": round(word_overlap * 100, 1),
                        "exact_match": is_exact,
                        "provider": hit.provider,
                        "search_snippet": (hit.snippet or "")[:400],
                        "thesis_phrase": phrase,
                        "snippet_only": False,
                    }

    return snippet_result


def search_text_for_sources(
    text: str,
    *,
    max_queries: int = 10,
    max_results: int = 10,
    max_verify: int = 16,
) -> list[dict[str, Any]]:
    """Search web and return sources with accurate URLs."""
    # Limit text for query generation (full doc is huge)
    probe = (text or "")[:25000]
    query_objs = build_search_queries(probe, max_queries=max_queries)
    if not query_objs:
        logger.warning("No search queries generated from text")
        return []

    logger.info("Web search: %d phrase queries generated", len(query_objs))
    seen_urls: set[str] = set()
    raw_hits: list[tuple[SearchQuery, SearchHit]] = []
    deadline = time.monotonic() + SEARCH_BUDGET_SEC

    for sq in query_objs:
        if time.monotonic() >= deadline or len(raw_hits) >= max_verify:
            break
        variants = [sq.search_query.strip()]
        if variants[0].startswith('"') and variants[0].endswith('"'):
            content = _content_words(variants[0].strip('"'))
            if len(content) >= 5:
                variants.append(" ".join(content[:10]))

        for variant in variants:
            if time.monotonic() >= deadline:
                break
            hits = web_search(variant, max_results=5)
            if not hits and variant.startswith('"'):
                words = re.findall(r"[\w\u00c0-\u024f']+", variant.strip('"'))
                if len(words) >= 6:
                    hits = web_search(" ".join(words[:8]), max_results=5)
            for hit in hits:
                norm = normalize_url_key(hit.url)
                if norm in seen_urls or is_blocked_url(hit.url):
                    continue
                seen_urls.add(norm)
                raw_hits.append((sq, hit))
                if len(raw_hits) >= max_verify:
                    break
            if hits:
                break

    if not raw_hits:
        logger.warning("Web search returned no URLs for any query")
        return []

    logger.info("Web search: %d candidate URLs to verify", len(raw_hits))

    verified_matches: list[dict[str, Any]] = []

    def _job(item: tuple[SearchQuery, SearchHit]) -> dict[str, Any] | None:
        sq, hit = item
        # Snippet-first; page fetch only for top candidates later
        return verify_hit(hit, sq.thesis_phrase, fetch_page=False)

    # Parallel snippet verify (no hanging page fetches)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_job, item): item for item in raw_hits}
        try:
            for fut in as_completed(futures, timeout=20):
                if time.monotonic() >= deadline:
                    break
                try:
                    result = fut.result(timeout=3)
                except Exception:
                    continue
                if not result:
                    continue
                sq0 = futures[fut][0]
                result["query"] = sq0.search_query
                result["thesis_phrase"] = sq0.thesis_phrase
                verified_matches.append(result)
                if len(verified_matches) >= max_results:
                    break
        except TimeoutError:
            logger.warning("Web verify timed out with %d matches so far", len(verified_matches))

    # Mos bëj page-fetch të rëndë këtu — shkakton hang (PDF/JS sites) dhe Web=0%.
    # Snippet nga Serper/Google mjafton për të treguar URL-në e burimit.

    verified_matches.sort(
        key=lambda x: (
            x.get("exact_match", False),
            x.get("verified", False),
            x.get("verify_score", 0),
            x.get("word_overlap", 0),
        ),
        reverse=True,
    )
    logger.info("Web search: %d matches with URLs", len(verified_matches))
    return verified_matches[:max_results]


def search_paragraph(paragraph: str, max_results: int = 3) -> list[dict]:
    return search_text_for_sources(
        paragraph,
        max_queries=4,
        max_results=max_results,
        max_verify=max_results + 4,
    )


def extract_snippet(url: str, query_words: set[str]) -> str | None:
    page, _ = _fetch_page_text(url)
    if not page:
        return None
    query = " ".join(list(query_words)[:20])
    found = find_passage_on_page(page, query)
    return found[0][:500] if found else page[:400]
