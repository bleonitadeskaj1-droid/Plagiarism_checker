"""Aggregate plagiarism statistics — fragments, sources, word/line/paragraph counts."""

from __future__ import annotations

import re
from typing import Any

from detection.content_words import content_token_spans, content_tokens, is_function_word

MATCH_EXACT = "exact_copy"
MATCH_REORDERED = "reordered"
MATCH_PARTIAL = "partial"
MATCH_TRANSLATED = "translated"

# Similar text must not count as a full copy in the overall %.
COVERAGE_WEIGHT = {
    MATCH_EXACT: 1.00,
    MATCH_REORDERED: 0.60,
    MATCH_PARTIAL: 0.35,
    MATCH_TRANSLATED: 0.28,
}
SIMILARITY_CAP = {
    MATCH_EXACT: 100.0,
    MATCH_REORDERED: 82.0,
    MATCH_PARTIAL: 65.0,
    MATCH_TRANSLATED: 55.0,
}


def _word_tokens(text: str, *, content_only: bool = False) -> list[str]:
    tokens = re.findall(r"[\w\u00c0-\u024f]+", (text or "").lower())
    if not content_only:
        return tokens
    return [t for t in tokens if not is_function_word(t)]


def build_location_index(text: str) -> dict[str, Any]:
    """Map line numbers to paragraph and sentence indices."""
    all_lines = text.splitlines()
    line_to_para: dict[int, int] = {}
    line_to_sent: dict[int, int] = {}
    paragraphs: list[dict[str, Any]] = []
    sent_counter = 0

    para_idx = 0
    for block in re.split(r"\n{2,}", text):
        block = block.strip()
        if not block:
            continue
        para_idx += 1
        start_line = None
        end_line = None
        block_lines: list[int] = []

        for i, ln in enumerate(all_lines, start=1):
            if not ln.strip():
                continue
            if ln.strip() in block or block[:50] in ln:
                line_to_para[i] = para_idx
                block_lines.append(i)
                if start_line is None:
                    start_line = i
                end_line = i

        for sent in re.split(r"(?<=[.!?])\s+", block):
            sent = sent.strip()
            if len(sent) < 12:
                continue
            sent_counter += 1
            for i, ln in enumerate(all_lines, start=1):
                if i in line_to_sent:
                    continue
                if sent[:40] in ln or ln.strip()[:40] in sent[:80]:
                    line_to_sent[i] = sent_counter

        paragraphs.append({
            "paragraph_number": para_idx,
            "start_line": start_line or (block_lines[0] if block_lines else 1),
            "end_line": end_line or (block_lines[-1] if block_lines else 1),
            "text_preview": block[:120],
        })

    content_count = len(content_tokens(text))
    return {
        "line_to_para": line_to_para,
        "line_to_sent": line_to_sent,
        "paragraphs": paragraphs,
        "total_paragraphs": para_idx,
        "total_sentences": sent_counter,
        "total_lines": len([l for l in all_lines if l.strip()]),
        "total_words": content_count,
        "total_words_raw": len(_word_tokens(text, content_only=False)),
    }


def _matched_word_count(line_text: str, source_text: str) -> int:
    if not source_text:
        return 0
    src = set(content_tokens(source_text))
    return sum(1 for w in content_tokens(line_text) if w in src)


def enrich_highlights(thesis_text: str, highlights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add paragraph/sentence location, word counts, and detection flags."""
    index = build_location_index(thesis_text)
    enriched: list[dict[str, Any]] = []

    for hl in highlights:
        if not hl.get("match_type"):
            enriched.append(hl)
            continue
        ln = hl.get("line_number", 0)
        text = hl.get("text") or ""
        source = hl.get("source_text") or ""
        wc = len(content_tokens(text))
        mwc = _matched_word_count(text, source)
        item = {
            **hl,
            "paragraph_number": index["line_to_para"].get(ln),
            "sentence_number": index["line_to_sent"].get(ln),
            "word_count": wc,
            "matched_word_count": mwc,
            "matched_word_pct": round((mwc / wc * 100) if wc else 0, 1),
            "location_label": _location_label(
                index["line_to_para"].get(ln),
                index["line_to_sent"].get(ln),
                ln,
            ),
        }
        enriched.append(item)

    return enriched


def _location_label(para: int | None, sent: int | None, line: int) -> str:
    parts = [f"Rreshti {line}"]
    if sent:
        parts.insert(0, f"Fjalia {sent}")
    if para:
        parts.insert(0, f"Paragr. {para}")
    return " · ".join(parts)


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping / adjacent [start, end) character spans."""
    cleaned = sorted(
        ((max(0, int(a)), max(0, int(b))) for a, b in intervals if b > a),
        key=lambda x: x[0],
    )
    if not cleaned:
        return []
    merged = [cleaned[0]]
    for start, end in cleaned[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _words_in_spans(text: str, spans: list[tuple[int, int]]) -> int:
    """Count content words only (lidhëzat / function words excluded)."""
    if not text or not spans:
        return 0
    return sum(len(content_tokens(text[a:b])) for a, b in spans)


def coverage_weight(match_type: str | None, similarity: float = 0.0) -> float:
    """How much of a flagged span counts toward the overall plagiarism %."""
    if match_type == MATCH_EXACT:
        return 1.0
    if match_type in COVERAGE_WEIGHT:
        return COVERAGE_WEIGHT[match_type]
    if similarity >= 92:
        return 0.75
    if similarity >= 70:
        return 0.40
    return 0.28


def soften_similar_scores(items: list[dict[str, Any]] | None) -> None:
    """Cap stored similar-match percentages so they are not shown as near-copies."""
    for item in items or []:
        if not isinstance(item, dict):
            continue
        match_type = item.get("match_type")
        cap = SIMILARITY_CAP.get(match_type)
        if cap is None or match_type == MATCH_EXACT:
            continue
        if "similarity" in item:
            item["similarity"] = min(float(item.get("similarity") or 0), cap)
        if "similarity_score" in item:
            item["similarity_score"] = min(float(item.get("similarity_score") or 0), cap)


def _weighted_words(text: str, weighted_spans: list[tuple[int, int, float]]) -> float:
    """Sum of the strongest match-weight covering each content word."""
    if not text or not weighted_spans:
        return 0.0
    total = 0.0
    for start, end in content_token_spans(text):
        mid = (start + end) / 2
        strongest = 0.0
        for span_start, span_end, weight in weighted_spans:
            if span_start <= mid < span_end and weight > strongest:
                strongest = weight
        total += strongest
    return total


def _line_char_spans(thesis_text: str) -> dict[int, tuple[int, int]]:
    """Map 1-based splitlines() indices (same as aligner) to char offsets."""
    spans: dict[int, tuple[int, int]] = {}
    pos = 0
    for idx, raw in enumerate(thesis_text.splitlines(keepends=True), start=1):
        content = raw.rstrip("\r\n")
        end = pos + len(content)
        if content.strip():
            spans[idx] = (pos, end)
        pos += len(raw)
    return spans


def _find_text_span(haystack: str, needle: str) -> tuple[int, int] | None:
    needle = (needle or "").strip()
    if len(needle) < 20:
        return None
    # Try progressively shorter prefixes for paraphrased excerpts
    for size in (len(needle), min(len(needle), 180), min(len(needle), 100), 60):
        probe = needle[:size].strip()
        if len(probe) < 20:
            continue
        idx = haystack.lower().find(probe.lower())
        if idx >= 0:
            return idx, idx + len(probe)
    return None


def compute_unique_coverage(
    thesis_text: str,
    highlights: list[dict[str, Any]] | None = None,
    matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Weighted unique content-word coverage across overlapping fragments.

    Exact copies count fully. Similar / reordered / translated text is
    discounted so thematic overlap is not scored like verbatim plagiarism.

    Lidhëzat and other function words (dhe, ose, e, i, the, and, …) are excluded
    from both numerator and denominator so the score reflects content overlap.
    Channel scores (internal/web) use the same method independently; their sum
    may exceed overall when the same text is found in both channels.
    AI is intentionally excluded from these percentages.
    """
    text = thesis_text or ""
    total_words = len(content_tokens(text))
    total_words_raw = len(_word_tokens(text, content_only=False))
    line_spans = _line_char_spans(text)

    internal_weighted: list[tuple[int, int, float]] = []
    web_weighted: list[tuple[int, int, float]] = []
    # Keep the strongest weight per line/channel (exact beats similar)
    best_line: dict[tuple[int, str], tuple[tuple[int, int], float]] = {}

    for h in highlights or []:
        if not h.get("match_type"):
            continue
        ln = h.get("line_number")
        if ln is None or ln not in line_spans:
            continue
        channel = "web" if h.get("source_type") == "web" else "internal"
        key = (int(ln), channel)
        weight = coverage_weight(
            h.get("match_type"),
            float(h.get("similarity") or 0),
        )
        span = line_spans[int(ln)]
        prev = best_line.get(key)
        if prev is None or weight > prev[1]:
            best_line[key] = (span, weight)

    for (ln, channel), (span, weight) in best_line.items():
        if channel == "web":
            web_weighted.append((span[0], span[1], weight))
        else:
            internal_weighted.append((span[0], span[1], weight))

    # Add match excerpts not already captured as highlighted lines
    from detection.content_words import content_tokens as _ct

    for m in matches or []:
        excerpt = (
            m.get("thesis_phrase")
            or m.get("thesis_excerpt")
            or ""
        )
        source_ex = (
            m.get("matched_text")
            or m.get("original_text")
            or m.get("source_excerpt")
            or ""
        )
        # Skip weak / mis-attributed excerpts (doc-level noise without real overlap)
        if len(excerpt.strip()) < 40 or len(source_ex.strip()) < 20:
            continue
        src_set = set(_ct(source_ex))
        ex_tokens = _ct(excerpt)
        if not ex_tokens or not src_set:
            continue
        overlap = len(set(ex_tokens) & src_set) / max(1, min(len(set(ex_tokens)), len(src_set)))
        if overlap < 0.55:
            continue
        sim = float(m.get("similarity") or m.get("similarity_score") or 0)
        if sim < 22 and m.get("source_type") != "web":
            continue
        span = _find_text_span(text, excerpt)
        if not span:
            continue
        weight = coverage_weight(m.get("match_type"), sim)
        if m.get("source_type") == "web":
            web_weighted.append((span[0], span[1], weight))
        else:
            internal_weighted.append((span[0], span[1], weight))

    internal_plain = [(a, b) for a, b, _ in internal_weighted]
    web_plain = [(a, b) for a, b, _ in web_weighted]
    internal_merged = _merge_intervals(internal_plain)
    web_merged = _merge_intervals(web_plain)
    overall_merged = _merge_intervals(internal_merged + web_merged)

    internal_words = _weighted_words(text, internal_weighted)
    web_words = _weighted_words(text, web_weighted)
    overall_words = _weighted_words(text, internal_weighted + web_weighted)
    unweighted_words = _words_in_spans(text, overall_merged)

    def _pct(n: float) -> float:
        if total_words <= 0:
            return 0.0
        return round(min(100.0, (n / total_words) * 100.0), 2)

    return {
        "scoring_method": "weighted_content_word_coverage",
        "total_words": total_words,
        "total_words_raw": total_words_raw,
        "excluded_function_words": max(0, total_words_raw - total_words),
        "unique_plagiarized_words": int(round(overall_words)),
        "unweighted_plagiarized_words": unweighted_words,
        "weighted_plagiarized_words": round(overall_words, 2),
        "internal_unique_words": int(round(internal_words)),
        "web_unique_words": int(round(web_words)),
        "overall_pct": _pct(overall_words),
        "internal_pct": _pct(internal_words),
        "web_pct": _pct(web_words),
        "fragment_spans": len(overall_merged),
        "internal_spans": len(internal_merged),
        "web_spans": len(web_merged),
    }


def compute_match_statistics(highlights: list[dict[str, Any]], thesis_text: str) -> dict[str, Any]:
    """Turnitin-style aggregate stats using unique-word coverage."""
    index = build_location_index(thesis_text)
    flagged = [h for h in highlights if h.get("match_type")]
    # Primary hits only for line/paragraph counts
    primary = [h for h in flagged if not h.get("is_secondary")]

    para_set = {h["paragraph_number"] for h in primary if h.get("paragraph_number")}
    sent_set = {h["sentence_number"] for h in primary if h.get("sentence_number")}
    line_set = {h["line_number"] for h in primary if h.get("line_number") is not None}
    coverage = compute_unique_coverage(thesis_text, highlights)
    matched_words = coverage["unique_plagiarized_words"]
    total_words = coverage["total_words"] or index["total_words"]

    by_type: dict[str, int] = {}
    for h in primary:
        mt = h.get("match_type") or "unknown"
        by_type[mt] = by_type.get(mt, 0) + 1

    source_keys = set()
    for h in flagged:
        if h.get("source_type") == "web":
            source_keys.add(f"web:{h.get('source_url') or h.get('source_title')}")
        else:
            source_keys.add(f"internal:{h.get('thesis_id') or h.get('source_title')}")

    return {
        "matched_lines": len(line_set),
        "matched_paragraphs": len(para_set),
        "matched_sentences": len(sent_set),
        "matched_words": matched_words,
        "total_words": total_words,
        "total_lines": index["total_lines"],
        "total_paragraphs": index["total_paragraphs"],
        "matched_word_pct": coverage["overall_pct"],
        "matched_line_pct": round(len(line_set) / index["total_lines"] * 100, 1) if index["total_lines"] else 0,
        "by_match_type": by_type,
        "distinct_sources": len(source_keys),
        "coverage": coverage,
    }


def build_source_summaries(
    highlights: list[dict[str, Any]],
    flagged_sections: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Per-source document summary with similarity and fragment counts."""
    sources: dict[str, dict[str, Any]] = {}

    def _key(h: dict) -> str:
        if h.get("source_type") == "web":
            return f"web:{h.get('source_url') or h.get('source_title')}"
        tid = h.get("thesis_id") or h.get("conf_source_id")
        return f"internal:{tid or h.get('source_title')}"

    for h in highlights:
        if not h.get("match_type"):
            continue
        k = _key(h)
        if k not in sources:
            sources[k] = {
                "source_type": h.get("source_type", "internal"),
                "source_title": h.get("source_title") or "Burim",
                "source_url": h.get("source_url"),
                "thesis_id": h.get("thesis_id"),
                "conf_source_id": h.get("conf_source_id"),
                "similarity": 0.0,
                "line_count": 0,
                "paragraph_count": 0,
                "matched_words": 0,
                "fragments": [],
            }
        s = sources[k]
        s["line_count"] += 1
        s["matched_words"] += h.get("matched_word_count", 0)
        s["similarity"] = max(s["similarity"], float(h.get("similarity") or 0))
        if h.get("paragraph_number"):
            s.setdefault("_paras", set()).add(h["paragraph_number"])
        s["fragments"].append({
            "line_number": h.get("line_number"),
            "paragraph_number": h.get("paragraph_number"),
            "sentence_number": h.get("sentence_number"),
            "location_label": h.get("location_label"),
            "match_type": h.get("match_type"),
            "similarity": h.get("similarity"),
            "word_count": h.get("word_count"),
            "matched_word_count": h.get("matched_word_count"),
            "text_preview": (h.get("text") or "")[:180],
            "cross_lingual": h.get("cross_lingual", False),
        })

    for s in sources.values():
        paras = s.pop("_paras", set())
        s["paragraph_count"] = len(paras)

    if flagged_sections:
        from detection.content_words import content_tokens as _ct

        for sec in flagged_sections:
            sim = float(sec.get("similarity") or sec.get("similarity_score") or 0)
            # Only add source cards that have real fragment overlap or strong doc score
            thesis_ex = (sec.get("thesis_excerpt") or "").strip()
            source_ex = (
                sec.get("matched_text")
                or sec.get("original_text")
                or sec.get("source_excerpt")
                or ""
            ).strip()
            t_set, s_set = set(_ct(thesis_ex)), set(_ct(source_ex))
            has_frag = (
                len(t_set) >= 4
                and len(s_set) >= 4
                and (len(t_set & s_set) / max(1, min(len(t_set), len(s_set)))) >= 0.45
            )
            if not has_frag and sim < 25:
                continue
            if sim < 3 and sec.get("source_type") != "web":
                continue
            k = _key(sec)
            if k in sources:
                sources[k]["similarity"] = max(sources[k]["similarity"], sim)
                sources[k]["document_similarity"] = max(
                    float(sources[k].get("document_similarity") or 0), sim
                )
            elif has_frag or sim >= 25:
                sources[k] = {
                    "source_type": sec.get("source_type", "internal"),
                    "source_title": sec.get("source_title") or "Burim",
                    "source_url": sec.get("source_url"),
                    "thesis_id": sec.get("thesis_id"),
                    "conf_source_id": sec.get("conf_source_id"),
                    "similarity": sim,
                    "document_similarity": sim,
                    "line_count": 0,
                    "paragraph_count": 0,
                    "matched_words": 0,
                    "fragments": [],
                }

    return sorted(sources.values(), key=lambda x: (x["line_count"], x["similarity"]), reverse=True)


def build_match_fragments(highlights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flat list of all matched fragments with full location metadata."""
    return [
        {
            "line_number": h.get("line_number"),
            "paragraph_number": h.get("paragraph_number"),
            "sentence_number": h.get("sentence_number"),
            "location_label": h.get("location_label"),
            "match_type": h.get("match_type"),
            "similarity": h.get("similarity"),
            "word_count": h.get("word_count"),
            "matched_word_count": h.get("matched_word_count"),
            "matched_word_pct": h.get("matched_word_pct"),
            "source_title": h.get("source_title"),
            "source_url": h.get("source_url"),
            "thesis_id": h.get("thesis_id"),
            "source_type": h.get("source_type"),
            "text": h.get("text"),
            "source_text": h.get("source_text"),
            "cross_lingual": h.get("cross_lingual", False),
        }
        for h in highlights
        if h.get("match_type")
    ]
