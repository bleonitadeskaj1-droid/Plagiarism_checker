"""Extract text from PDF files."""

from __future__ import annotations

from pathlib import Path


def extract_pdf(path: str | Path) -> str:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")

    try:
        import pdfplumber

        parts: list[str] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text.strip():
                    parts.append(text)
        if parts:
            return "\n\n".join(parts)
    except ImportError:
        pass

    try:
        from PyPDF2 import PdfReader

        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text)
        return "\n\n".join(parts)
    except ImportError as exc:
        raise RuntimeError("Install pdfplumber or PyPDF2 for PDF extraction") from exc
