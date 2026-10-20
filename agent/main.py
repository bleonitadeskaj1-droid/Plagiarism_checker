"""Flask API for the plagiarism analysis agent."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Flask, jsonify, request

from agent.analyzer import PlagiarismAnalyzer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
analyzer = PlagiarismAnalyzer()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = os.getenv("AGENT_REPORTS_DIR", str(PROJECT_ROOT / "backend" / "reports"))


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "plagiarism-agent"})


@app.post("/agent/analyze")
def analyze():
    data = request.get_json(silent=True) or {}
    thesis_id = int(data.get("thesis_id") or 0)
    file_path = data.get("file_path") or ""
    compare_with = data.get("compare_with") or []
    search_web = bool(data.get("search_web", True))
    chunk_words = int(data.get("chunk_words") or 1000)
    cache_key = data.get("cache_key")

    if not thesis_id or not file_path:
        return jsonify({"error": "thesis_id dhe file_path janë të detyrueshme"}), 400

    path = Path(file_path)
    if not path.is_file():
        # Try relative to project root
        alt = PROJECT_ROOT / file_path
        if alt.is_file():
            path = alt
        else:
            return jsonify({"error": f"Skedari nuk u gjet: {file_path}"}), 404

    try:
        result = analyzer.analyze(
            thesis_id=thesis_id,
            file_path=str(path),
            compare_with=compare_with,
            search_web=search_web,
            chunk_words=chunk_words,
            reports_dir=REPORTS_DIR,
            cache_key=cache_key,
        )
        return jsonify(result)
    except Exception as exc:
        logger.exception("Analysis failed")
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.getenv("AGENT_PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
