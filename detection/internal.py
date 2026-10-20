"""Fast internal duplicate detection — exact hash + MinHash near-duplicate."""

from __future__ import annotations

import hashlib
import re
from typing import Any

logger = __import__("logging").getLogger(__name__)

_MINHASH_PERM = 128


def _normalize_fingerprint(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").lower())
    return re.sub(r"[^\w\s]", "", text, flags=re.UNICODE).strip()


def content_fingerprint(text: str) -> str:
    """Stable hash for identical document detection."""
    normalized = _normalize_fingerprint(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _shingle_tokens(text: str, n: int = 5) -> set[str]:
    words = _normalize_fingerprint(text).split()
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def _minhash_jaccard(text_a: str, text_b: str) -> float:
    """MinHash Jaccard estimate — fast near-duplicate score (0–100)."""
    shingles_a = _shingle_tokens(text_a)
    shingles_b = _shingle_tokens(text_b)
    if not shingles_a or not shingles_b:
        return 0.0

    try:
        from datasketch import MinHash

        def _build(shingles: set[str]) -> MinHash:
            mh = MinHash(num_perm=_MINHASH_PERM)
            for sh in shingles:
                mh.update(sh.encode("utf-8"))
            return mh

        mh_a = _build(shingles_a)
        mh_b = _build(shingles_b)
        return round(float(mh_a.jaccard(mh_b)) * 100.0, 2)
    except Exception as exc:
        logger.debug("MinHash failed, using shingle Jaccard: %s", exc)
        inter = len(shingles_a & shingles_b)
        union = len(shingles_a | shingles_b)
        return round(inter / union * 100.0, 2) if union else 0.0


def quick_internal_score(source: str, target: str) -> dict[str, Any]:
    """
    Fast internal pre-score before full ensemble.
    Returns final_score 100 for identical docs, high for near-duplicates.
    """
    if not source.strip() or not target.strip():
        return {"final_score": 0.0, "method": "empty", "exact_duplicate": False}

    fp_a = content_fingerprint(source)
    fp_b = content_fingerprint(target)
    if fp_a == fp_b:
        excerpt = source.strip()[:280]
        return {
            "final_score": 100.0,
            "method": "exact_hash",
            "exact_duplicate": True,
            "minhash_score": 100.0,
            "thesis_excerpt": excerpt,
            "source_excerpt": target.strip()[:280],
            "confidence": "very_high",
        }

    mh = _minhash_jaccard(source, target)
    if mh >= 92.0:
        return {
            "final_score": round(min(99.5, mh), 2),
            "method": "minhash_near_duplicate",
            "exact_duplicate": False,
            "minhash_score": mh,
            "thesis_excerpt": source.strip()[:280],
            "source_excerpt": target.strip()[:280],
            "confidence": "very_high",
        }
    if mh >= 75.0:
        return {
            "final_score": round(mh * 0.95, 2),
            "method": "minhash_high",
            "exact_duplicate": False,
            "minhash_score": mh,
            "thesis_excerpt": source.strip()[:280],
            "source_excerpt": target.strip()[:280],
            "confidence": "high",
        }

    return {
        "final_score": 0.0,
        "method": "none",
        "exact_duplicate": False,
        "minhash_score": mh,
    }
