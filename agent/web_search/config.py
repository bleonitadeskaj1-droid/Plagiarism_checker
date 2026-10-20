"""Web search API configuration and availability checks."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# Official Search APIs only — no DuckDuckGo/SearXNG unless WEB_SEARCH_ALLOW_FALLBACK=1
OFFICIAL_PROVIDERS = (
    "serper",
    "serpapi",
    "brave",
    "tavily",
    "bing",
    "google_cse",
)

# Academic databases (no API key required for basic search)
ACADEMIC_PROVIDERS = (
    "openalex",
    "crossref",
    "semantic_scholar",
)

PROVIDER_SETUP = {
    "serper": {
        "env": "SERPER_API_KEY",
        "url": "https://serper.dev/",
        "note": "Google Search · ~2500 falas/muaj — rekomandohet",
    },
    "brave": {
        "env": "BRAVE_SEARCH_API_KEY",
        "url": "https://brave.com/search/api/",
        "note": "Brave Search · 2000 falas/muaj",
    },
    "serpapi": {
        "env": "SERPAPI_KEY",
        "url": "https://serpapi.com/manage-api-key",
        "note": "Google via SerpAPI · 100 falas/muaj",
    },
    "tavily": {
        "env": "TAVILY_API_KEY",
        "url": "https://tavily.com/",
        "note": "Tavily · 1000 falas/muaj",
    },
    "bing": {
        "env": "BING_SEARCH_API_KEY",
        "url": "https://portal.azure.com/",
        "note": "Bing Search v7 (Azure)",
    },
    "google_cse": {
        "env": "GOOGLE_CSE_API_KEY + GOOGLE_CSE_ID",
        "url": "https://programmablesearchengine.google.com/",
        "note": "Google Custom Search · 100/ditë falas",
    },
    "openalex": {
        "env": "(opsional OPENALEX_EMAIL)",
        "url": "https://openalex.org/",
        "note": "OpenAlex — katalog akademik falas",
    },
    "crossref": {
        "env": "(opsional CROSSREF_MAILTO)",
        "url": "https://www.crossref.org/",
        "note": "Crossref DOI / metadata akademike",
    },
    "semantic_scholar": {
        "env": "(opsional SEMANTIC_SCHOLAR_API_KEY)",
        "url": "https://www.semanticscholar.org/product/api",
        "note": "Semantic Scholar — papers & abstracts",
    },
}

DEFAULT_SEARXNG_URL = "https://searx.be"


def _has(key: str) -> bool:
    return bool(os.getenv(key, "").strip())


def get_searxng_url() -> str:
    return (os.getenv("SEARXNG_URL") or DEFAULT_SEARXNG_URL).strip().rstrip("/")


def get_academic_providers() -> list[str]:
    """Academic sources — enabled by default unless ACADEMIC_SEARCH=0."""
    if os.getenv("ACADEMIC_SEARCH", "1").lower() in {"0", "false", "no"}:
        return []
    return list(ACADEMIC_PROVIDERS)


def get_official_providers() -> list[str]:
    """Return list of configured official search API providers (best first)."""
    providers: list[str] = []
    # Serper/SerpAPI/Brave — rezultate Google/web të forta për fraza akademike
    if _has("SERPER_API_KEY"):
        providers.append("serper")
    if _has("SERPAPI_KEY"):
        providers.append("serpapi")
    if _has("BRAVE_SEARCH_API_KEY"):
        providers.append("brave")
    if _has("TAVILY_API_KEY"):
        providers.append("tavily")
    if _has("BING_SEARCH_API_KEY"):
        providers.append("bing")
    if _has("GOOGLE_CSE_API_KEY") and _has("GOOGLE_CSE_ID"):
        providers.append("google_cse")
    return providers


def _allow_fallback() -> bool:
    return os.getenv("WEB_SEARCH_ALLOW_FALLBACK", "").lower() in {"1", "true", "yes"}


def get_configured_providers() -> list[str]:
    """Providers for web search — official APIs + academic databases."""
    providers: list[str] = []
    official = get_official_providers()
    providers.extend(official)
    # Academic sources always appended (no key required)
    for p in get_academic_providers():
        if p not in providers:
            providers.append(p)
    if providers:
        return providers
    if _allow_fallback():
        fallback: list[str] = []
        if _has("SEARXNG_URL"):
            fallback.append("searxng")
        fallback.append("duckduckgo")
        fallback.extend(get_academic_providers())
        return fallback
    return list(get_academic_providers())


def require_api_providers() -> bool:
    """True when project policy requires a paid/free Search API key."""
    return os.getenv("WEB_SEARCH_REQUIRE_API", "1").lower() in {"1", "true", "yes"}


def is_web_search_available() -> bool:
    """True when at least one search provider is configured (incl. academic)."""
    if get_configured_providers():
        return True
    if require_api_providers():
        return bool(get_official_providers())
    return False


def setup_instructions() -> str:
    lines = [
        "Vendosni NJË nga këta çelësa në .env (mjafton një), ose përdorni burimet akademike falas:",
        "• SERPER_API_KEY — https://serper.dev/ (Google, rekomandohet)",
        "• BRAVE_SEARCH_API_KEY — https://brave.com/search/api/",
        "• SERPAPI_KEY — https://serpapi.com/manage-api-key",
        "• TAVILY_API_KEY — https://tavily.com/",
        "• BING_SEARCH_API_KEY — Azure Bing Search v7",
        "• GOOGLE_CSE_API_KEY + GOOGLE_CSE_ID — Google Custom Search",
        "• OpenAlex / Crossref / Semantic Scholar — aktivë automatikisht (ACADEMIC_SEARCH=1)",
    ]
    return " ".join(lines)


def get_web_search_status() -> dict[str, Any]:
    """Status payload for API / UI."""
    official = get_official_providers()
    academic = get_academic_providers()
    configured = get_configured_providers()
    api_required = require_api_providers()
    using_fallback = _allow_fallback() and not official and bool(configured)

    if official or academic:
        parts = []
        if official:
            parts.append("API: " + ", ".join(official))
        if academic:
            parts.append("akademike: " + ", ".join(academic))
        message = "Kërkimi web aktiv — " + " · ".join(parts)
        setup_hint = None
    elif api_required:
        message = (
            "Kërkimi web kërkon Search API key "
            "(Serper, Brave, SerpAPI, Tavily, Bing ose Google CSE)."
        )
        setup_hint = (
            "Më e lehta: https://serper.dev → Sign up → kopjoni çelësin në .env si "
            "SERPER_API_KEY=...  (alternativa: Brave / SerpAPI / Tavily)"
        )
    elif using_fallback:
        message = "Kërkimi web përdor fallback (jo API). Aktivizoni WEB_SEARCH_REQUIRE_API=1 për prod."
        setup_hint = "Shtoni SERPER_API_KEY ose BRAVE_SEARCH_API_KEY në .env për linke të sakta."
    else:
        message = "Kërkimi web nuk është i disponueshëm."
        setup_hint = setup_instructions()

    return {
        "available": is_web_search_available(),
        "providers": configured,
        "official_providers": official,
        "academic_providers": academic,
        "api_required": api_required,
        "using_fallback": using_fallback,
        "provider_setup": PROVIDER_SETUP,
        "searxng_url": get_searxng_url() if "searxng" in configured else None,
        "message": message,
        "setup_hint": setup_hint,
    }
