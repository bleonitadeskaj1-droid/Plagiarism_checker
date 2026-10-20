"""Line-level text alignment for plagiarism highlighting."""

from __future__ import annotations

import os
import re
from typing import Any

MATCH_EXACT = "exact_copy"
MATCH_REORDERED = "reordered"
MATCH_PARTIAL = "partial"
MATCH_TRANSLATED = "translated"

_engine_cache = None


def _get_engine():
    global _engine_cache
    if _engine_cache is None:
        try:
            from detection.engine import get_engine
            _engine_cache = get_engine()
        except Exception:
            _engine_cache = False
    return _engine_cache if _engine_cache else None

_HEADER_RE = re.compile(
    r"^(\d+\.)+\s*[\w\u00c0-\u024f]{0,40}$|^[IVXLC]+\.\s|^\d+\.?\s*$|^#{1,6}\s",
    re.UNICODE,
)


def _normalize_line(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _word_tokens(text: str) -> list[str]:
    """Content tokens for sentence overlap — lidhëzat (dhe, ose, e, i, …) excluded."""
    from detection.content_words import content_tokens

    return content_tokens(text)


def _content_normalized(text: str) -> str:
    from detection.content_words import content_text

    return content_text(text)


def _shared_word_ratio(a: str, b: str) -> float:
    wa, wb = set(_word_tokens(a)), set(_word_tokens(b))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def _is_header_only(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < 15:
        return True
    if _HEADER_RE.match(stripped) and len(stripped) < 80:
        return True
    # Short section titles / labels (few content words)
    if len(stripped) < 55 and len(_word_tokens(stripped)) < 4:
        return True
    if stripped.endswith(":") and len(_word_tokens(stripped)) <= 4:
        return True
    return False


def _min_content_ok(text: str, minimum: int = 4) -> bool:
    return len(_word_tokens(text)) >= minimum


def _validate_highlight_against_source(line_text: str, source_text: str) -> dict[str, Any] | None:
    """Accept a highlight only if the line actually matches the attributed source."""
    if not line_text or not source_text:
        return None
    if not _min_content_ok(line_text) or _is_header_only(line_text):
        return None
    scores = _score_line_pair(line_text[:500], source_text[:800])
    mt = _classify_match(
        scores["ratio"],
        scores["sort_ratio"],
        scores["word_overlap"] / 100.0,
        scores.get("full_ratio", 0),
        scores.get("full_sort_ratio", 0),
    )
    if not mt:
        return None
    return {"match_type": mt, "similarity": _similarity_for_type(mt, scores), "scores": scores}


def _split_lines(text: str, min_len: int = 25) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for idx, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if len(line) >= min_len and not _is_header_only(line):
            lines.append({"line_number": idx, "text": line, "normalized": _normalize_line(line)})
    return lines


def _split_paragraphs(text: str, min_len: int = 60) -> list[dict[str, Any]]:
    paragraphs: list[dict[str, Any]] = []
    all_lines = text.splitlines()
    for block in re.split(r"\n{2,}", text):
        block = block.strip()
        if len(block) < min_len:
            continue
        start_line = 1
        for i, ln in enumerate(all_lines, start=1):
            if block[:40] in ln or ln.strip()[:40] in block[:80]:
                start_line = i
                break
        paragraphs.append({
            "text": block,
            "normalized": _normalize_line(block),
            "start_line": start_line,
        })
    return paragraphs


def _split_sentences(text: str, min_len: int = 30) -> list[dict[str, Any]]:
    """Split into sentences mapped to the first containing line number."""
    all_lines = text.splitlines()
    sentences: list[dict[str, Any]] = []
    for sent in re.split(r"(?<=[.!?…])\s+", text or ""):
        sent = sent.strip()
        if len(sent) < min_len or _is_header_only(sent):
            continue
        # Prefer content-rich sentences (at least 3 content tokens)
        if len(_word_tokens(sent)) < 3:
            continue
        start_line = 1
        probe = sent[:50]
        for i, ln in enumerate(all_lines, start=1):
            if probe in ln or ln.strip()[:40] in sent[:80]:
                start_line = i
                break
        sentences.append({
            "text": sent,
            "normalized": _normalize_line(sent),
            "line_number": start_line,
        })
    return sentences


def align_sentences_against_document(
    thesis_text: str,
    source_text: str,
    source_meta: dict[str, Any],
    *,
    max_src: int = 60,
    max_tgt: int = 80,
) -> list[dict[str, Any]]:
    """Compare thesis sentences to source sentences using content words only."""
    src_sents = _split_sentences(thesis_text)[:max_src]
    tgt_sents = _split_sentences(source_text)[:max_tgt]
    if not src_sents or not tgt_sents:
        return []

    # Precompute content token sets for cheap overlap gate
    tgt_sets = [(t, set(_word_tokens(t["text"]))) for t in tgt_sents]
    highlights: list[dict[str, Any]] = []
    for src in src_sents:
        src_set = set(_word_tokens(src["text"]))
        if len(src_set) < 3:
            continue
        best: dict[str, Any] | None = None
        # Rank targets by shared content words, score only top few
        candidates = []
        for tgt, tset in tgt_sets:
            if not tset:
                continue
            shared = len(src_set & tset)
            if shared < 3:
                continue
            candidates.append((shared / min(len(src_set), len(tset)), tgt))
        candidates.sort(key=lambda x: x[0], reverse=True)
        for _, tgt in candidates[:8]:
            scores = _score_line_pair(src["normalized"][:500], tgt["normalized"][:500])
            mt = _classify_match(
                scores["ratio"],
                scores["sort_ratio"],
                scores["word_overlap"] / 100.0,
                scores.get("full_ratio", 0),
                scores.get("full_sort_ratio", 0),
            )
            if not mt:
                continue
            sim = _similarity_for_type(mt, scores)
            if best is None or sim > float(best.get("similarity") or 0):
                best = _make_highlight(
                    src["line_number"],
                    src["text"],
                    mt,
                    sim,
                    source_meta,
                    source_text=tgt["text"],
                )
        if best:
            highlights.append(best)
    return highlights


def _classify_match(
    ratio: float,
    sort_ratio: float,
    word_overlap: float,
    full_ratio: float = 0.0,
    full_sort_ratio: float = 0.0,
) -> str | None:
    """Classify a sentence/line pair.

    exact_copy (100%): full sentence matches period-to-period (all words, including lidhëza).
    reordered / partial: content-word similarity only — reported as similar, not 100%.
    """
    # Word-for-word copy: full normalized sentence nearly identical
    if full_ratio >= 96.0 or (full_ratio >= 93.0 and full_sort_ratio >= 98.0):
        return MATCH_EXACT
    if word_overlap < 0.55:
        return None
    # Similar / paraphrase — never promote to exact without full-sentence identity
    if sort_ratio >= 86 and ratio >= 72 and word_overlap >= 0.62:
        return MATCH_REORDERED
    if ratio >= 68 and word_overlap >= 0.58:
        return MATCH_PARTIAL
    # Strong content-word overlap with moderate string ratio → still "e ngjashme"
    if word_overlap >= 0.70 and (ratio >= 58 or sort_ratio >= 64):
        return MATCH_PARTIAL
    if full_sort_ratio >= 84 and word_overlap >= 0.60 and ratio >= 62:
        return MATCH_PARTIAL
    return None


def _similarity_for_type(match_type: str, scores: dict[str, float]) -> float:
    """exact_copy is 100%; similar text is discounted so it is not scored like a copy."""
    if match_type == MATCH_EXACT:
        return 100.0
    full = float(scores.get("full_ratio") or 0)
    ratio = float(scores.get("ratio") or 0)
    sort_r = float(scores.get("sort_ratio") or 0)
    wo = float(scores.get("word_overlap") or 0)
    if match_type == MATCH_REORDERED:
        raw = max(ratio, sort_r * 0.90, wo * 0.80)
        return min(82.0, round(raw * 0.82, 2))
    if match_type == MATCH_TRANSLATED:
        raw = max(ratio, wo * 0.70, full * 0.55)
        return min(55.0, round(raw * 0.50, 2))
    raw = max(ratio * 0.70, wo * 0.58, full * 0.55)
    return min(65.0, round(raw, 2))


def _classify_semantic(semantic: float, cross: float, word_overlap: float) -> str | None:
    """Cross-lingual / paraphrase when lexical overlap is low but semantics are high."""
    if word_overlap >= 0.45:
        return None
    combined = max(semantic, cross * 0.95)
    if combined >= 78:
        return MATCH_TRANSLATED
    if combined >= 70 and cross >= 65:
        return MATCH_PARTIAL
    if combined >= 66:
        return MATCH_PARTIAL
    return None


def _score_line_pair(src: str, tgt: str) -> dict[str, float]:
    """Score sentence/line pair.

    Content-word scores drive similarity; full-sentence scores gate exact_copy.
    """
    src_full = _normalize_line(src)
    tgt_full = _normalize_line(tgt)
    src_c = _content_normalized(src) or src_full
    tgt_c = _content_normalized(tgt) or tgt_full
    try:
        from rapidfuzz import fuzz

        return {
            "ratio": float(fuzz.ratio(src_c, tgt_c)),
            "sort_ratio": float(fuzz.token_sort_ratio(src_c, tgt_c)),
            "word_overlap": _shared_word_ratio(src, tgt) * 100,
            "full_ratio": float(fuzz.ratio(src_full, tgt_full)),
            "full_sort_ratio": float(fuzz.token_sort_ratio(src_full, tgt_full)),
        }
    except ImportError:
        from difflib import SequenceMatcher

        r = SequenceMatcher(None, src_c, tgt_c).ratio() * 100
        fr = SequenceMatcher(None, src_full, tgt_full).ratio() * 100
        return {
            "ratio": r,
            "sort_ratio": r,
            "word_overlap": _shared_word_ratio(src, tgt) * 100,
            "full_ratio": fr,
            "full_sort_ratio": fr,
        }


def _make_highlight(
    line_number: int,
    text: str,
    match_type: str,
    similarity: float,
    source_meta: dict[str, Any],
    source_line_number: int | None = None,
    source_text: str = "",
    cross_lingual: bool = False,
) -> dict[str, Any]:
    return {
        "line_number": line_number,
        "text": text,
        "match_type": match_type,
        "similarity": round(similarity, 2),
        "source_type": source_meta.get("source_type", "internal"),
        "source_title": source_meta.get("source_title", "Burim"),
        "source_url": source_meta.get("source_url"),
        "source_line_number": source_line_number,
        "source_text": source_text[:500] if source_text else "",
        "thesis_id": source_meta.get("thesis_id"),
        "conf_source_id": source_meta.get("conf_source_id"),
        "cross_lingual": cross_lingual,
    }


def align_lines_against_document(
    thesis_text: str,
    target_text: str,
    source_meta: dict[str, Any],
) -> list[dict[str, Any]]:
    """Find thesis lines copied from another document."""
    src_lines = _split_lines(thesis_text)[:100]
    tgt_lines = _split_lines(target_text)[:220]
    if not src_lines or not tgt_lines:
        return []

    highlights: list[dict[str, Any]] = []
    for src in src_lines:
        best: dict[str, Any] | None = None
        for tgt in tgt_lines:
            scores = _score_line_pair(src["normalized"][:400], tgt["normalized"][:400])
            word_overlap = scores["word_overlap"] / 100.0
            match_type = _classify_match(
                scores["ratio"],
                scores["sort_ratio"],
                word_overlap,
                scores.get("full_ratio", 0),
                scores.get("full_sort_ratio", 0),
            )
            if not match_type:
                continue
            sim = _similarity_for_type(match_type, scores)
            if best is None or sim > best["similarity"]:
                best = _make_highlight(
                    src["line_number"], src["text"], match_type, sim, source_meta,
                    tgt["line_number"], tgt["text"],
                )
        if best:
            highlights.append(best)

    # Semantic pass only when ML models are enabled
    if os.getenv("DISABLE_ML_MODELS", "").lower() in {"1", "true", "yes"}:
        return highlights

    engine = _get_engine()
    if engine:
        matched_lines = {h["line_number"] for h in highlights}
        for src in src_lines[:40]:
            if src["line_number"] in matched_lines:
                continue
            best_sem: dict[str, Any] | None = None
            for tgt in tgt_lines[:60]:
                wo = _shared_word_ratio(src["text"], tgt["text"])
                if wo >= 0.50:
                    continue
                metrics = engine.score_pair(src["text"][:500], tgt["text"][:500], rerank=False)
                sem = float(metrics.get("semantic_score") or 0)
                cross = float(metrics.get("cross_encoder_score") or 0)
                mt = _classify_semantic(sem, cross, wo)
                if not mt:
                    continue
                sim = max(sem, cross * 0.92, float(metrics.get("final_score") or 0))
                if best_sem is None or sim > best_sem["similarity"]:
                    best_sem = _make_highlight(
                        src["line_number"], src["text"], mt, sim, source_meta,
                        tgt["line_number"], tgt["text"], cross_lingual=True,
                    )
            if best_sem:
                highlights.append(best_sem)

    return highlights


def _align_paragraphs_against_document(
    thesis_text: str,
    target_text: str,
    source_meta: dict[str, Any],
) -> list[dict[str, Any]]:
    """Match copied paragraphs — catches multi-line copy even when doc score is low."""
    src_paras = _split_paragraphs(thesis_text)[:35]
    tgt_paras = _split_paragraphs(target_text)[:40]
    if not src_paras or not tgt_paras:
        return []

    all_lines = thesis_text.splitlines()
    highlights: list[dict[str, Any]] = []

    for sp in src_paras:
        best_score = 0.0
        best_tgt = None
        for tp in tgt_paras:
            scores = _score_line_pair(sp["normalized"][:2500], tp["normalized"][:2500])
            wo = scores["word_overlap"] / 100.0
            mt = _classify_match(
                scores["ratio"],
                scores["sort_ratio"],
                wo,
                scores.get("full_ratio", 0),
                scores.get("full_sort_ratio", 0),
            )
            if not mt and scores["ratio"] >= 60 and wo >= 0.48:
                mt = MATCH_PARTIAL
            if not mt:
                continue
            sim = _similarity_for_type(mt, scores)
            if sim > best_score:
                best_score = sim
                best_tgt = tp

        if not best_tgt or best_score < 60:
            continue

        seen: set[int] = set()
        for i, line in enumerate(all_lines, start=1):
            if i in seen or len(line.strip()) < 20:
                continue
            line_s = line.strip()
            if line_s not in sp["text"]:
                continue
            validated = _validate_highlight_against_source(line_s, best_tgt["text"])
            if not validated:
                continue
            seen.add(i)
            highlights.append(_make_highlight(
                i, line_s, validated["match_type"], validated["similarity"], source_meta,
                source_line_number=best_tgt["start_line"],
                source_text=best_tgt["text"][:400],
            ))

    return highlights


def _excerpt_chunks(text: str) -> list[str]:
    """Split a matched excerpt into line/sentence chunks for line-level alignment."""
    chunks: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[\n.!?;]+", text or ""):
        chunk = raw.strip()
        if len(chunk) < 20:
            continue
        norm = _normalize_line(chunk)
        if norm in seen:
            continue
        seen.add(norm)
        chunks.append(chunk)
    if not chunks and len((text or "").strip()) >= 20:
        chunks.append(text.strip())
    return chunks


def _align_from_excerpts(
    thesis_text: str,
    matches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Highlight lines that overlap with engine-detected excerpt pairs.

    thesis_excerpt is used ONLY to locate candidate lines in the thesis.
    Classification is always against the source excerpt — never against the
    thesis excerpt itself (that caused 100% self-matches attributed to wrong sources).
    """
    highlights: list[dict[str, Any]] = []
    all_lines = thesis_text.splitlines()

    for match in matches:
        thesis_ex = (match.get("thesis_excerpt") or "").strip()
        source_ex = (
            match.get("matched_text")
            or match.get("original_text")
            or match.get("source_excerpt")
            or ""
        ).strip()
        if len(source_ex) < 20:
            continue

        meta = {
            "source_type": match.get("source_type", "internal"),
            "source_title": match.get("source_title") or "Burim",
            "source_url": match.get("source_url"),
            "thesis_id": match.get("thesis_id"),
        }

        # Locate candidate lines via thesis excerpt (if present), else scan all lines vs source
        locator_chunks = _excerpt_chunks(thesis_ex) if len(thesis_ex) >= 20 else []
        candidate_lines: list[tuple[int, str]] = []
        if locator_chunks:
            for chunk in locator_chunks:
                chunk_norm = _normalize_line(chunk)
                for i, line in enumerate(all_lines, start=1):
                    line_s = line.strip()
                    if len(line_s) < 20 or not _min_content_ok(line_s):
                        continue
                    line_norm = _normalize_line(line_s)
                    if (
                        chunk_norm in line_norm
                        or line_norm in chunk_norm
                        or (len(line_s) >= 30 and line_s[:40] in chunk)
                        or _shared_word_ratio(line_s, chunk) >= 0.55
                    ):
                        candidate_lines.append((i, line_s))
        else:
            for i, line in enumerate(all_lines, start=1):
                line_s = line.strip()
                if len(line_s) >= 25 and _min_content_ok(line_s):
                    candidate_lines.append((i, line_s))

        # Deduplicate line numbers
        seen_ln: set[int] = set()
        source_chunks = _excerpt_chunks(source_ex) or [source_ex]
        for i, line_s in candidate_lines:
            if i in seen_ln or _is_header_only(line_s):
                continue
            best: dict[str, Any] | None = None
            best_src = source_ex
            for src_chunk in source_chunks[:12]:
                validated = _validate_highlight_against_source(line_s, src_chunk)
                if not validated:
                    continue
                if best is None or validated["similarity"] > best["similarity"]:
                    best = validated
                    best_src = src_chunk
            if not best:
                validated = _validate_highlight_against_source(line_s, source_ex)
                if not validated:
                    continue
                best = validated
                best_src = source_ex
            seen_ln.add(i)
            highlights.append(_make_highlight(
                i, line_s, best["match_type"], best["similarity"], meta,
                source_text=best_src[:500],
            ))

    return highlights


def _align_paragraphs_against_web(thesis_text: str, web_matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    highlights: list[dict[str, Any]] = []
    all_lines = thesis_text.splitlines()

    for wm in web_matches:
        snippet = (wm.get("web_snippet_full") or wm.get("matched_text") or "").strip()
        thesis_ref = (wm.get("thesis_phrase") or wm.get("thesis_excerpt") or "").strip()
        if len(snippet) < 30:
            continue
        meta = {
            "source_type": "web",
            "source_title": wm.get("source_title") or wm.get("source_url", "Web"),
            "source_url": wm.get("source_url") or wm.get("source"),
        }
        snippet_norm = _normalize_line(snippet)
        thesis_norm = _normalize_line(thesis_ref) if thesis_ref else ""

        for i, line in enumerate(all_lines, start=1):
            line_s = line.strip()
            if len(line_s) < 20:
                continue
            line_norm = _normalize_line(line_s)
            if thesis_norm and len(thesis_norm) >= 25:
                if thesis_norm[:45] not in line_norm and _score_line_pair(line_norm, thesis_norm)["ratio"] < 55:
                    continue
            scores = _score_line_pair(line_norm, snippet_norm)
            wo = scores["word_overlap"] / 100.0
            mt = _classify_match(
                scores["ratio"],
                scores["sort_ratio"],
                wo,
                scores.get("full_ratio", 0),
                scores.get("full_sort_ratio", 0),
            )
            if not mt:
                continue
            if not _min_content_ok(line_s) or _is_header_only(line_s):
                continue
            sim = _similarity_for_type(mt, scores)
            highlights.append(_make_highlight(i, line_s, mt, sim, meta, source_text=snippet))

    return highlights


def _highlight_source_key(h: dict[str, Any]) -> str:
    if h.get("source_type") == "web":
        return f"web:{h.get('source_url') or h.get('source_title')}"
    tid = h.get("thesis_id") or h.get("conf_source_id")
    return f"internal:{tid or h.get('source_title')}"


def build_thesis_highlights(
    thesis_text: str,
    internal_matches: list[dict[str, Any]],
    web_matches: list[dict[str, Any]],
    documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build line highlights from excerpts, paragraphs, and line alignment.

    Keeps the best match per line for display, but also retains strong secondary
    matches from other sources so multi-source summaries are attributed correctly.
    """
    doc_by_id = {d.get("id"): d for d in documents if d.get("id") is not None}
    doc_by_title = {d.get("title", ""): d for d in documents}
    line_best: dict[int, dict[str, Any]] = {}
    line_by_source: dict[tuple[int, str], dict[str, Any]] = {}

    def _merge(items: list[dict[str, Any]]) -> None:
        for hl in items:
            if not hl.get("match_type"):
                continue
            ln = hl["line_number"]
            sk = _highlight_source_key(hl)
            prev_src = line_by_source.get((ln, sk))
            if prev_src is None or float(hl.get("similarity") or 0) > float(prev_src.get("similarity") or 0):
                line_by_source[(ln, sk)] = hl

            prev = line_best.get(ln)
            if prev is None or float(hl.get("similarity") or 0) > float(prev.get("similarity") or 0):
                line_best[ln] = hl

    # 1) Excerpt pairs from detection engine (works even at 5% doc score)
    _merge(_align_from_excerpts(thesis_text, internal_matches + web_matches))

    # 2) Top matched documents — align more candidates for multi-source tests
    sorted_matches = sorted(
        internal_matches,
        key=lambda m: float(m.get("similarity") or m.get("similarity_score") or 0),
        reverse=True,
    )[:12]

    for match in sorted_matches:
        match_sim = float(match.get("similarity") or match.get("similarity_score") or 0)
        has_excerpt = len((match.get("thesis_excerpt") or "").strip()) >= 20
        # Allow weaker document-level scores when excerpts exist (summary-of-many case)
        if match_sim < 3 and not has_excerpt:
            continue

        thesis_id = match.get("thesis_id")
        doc = doc_by_id.get(thesis_id) if thesis_id else doc_by_title.get(match.get("source_title", ""))
        if not doc or not doc.get("content"):
            continue
        meta = {
            "source_type": match.get("source_type", "internal"),
            "source_title": match.get("source_title") or doc.get("title"),
            "source_url": match.get("source_url") or (f"internal://thesis/{thesis_id}" if thesis_id else None),
            "thesis_id": thesis_id,
        }
        _merge(_align_paragraphs_against_document(thesis_text, doc["content"], meta))
        if match_sim >= 3 or has_excerpt:
            _merge(align_lines_against_document(thesis_text, doc["content"], meta))
            _merge(align_sentences_against_document(thesis_text, doc["content"], meta))

    _merge(_align_paragraphs_against_web(thesis_text, web_matches))

    # Primary winners + competitive secondary sources on the same lines
    result: list[dict[str, Any]] = []
    for ln, best in line_best.items():
        result.append(best)
        best_sim = float(best.get("similarity") or 0)
        best_key = _highlight_source_key(best)
        for (line_no, sk), hl in list(line_by_source.items()):
            if line_no != ln or sk == best_key:
                continue
            sim = float(hl.get("similarity") or 0)
            # Keep secondary when strong on its own, or close to the winner
            if sim >= 32 or (best_sim > 0 and sim >= best_sim * 0.70 and sim >= 28):
                secondary = dict(hl)
                secondary["is_secondary"] = True
                result.append(secondary)

    # Sources with zero primary lines: promote their strongest unique hits
    primary_keys = {_highlight_source_key(h) for h in line_best.values()}
    claimed_lines = set(line_best.keys())
    for match in sorted_matches:
        thesis_id = match.get("thesis_id")
        sk = f"internal:{thesis_id or match.get('source_title')}"
        if sk in primary_keys:
            continue
        # Collect this source's line hits not already dominated by a much stronger source
        candidates = [
            hl for (ln, key), hl in line_by_source.items()
            if key == sk and float(hl.get("similarity") or 0) >= 30
        ]
        candidates.sort(key=lambda h: float(h.get("similarity") or 0), reverse=True)
        promoted = 0
        for hl in candidates:
            ln = hl["line_number"]
            best = line_best.get(ln)
            sim = float(hl.get("similarity") or 0)
            if best is None:
                line_best[ln] = hl
                claimed_lines.add(ln)
                result.append(hl)
                primary_keys.add(sk)
                promoted += 1
            elif sim >= 30 and (
                _highlight_source_key(best) != sk
                and (sim >= float(best.get("similarity") or 0) * 0.78 or ln not in claimed_lines)
            ):
                secondary = dict(hl)
                secondary["is_secondary"] = True
                result.append(secondary)
                promoted += 1
            if promoted >= 8:
                break

    result = sorted(
        result,
        key=lambda x: (x["line_number"], 0 if x.get("is_secondary") else -1, -float(x.get("similarity") or 0)),
    )
    # Final gate: drop highlights that do not actually match their attributed source
    filtered: list[dict[str, Any]] = []
    for h in result:
        if not h.get("match_type"):
            continue
        src = (h.get("source_text") or "").strip()
        line_t = (h.get("text") or "").strip()
        if not src or not line_t:
            continue
        validated = _validate_highlight_against_source(line_t, src)
        if not validated:
            continue
        h = dict(h)
        h["match_type"] = validated["match_type"]
        h["similarity"] = validated["similarity"]
        filtered.append(h)

    from detection.stats import enrich_highlights
    return enrich_highlights(thesis_text, filtered)


def render_highlighted_lines(highlights: list[dict[str, Any]], thesis_text: str) -> list[dict[str, Any]]:
    hl_map: dict[int, dict[str, Any]] = {}
    for h in highlights:
        ln = h.get("line_number")
        if ln is None or not h.get("match_type"):
            continue
        prev = hl_map.get(ln)
        # Prefer primary (non-secondary) highlights for the document view
        if prev is None:
            hl_map[ln] = h
        elif prev.get("is_secondary") and not h.get("is_secondary"):
            hl_map[ln] = h
        elif bool(prev.get("is_secondary")) == bool(h.get("is_secondary")):
            if float(h.get("similarity") or 0) > float(prev.get("similarity") or 0):
                hl_map[ln] = h

    result: list[dict[str, Any]] = []
    for idx, raw in enumerate(thesis_text.splitlines(), start=1):
        line = raw.rstrip()
        if not line.strip():
            continue
        if idx in hl_map:
            result.append(hl_map[idx])
        else:
            result.append({"line_number": idx, "text": line, "match_type": None, "similarity": 0.0})
    return result


def compute_line_plagiarism_score(highlights: list[dict[str, Any]], total_lines: int) -> float:
    if not highlights or total_lines <= 0:
        return 0.0
    # Deduplicate by line so secondary sources do not inflate the score
    best_by_line: dict[int, dict[str, Any]] = {}
    for h in highlights:
        if not h.get("match_type"):
            continue
        ln = h.get("line_number")
        if ln is None:
            continue
        prev = best_by_line.get(ln)
        if prev is None or float(h.get("similarity") or 0) > float(prev.get("similarity") or 0):
            best_by_line[ln] = h
    weights = {MATCH_EXACT: 1.0, MATCH_REORDERED: 0.85, MATCH_PARTIAL: 0.55, MATCH_TRANSLATED: 0.75}
    weighted = sum(weights.get(h.get("match_type"), 0.3) for h in best_by_line.values())
    return round(min(100.0, (weighted / total_lines) * 100), 2)
