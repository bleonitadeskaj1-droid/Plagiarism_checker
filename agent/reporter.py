"""Generate final analysis report."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_report(
    thesis_id: int,
    score: float,
    details: list[dict[str, Any]],
    web_refs: list[dict[str, Any]],
    highlighted: list[dict[str, Any]],
    cache_key: str | None = None,
) -> dict[str, Any]:
    if score >= 50:
        recommendation = "plagiarism"
    elif score >= 25:
        recommendation = "suspicious"
    else:
        recommendation = "clean"

    return {
        "thesis_id": thesis_id,
        "score": round(score, 2),
        "internal_score": round(score, 2),
        "web_score": round(max((r.get("similarity", 0) for r in web_refs), default=0), 2),
        "recommendation": recommendation,
        "details": details,
        "web_refs": web_refs,
        "highlighted_paragraphs": highlighted,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_key": cache_key,
    }


def save_report(report: dict[str, Any], reports_dir: str | Path) -> str:
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    thesis_id = report.get("thesis_id", 0)
    path = reports_dir / f"{thesis_id}_{int(datetime.now().timestamp())}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.resolve())
