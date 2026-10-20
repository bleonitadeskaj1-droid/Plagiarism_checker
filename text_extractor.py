"""
Minimal text extraction for thesis upload (DOCX / PDF / TXT).

DOCX uses only the Python standard library so upload never depends on python-docx.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

WORDML_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _ensure_pypdf2() -> None:
    try:
        import PyPDF2  # noqa: F401
    except ImportError:
        logger.warning("PyPDF2 missing; installing...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "PyPDF2>=3.0", "-q"],
            check=False,
        )


def extract_docx(path: Path) -> str:
    """Read DOCX via zip+xml (no python-docx required)."""
    if not path.exists():
        raise FileNotFoundError(f"Skedari nuk ekziston: {path}")

    with zipfile.ZipFile(path) as zf:
        if "word/document.xml" not in zf.namelist():
            raise ValueError("Skedari DOCX nuk është valid (mungon document.xml)")
        root = ET.fromstring(zf.read("word/document.xml"))

    paragraphs: list[str] = []
    for para in root.iter(f"{WORDML_NS}p"):
        chunks = [node.text for node in para.iter(f"{WORDML_NS}t") if node.text]
        if chunks:
            paragraphs.append("".join(chunks))

    text = "\n\n".join(paragraphs).strip()
    if not text:
        raise ValueError("DOCX nuk përmban tekst të lexueshëm")
    return text


def extract_txt(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Skedari nuk ekziston: {path}")
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        raise ValueError("TXT nuk përmban tekst të lexueshëm")
    return text


def extract_pdf(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Skedari nuk ekziston: {path}")

    _ensure_pypdf2()
    from PyPDF2 import PdfReader

    parts: list[str] = []
    with open(path, "rb") as handle:
        for page in PdfReader(handle).pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                parts.append(page_text.strip())

    if not parts:
        raise ValueError("PDF nuk përmban tekst të lexueshëm")
    return "\n\n".join(parts)


def extract_text_from_file(file_path: str, file_ext: str) -> str:
    """Extract plain text for upload pipeline."""
    path = Path(file_path)
    ext = file_ext.lower().lstrip(".")

    logger.info("text_extractor: path=%s ext=%s", path.name, ext)

    if ext == "docx":
        text = extract_docx(path)
        logger.info("text_extractor: DOCX OK chars=%s", len(text))
        return text
    if ext == "txt":
        text = extract_txt(path)
        logger.info("text_extractor: TXT OK chars=%s", len(text))
        return text
    if ext == "pdf":
        text = extract_pdf(path)
        logger.info("text_extractor: PDF OK chars=%s", len(text))
        return text

    raise ValueError(f"Format i pambështetur: .{ext}")
