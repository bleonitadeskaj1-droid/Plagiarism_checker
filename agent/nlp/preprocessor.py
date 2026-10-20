"""Albanian + English text preprocessing for academic plagiarism detection."""

from __future__ import annotations

import re
import unicodedata

try:
    import nltk
    from nltk.corpus import stopwords

    try:
        stopwords.words("english")
    except LookupError:
        nltk.download("stopwords", quiet=True)
    _EN_STOP = set(stopwords.words("english"))
except Exception:
    _EN_STOP = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of",
        "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
        "do", "does", "did", "will", "would", "could", "should", "may", "might",
        "this", "that", "these", "those", "with", "from", "by", "as", "into",
    }

# Common Albanian function words (academic + general)
ALBANIAN_STOPWORDS = {
    "dhe", "edhe", "ose", "por", "se", "që", "qe", "në", "ne", "nga", "me", "për",
    "per", "një", "nje", "të", "te", "i", "e", "së", "se", "nuk", "pa", "si",
    "kur", "ku", "kjo", "ky", "këto", "keto", "ata", "ato", "ai", "ajo", "unë",
    "une", "ti", "ne", "ju", "ishte", "janë", "jane", "është", "eshte", "ka",
    "kanë", "kane", "do", "mund", "duhet", "më", "me", "shumë", "shume", "pak",
    "tjetër", "tjeter", "tjetra", "gjithë", "gjithe", "çdo", "cdo", "disa",
    "sepse", "pra", "kështu", "keshtu", "atëherë", "atehere", "ndërsa", "ndersa",
    "megjithatë", "megjithate", "gjithashtu", "vetëm", "vetem", "edhe", "apo",
    "deri", "pas", "para", "mbi", "nën", "nen", "midis", "kundër", "kunder",
    "sipas", "përmes", "permes", "rreth", "gjatë", "gjate", "brenda", "jashtë",
    "jashte", "tani", "sot", "nesër", "neser", "dje", "viti", "viteve", "tema",
    "punim", "kapitull", "pjesa", "figurë", "figure", "tabela",
}

ACADEMIC_STOPWORDS_SQ = {
    "hyrje", "përfundim", "perfundim", "literaturë", "literature", "bibliografi",
    "referenca", "metodologji", "analiza", "rezultatet", "diskutim", "abstrakt",
}

_STOP = _EN_STOP | ALBANIAN_STOPWORDS | ACADEMIC_STOPWORDS_SQ
TOKEN_RE = re.compile(r"\b[\w']+\b", re.UNICODE)


def clean_text(text: str) -> str:
    text = (text or "").replace("\x00", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fold_albanian(text: str) -> str:
    """NFKC + strip combining marks so ë/ç variants match more reliably."""
    text = unicodedata.normalize("NFKC", text or "")
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    return text


def tokenize(text: str, *, fold: bool = False) -> list[str]:
    src = fold_albanian(text).lower() if fold else (text or "").lower()
    return TOKEN_RE.findall(src)


def light_stem_sq(token: str) -> str:
    """Very light suffix trim for Albanian — avoids over-stemming short tokens."""
    if len(token) < 6:
        return token
    folded = fold_albanian(token)
    for suf in ("eve", "it", "in", "ia", "ve", "et", "es", "en"):
        if folded.endswith(suf) and len(folded) - len(suf) >= 4:
            return folded[: -len(suf)]
    return folded


def normalize(text: str, *, fold: bool = True, stem_sq: bool = True) -> str:
    tokens = []
    for t in tokenize(text, fold=fold):
        if t in _STOP or len(t) <= 1:
            continue
        tokens.append(light_stem_sq(t) if stem_sq else t)
    return " ".join(tokens)


def split_paragraphs(text: str, min_len: int = 40) -> list[str]:
    chunks = re.split(r"\n\s*\n+", text or "")
    paragraphs = []
    for chunk in chunks:
        chunk = clean_text(chunk)
        if len(chunk) >= min_len:
            paragraphs.append(chunk)
    if not paragraphs and (text or "").strip():
        paragraphs = [clean_text(text)]
    return paragraphs


def chunk_by_words(text: str, chunk_words: int = 1000) -> list[str]:
    words = (text or "").split()
    if len(words) <= chunk_words:
        return [text or ""]
    return [" ".join(words[i : i + chunk_words]) for i in range(0, len(words), chunk_words)]


def detect_language_hint(text: str) -> str:
    """Heuristic: Albanian if ë/ç or many SQ stopwords."""
    sample = (text or "")[:4000]
    if re.search(r"[ëçËÇ]", sample):
        return "sq"
    lower = sample.lower()
    hits = sum(1 for w in ("dhe", "në", "për", "që", "një", "është", "punim") if f" {w} " in f" {lower} ")
    return "sq" if hits >= 3 else "en"


def prepare_for_matching(text: str) -> str:
    """Canonical form for lexical matching of Albanian academic text."""
    return normalize(clean_text(text), fold=True, stem_sq=True)
