"""Text extraction and analysis utilities."""

from __future__ import annotations

import hashlib
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

WORDML_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
SUPPORTED_EXTENSIONS = {"docx", "pdf", "txt"}


def compute_content_hash(text: str) -> str:
    """Return SHA-256 hex digest of normalized text."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def count_words(text: str) -> int:
    """Count words in text."""
    return len(re.findall(r"\b\w+\b", text))


def extract_docx(path: Path) -> str:
    """Read DOCX via zip+xml (stdlib only)."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with zipfile.ZipFile(path) as zf:
        if "word/document.xml" not in zf.namelist():
            raise ValueError("Invalid DOCX: missing document.xml")
        root = ET.fromstring(zf.read("word/document.xml"))

    paragraphs: list[str] = []
    for para in root.iter(f"{WORDML_NS}p"):
        chunks = [node.text for node in para.iter(f"{WORDML_NS}t") if node.text]
        if chunks:
            paragraphs.append("".join(chunks))

    text = "\n\n".join(paragraphs).strip()
    if not text:
        raise ValueError("DOCX contains no readable text")
    return text


def extract_txt(path: Path) -> str:
    """Read plain text file."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        raise ValueError("TXT contains no readable text")
    return text


def extract_pdf(path: Path) -> str:
    """Extract text from PDF using pypdf."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    from pypdf import PdfReader

    parts: list[str] = []
    with path.open("rb") as handle:
        for page in PdfReader(handle).pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                parts.append(page_text.strip())

    if not parts:
        raise ValueError("PDF contains no readable text")
    return "\n\n".join(parts)


def extract_text_from_file(file_path: str | Path, file_ext: str | None = None) -> str:
    """Extract plain text from supported file types."""
    path = Path(file_path)
    ext = (file_ext or path.suffix).lower().lstrip(".")

    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported format: .{ext}")

    logger.info("Extracting text: file=%s ext=%s", path.name, ext)

    if ext == "docx":
        return extract_docx(path)
    if ext == "txt":
        return extract_txt(path)
    return extract_pdf(path)


def detect_language_simple(text: str) -> str:
    """Rough language hint based on character patterns."""
    sample = text[:2000].lower()
    if re.search(r"[ëç]", sample):
        return "sq"
    if re.search(r"[àâçéèêëïîôùûü]", sample):
        return "fr"
    if re.search(r"[äöüß]", sample):
        return "de"
    return "en"


def extract_key_sentences(text: str, limit: int = 5) -> list[str]:
    """Return longest sentences as simple key-sentence heuristic."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 40]
    sentences.sort(key=len, reverse=True)
    return sentences[:limit]
