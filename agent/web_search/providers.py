"""Official Search API providers for web plagiarism source discovery."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import quote_plus

import requests

from agent.web_search.config import get_configured_providers

logger = logging.getLogger(__name__)

USER_AGENT = "PlagiarismChecker/2.0 (academic research; +local)"
REQUEST_TIMEOUT = 16


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str
    provider: str


def _get(url: str, **kwargs) -> requests.Response | None:
    try:
        resp = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            **kwargs,
        )
        if resp.status_code == 200:
            return resp
        logger.debug("Search API %s returned %s", url[:80], resp.status_code)
    except Exception as exc:
        logger.debug("Search API request failed: %s", exc)
    return None


def _search_serper(query: str, max_results: int) -> Iterator[SearchHit]:
    """Serper.dev — Google Search API (falas ~2500/muaj): https://serper.dev/"""
    key = os.getenv("SERPER_API_KEY", "").strip()
    if not key:
        return
    # Query shumë të gjata me thonjëza shpesh kthejnë 0 — shkurto
    q = (query or "").strip()
    if q.startswith('"') and q.endswith('"') and len(q) > 120:
        inner = q.strip('"')
        words = inner.split()
        q = '"' + " ".join(words[:12]) + '"'
    elif len(q) > 180:
        q = " ".join(q.split()[:18])
    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            json={"q": q, "num": min(max(max_results, 5), 10)},
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": USER_AGENT,
                "X-API-KEY": key,
                "Content-Type": "application/json",
            },
        )
        if resp.status_code != 200:
            logger.warning("Serper returned %s for query len=%s", resp.status_code, len(q))
            return
    except Exception as exc:
        logger.warning("Serper search failed: %s", exc)
        return
    data = resp.json() or {}
    for item in data.get("organic") or []:
        link = (item.get("link") or "").strip()
        if not link.startswith("http"):
            continue
        # Prefer longer snippet text for verification
        snip = item.get("snippet") or item.get("attributes") or ""
        if isinstance(snip, dict):
            snip = " ".join(str(v) for v in snip.values())
        yield SearchHit(
            url=link,
            title=(item.get("title") or link)[:300],
            snippet=str(snip)[:1200],
            provider="serper",
        )


def _search_tavily(query: str, max_results: int) -> Iterator[SearchHit]:
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        return
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": key,
                "query": query,
                "search_depth": "advanced",
                "max_results": max_results,
                "include_answer": False,
                "include_raw_content": True,
            },
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        if resp.status_code != 200:
            return
    except Exception as exc:
        logger.debug("Tavily search failed: %s", exc)
        return
    for item in resp.json().get("results") or []:
        url = (item.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        raw = item.get("raw_content") or item.get("content") or item.get("snippet") or ""
        yield SearchHit(
            url=url,
            title=(item.get("title") or url)[:300],
            snippet=(raw or "")[:1200],
            provider="tavily",
        )


def _search_serpapi(query: str, max_results: int) -> Iterator[SearchHit]:
    key = os.getenv("SERPAPI_KEY", "").strip()
    if not key:
        return
    url = (
        "https://serpapi.com/search.json?"
        f"engine=google&q={quote_plus(query)}&num={max_results}&api_key={quote_plus(key)}"
    )
    resp = _get(url)
    if resp is None:
        return
    for item in resp.json().get("organic_results") or []:
        link = (item.get("link") or "").strip()
        if not link.startswith("http"):
            continue
        yield SearchHit(
            url=link,
            title=(item.get("title") or link)[:300],
            snippet=(item.get("snippet") or "")[:1200],
            provider="serpapi",
        )


def _search_google_cse(query: str, max_results: int) -> Iterator[SearchHit]:
    api_key = os.getenv("GOOGLE_CSE_API_KEY", "").strip()
    cse_id = os.getenv("GOOGLE_CSE_ID", "").strip()
    if not api_key or not cse_id:
        return
    url = (
        "https://www.googleapis.com/customsearch/v1?"
        f"key={quote_plus(api_key)}&cx={quote_plus(cse_id)}"
        f"&q={quote_plus(query)}&num={min(max_results, 10)}"
    )
    resp = _get(url)
    if resp is None:
        return
    for item in resp.json().get("items") or []:
        link = (item.get("link") or "").strip()
        if not link.startswith("http"):
            continue
        yield SearchHit(
            url=link,
            title=(item.get("title") or link)[:300],
            snippet=(item.get("snippet") or "")[:1200],
            provider="google_cse",
        )


def _search_brave(query: str, max_results: int) -> Iterator[SearchHit]:
    key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    if not key:
        return
    url = (
        "https://api.search.brave.com/res/v1/web/search?"
        f"q={quote_plus(query)}&count={max_results}"
    )
    try:
        resp = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "X-Subscription-Token": key,
            },
        )
        if resp.status_code != 200:
            return
    except Exception as exc:
        logger.debug("Brave search failed: %s", exc)
        return
    for item in resp.json().get("web", {}).get("results") or []:
        link = (item.get("url") or "").strip()
        if not link.startswith("http"):
            continue
        yield SearchHit(
            url=link,
            title=(item.get("title") or link)[:300],
            snippet=(item.get("description") or "")[:1200],
            provider="brave",
        )


def _search_bing(query: str, max_results: int) -> Iterator[SearchHit]:
    key = os.getenv("BING_SEARCH_API_KEY", "").strip()
    if not key:
        return
    url = (
        "https://api.bing.microsoft.com/v7.0/search?"
        f"q={quote_plus(query)}&count={max_results}&textDecorations=false&textFormat=Raw"
    )
    try:
        resp = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Ocp-Apim-Subscription-Key": key},
        )
        if resp.status_code != 200:
            return
    except Exception as exc:
        logger.debug("Bing search failed: %s", exc)
        return
    for item in resp.json().get("webPages", {}).get("value") or []:
        link = (item.get("url") or "").strip()
        if not link.startswith("http"):
            continue
        yield SearchHit(
            url=link,
            title=(item.get("name") or link)[:300],
            snippet=(item.get("snippet") or "")[:1200],
            provider="bing",
        )


def _search_duckduckgo(query: str, max_results: int) -> Iterator[SearchHit]:
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            logger.debug("ddgs package not installed")
            return

    try:
        with DDGS() as ddgs:
            results = ddgs.text(query, max_results=max_results)
            if results is None:
                return
            for item in results:
                url = (item.get("href") or item.get("url") or "").strip()
                if not url.startswith("http"):
                    continue
                yield SearchHit(
                    url=url,
                    title=(item.get("title") or url)[:300],
                    snippet=(item.get("body") or item.get("snippet") or "")[:1200],
                    provider="duckduckgo",
                )
    except Exception as exc:
        logger.debug("DuckDuckGo search failed: %s", exc)


def _search_searxng(query: str, max_results: int) -> Iterator[SearchHit]:
    from agent.web_search.config import get_searxng_url

    base = get_searxng_url()
    url = f"{base}/search?q={quote_plus(query)}&format=json&language=auto"
    try:
        resp = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        if resp.status_code != 200:
            logger.debug("SearXNG %s returned %s", base, resp.status_code)
            return
        data = resp.json()
    except Exception as exc:
        logger.debug("SearXNG search failed (%s): %s", base, exc)
        return

    for item in (data.get("results") or [])[:max_results]:
        link = (item.get("url") or "").strip()
        if not link.startswith("http"):
            continue
        yield SearchHit(
            url=link,
            title=(item.get("title") or link)[:300],
            snippet=(item.get("content") or item.get("snippet") or "")[:1200],
            provider="searxng",
        )


def _search_openalex(query: str, max_results: int) -> Iterator[SearchHit]:
    """OpenAlex — free scholarly catalog: https://openalex.org/"""
    q = (query or "").strip().strip('"')
    if len(q) < 8:
        return
    email = os.getenv("OPENALEX_EMAIL", "").strip() or "plagiarism-checker@localhost"
    params = {
        "search": " ".join(q.split()[:20]),
        "per_page": min(max(max_results, 3), 8),
        "mailto": email,
    }
    resp = _get("https://api.openalex.org/works", params=params)
    if not resp:
        return
    data = resp.json() or {}
    for item in data.get("results") or []:
        primary = item.get("primary_location") or {}
        link = (
            (primary.get("landing_page_url") or "")
            or (item.get("doi") and f"https://doi.org/{str(item['doi']).replace('https://doi.org/', '')}")
            or (item.get("id") or "")
        )
        link = str(link).strip()
        if not link.startswith("http"):
            continue
        title = (item.get("display_name") or link)[:300]
        abstract = ""
        inv = item.get("abstract_inverted_index")
        if isinstance(inv, dict) and inv:
            # Reconstruct approximate abstract from inverted index
            positions: list[tuple[int, str]] = []
            for word, idxs in inv.items():
                for i in idxs or []:
                    positions.append((int(i), word))
            positions.sort()
            abstract = " ".join(w for _, w in positions[:120])
        yield SearchHit(
            url=link,
            title=title,
            snippet=(abstract or title)[:1200],
            provider="openalex",
        )


def _search_crossref(query: str, max_results: int) -> Iterator[SearchHit]:
    """Crossref works API — DOI metadata: https://api.crossref.org/"""
    q = (query or "").strip().strip('"')
    if len(q) < 8:
        return
    mailto = os.getenv("CROSSREF_MAILTO", "").strip() or "plagiarism-checker@localhost"
    params = {
        "query": " ".join(q.split()[:18]),
        "rows": min(max(max_results, 3), 8),
        "mailto": mailto,
    }
    resp = _get("https://api.crossref.org/works", params=params)
    if not resp:
        return
    data = (resp.json() or {}).get("message") or {}
    for item in data.get("items") or []:
        doi = (item.get("DOI") or "").strip()
        link = f"https://doi.org/{doi}" if doi else (item.get("URL") or "")
        if not str(link).startswith("http"):
            continue
        titles = item.get("title") or []
        title = (titles[0] if titles else link)[:300]
        abstract = re.sub(r"<[^>]+>", " ", item.get("abstract") or "")
        container = ""
        cont = item.get("container-title") or []
        if cont:
            container = cont[0]
        snippet = (abstract or container or title)[:1200]
        yield SearchHit(
            url=str(link),
            title=str(title),
            snippet=snippet,
            provider="crossref",
        )


def _search_semantic_scholar(query: str, max_results: int) -> Iterator[SearchHit]:
    """Semantic Scholar Graph API — https://api.semanticscholar.org/"""
    q = (query or "").strip().strip('"')
    if len(q) < 8:
        return
    headers = {"User-Agent": USER_AGENT}
    key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if key:
        headers["x-api-key"] = key
    try:
        resp = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={
                "query": " ".join(q.split()[:18]),
                "limit": min(max(max_results, 3), 8),
                "fields": "title,abstract,url,externalIds",
            },
            timeout=REQUEST_TIMEOUT,
            headers=headers,
        )
        if resp.status_code != 200:
            logger.debug("Semantic Scholar returned %s", resp.status_code)
            return
    except Exception as exc:
        logger.debug("Semantic Scholar failed: %s", exc)
        return
    data = resp.json() or {}
    for item in data.get("data") or []:
        link = (item.get("url") or "").strip()
        if not link:
            ext = item.get("externalIds") or {}
            doi = ext.get("DOI")
            if doi:
                link = f"https://doi.org/{doi}"
        if not link.startswith("http"):
            continue
        yield SearchHit(
            url=link,
            title=(item.get("title") or link)[:300],
            snippet=(item.get("abstract") or item.get("title") or "")[:1200],
            provider="semantic_scholar",
        )


_PROVIDER_FUNCS = {
    "serper": _search_serper,
    "tavily": _search_tavily,
    "serpapi": _search_serpapi,
    "google_cse": _search_google_cse,
    "brave": _search_brave,
    "bing": _search_bing,
    "searxng": _search_searxng,
    "duckduckgo": _search_duckduckgo,
    "openalex": _search_openalex,
    "crossref": _search_crossref,
    "semantic_scholar": _search_semantic_scholar,
}

_warned_no_providers = False


def web_search(query: str, *, max_results: int = 5) -> list[SearchHit]:
    """Run query through configured Search APIs (official or SearXNG fallback)."""
    global _warned_no_providers
    query = (query or "").strip()
    if len(query) < 8:
        return []

    configured = get_configured_providers()
    if not configured:
        if not _warned_no_providers:
            logger.warning("Web search skipped: no providers configured")
            _warned_no_providers = True
        return []

    hits: list[SearchHit] = []
    seen_urls: set[str] = set()

    for provider in configured:
        search_fn = _PROVIDER_FUNCS.get(provider)
        if not search_fn:
            continue
        try:
            for hit in search_fn(query, max_results):
                norm = hit.url.rstrip("/").lower()
                if norm in seen_urls:
                    continue
                seen_urls.add(norm)
                hits.append(hit)
                if len(hits) >= max_results:
                    return hits
        except Exception as exc:
            logger.warning("Provider %s failed: %s", provider, exc)

    return hits[:max_results]
