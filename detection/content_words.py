"""Function words / conjunctions excluded from plagiarism percentage counts.

These still appear in the document text and highlights, but do not inflate
unique-word coverage scores. Content words only drive the reported %.
"""

from __future__ import annotations

import re
import unicodedata

# Albanian conjunctions, prepositions, pronouns, auxiliaries (NOT content nouns)
FUNCTION_WORDS_SQ = frozenset({
    "dhe", "edhe", "ose", "apo", "por", "se", "qe", "që",
    "e", "i", "a", "u", "o",
    "ne", "në", "nga", "me", "per", "për", "te", "të", "se", "së",
    "nje", "një", "nuk", "pa", "si", "kur", "ku",
    "kjo", "ky", "keto", "këto", "ata", "ato", "ai", "ajo",
    "une", "unë", "ti", "ju", "ne",
    "ishte", "jane", "janë", "eshte", "është", "ka", "kane", "kanë",
    "do", "mund", "duhet", "me", "më",
    "pra", "sepse", "ndersa", "ndërsa", "keshtu", "kështu",
    "atehere", "atëherë", "megjithate", "megjithatë", "gjithashtu",
    "vetem", "vetëm", "deri", "pas", "para", "mbi", "nen", "nën",
    "midis", "kunder", "kundër", "sipas", "permes", "përmes",
    "rreth", "gjate", "gjatë", "brenda", "jashte", "jashtë",
    "tani", "sot", "pak", "shume", "shumë", "disa", "cdo", "çdo",
    "tjeter", "tjetër", "tjetra", "gjithe", "gjithë",
    "sa", "qe", "cila", "cili", "cilat", "cilet", "çila", "çili",
    "ma", "s", "te", "tu", "ton", "jon",
})

FUNCTION_WORDS_EN = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "then", "so", "as",
    "of", "to", "in", "on", "at", "for", "with", "from", "by", "into",
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "must", "can",
    "this", "that", "these", "those", "it", "its", "they", "them", "their",
    "he", "she", "his", "her", "we", "our", "you", "your", "i", "my",
    "not", "no", "nor", "than", "too", "very", "just", "also", "only",
    "about", "over", "under", "after", "before", "between", "through",
    "which", "who", "whom", "what", "when", "where", "how", "why",
    "there", "here", "such", "same", "other", "another", "each", "every",
    "both", "all", "any", "some", "more", "most", "own",
})

FUNCTION_WORDS = FUNCTION_WORDS_SQ | FUNCTION_WORDS_EN

_TOKEN_RE = re.compile(r"[\w\u00c0-\u024f]+", re.UNICODE)


def fold_token(token: str) -> str:
    text = unicodedata.normalize("NFKC", (token or "").lower())
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def is_function_word(token: str) -> bool:
    t = fold_token(token)
    if not t:
        return True
    if len(t) <= 1:
        return True
    return t in FUNCTION_WORDS


def content_tokens(text: str) -> list[str]:
    """Tokens that count toward plagiarism coverage (excludes lidhëza / function words)."""
    out: list[str] = []
    for raw in _TOKEN_RE.findall(text or ""):
        if is_function_word(raw):
            continue
        out.append(fold_token(raw))
    return out


def content_token_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of content words (same tokens as content_tokens)."""
    spans: list[tuple[int, int]] = []
    for match in _TOKEN_RE.finditer(text or ""):
        if is_function_word(match.group(0)):
            continue
        spans.append((match.start(), match.end()))
    return spans


def content_text(text: str) -> str:
    return " ".join(content_tokens(text))
