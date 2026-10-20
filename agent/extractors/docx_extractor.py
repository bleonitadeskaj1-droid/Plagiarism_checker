"""Extract text from DOCX files."""

from __future__ import annotations

from pathlib import Path


def extract_docx(path: str | Path) -> str:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"DOCX not found: {path}")

    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError("Install python-docx for DOCX extraction") from exc

    doc = Document(str(path))
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    return "\n\n".join(paragraphs)
