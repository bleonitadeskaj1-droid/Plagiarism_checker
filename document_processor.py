"""
Document Processing Module
Handles extraction, cleaning, and normalization of text from various document formats.

Supported Formats:
- PDF (PyMuPDF, PyPDF2)
- DOCX (python-docx)
- TXT
- PPTX (python-pptx)
- XLSX (openpyxl)

Features:
- Automatic format detection
- Text extraction and cleaning
- Language detection
- Metadata extraction
- Batch processing
- Error handling and validation
"""

import hashlib
import logging
import re
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

WORDML_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Optional packages used for richer extraction (DOCX also works via stdlib fallback).
_OPTIONAL_PACKAGES = (
    ("docx", "python-docx>=1.1.0"),
    ("docx2txt", "docx2txt>=0.8"),
    ("PyPDF2", "PyPDF2>=3.0"),
)


def _install_missing_packages() -> None:
    """Install missing extraction packages into the active Python interpreter."""
    missing = []
    for module_name, pip_name in _OPTIONAL_PACKAGES:
        try:
            __import__(module_name)
        except ImportError:
            missing.append(pip_name)

    if not missing:
        return

    logger.warning("Installing missing text-extraction packages: %s", ", ".join(missing))
    subprocess.run(
        [sys.executable, "-m", "pip", "install", *missing, "-q"],
        check=False,
    )


_install_missing_packages()

# Document processing libraries
try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

try:
    from PyPDF2 import PdfReader
    PYPDF2_AVAILABLE = True
except ImportError:
    PYPDF2_AVAILABLE = False

try:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    PYTHON_DOCX_AVAILABLE = True
except ImportError:
    PYTHON_DOCX_AVAILABLE = False
    Document = None
    Table = None
    Paragraph = None

try:
    import docx2txt
    DOCX2TXT_AVAILABLE = True
except ImportError:
    DOCX2TXT_AVAILABLE = False
    docx2txt = None

try:
    from pptx import Presentation
    PYTHON_PPTX_AVAILABLE = True
except ImportError:
    PYTHON_PPTX_AVAILABLE = False

try:
    from openpyxl import load_workbook
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False

try:
    from langdetect import detect, DetectorFactory
    LANGDETECT_AVAILABLE = True
    DetectorFactory.seed = 0
except ImportError:
    LANGDETECT_AVAILABLE = False


def log_extraction_dependencies() -> dict[str, bool]:
    """Log and return availability of text-extraction libraries."""
    status = {
        "PyPDF2": PYPDF2_AVAILABLE,
        "PyMuPDF": PYMUPDF_AVAILABLE,
        "python-docx": PYTHON_DOCX_AVAILABLE,
        "docx2txt": DOCX2TXT_AVAILABLE,
        "stdlib-docx": True,
    }
    for name, available in status.items():
        if available:
            logger.info("Text extraction dependency available: %s", name)
        else:
            logger.warning("Text extraction dependency missing: %s", name)
    return status


class DocumentFormat(str, Enum):
    """Supported document formats."""
    PDF = "pdf"
    DOCX = "docx"
    TXT = "txt"
    PPTX = "pptx"
    XLSX = "xlsx"
    UNKNOWN = "unknown"


class DocumentProcessor:
    """
    Process and extract text from various document formats.
    
    Handles:
    - Format detection
    - Text extraction
    - Metadata extraction
    - Text cleaning and normalization
    - Language detection
    - Batch processing
    """
    
    # Maximum text length per document
    MAX_TEXT_LENGTH = 1_000_000  # 1MB
    
    # Supported file extensions
    SUPPORTED_EXTENSIONS = {
        ".pdf": DocumentFormat.PDF,
        ".docx": DocumentFormat.DOCX,
        ".txt": DocumentFormat.TXT,
        ".pptx": DocumentFormat.PPTX,
        ".xlsx": DocumentFormat.XLSX,
    }
    
    def __init__(self):
        """Initialize document processor with available libraries."""
        self._check_dependencies()
    
    def _check_dependencies(self) -> None:
        """Log availability of document processing libraries."""
        log_extraction_dependencies()
    
    def detect_format(self, file_path: Path) -> DocumentFormat:
        """
        Detect document format by extension and content.
        
        Args:
            file_path: Path to the document
        
        Returns:
            Detected DocumentFormat
        """
        suffix = file_path.suffix.lower()
        
        if suffix in self.SUPPORTED_EXTENSIONS:
            return self.SUPPORTED_EXTENSIONS[suffix]
        
        return DocumentFormat.UNKNOWN
    
    def extract_text(self, file_path: Path) -> Tuple[str, Dict[str, Any]]:
        """
        Extract text from a document file.
        
        Args:
            file_path: Path to the document
        
        Returns:
            Tuple of (extracted_text, metadata)
        """
        file_path = Path(file_path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        doc_format = self.detect_format(file_path)
        
        metadata = {
            "file_path": str(file_path),
            "file_name": file_path.name,
            "file_size": file_path.stat().st_size,
            "format": doc_format.value,
            "extraction_method": None,
        }
        
        try:
            if doc_format == DocumentFormat.PDF:
                text, method = self._extract_pdf(file_path)
                metadata["extraction_method"] = method
            elif doc_format == DocumentFormat.DOCX:
                text, method = self._extract_docx(file_path)
                metadata["extraction_method"] = method
            elif doc_format == DocumentFormat.TXT:
                text = self._extract_txt(file_path)
                metadata["extraction_method"] = "plain-text"
            elif doc_format == DocumentFormat.PPTX:
                text = self._extract_pptx(file_path)
                metadata["extraction_method"] = "python-pptx"
            elif doc_format == DocumentFormat.XLSX:
                text = self._extract_xlsx(file_path)
                metadata["extraction_method"] = "openpyxl"
            else:
                raise ValueError(f"Unsupported format: {doc_format}")
            
            # Clean and normalize text
            text = self._clean_text(text)
            
            # Detect language
            if LANGDETECT_AVAILABLE:
                try:
                    metadata["language"] = detect(text[:500])  # Detect from first 500 chars
                except:
                    metadata["language"] = "unknown"
            
            # Calculate hash
            metadata["content_hash"] = hashlib.sha256(text.encode()).hexdigest()
            metadata["text_length"] = len(text)
            metadata["extraction_success"] = True
            
            logger.info(f"Extracted {len(text)} chars from {file_path.name}")
            
            return text, metadata
        
        except Exception as e:
            logger.error("Error extracting text from %s (%s): %s", file_path, doc_format.value, e)
            metadata["extraction_success"] = False
            metadata["error"] = str(e)
            return "", metadata
    
    def _extract_pdf(self, file_path: Path) -> Tuple[str, str]:
        """Extract text from PDF using PyMuPDF or PyPDF2."""
        if PYMUPDF_AVAILABLE:
            try:
                text_parts = []
                with fitz.open(file_path) as pdf:
                    for page in pdf:
                        page_text = page.get_text() or ""
                        if page_text.strip():
                            text_parts.append(page_text.strip())
                if text_parts:
                    return "\n\n".join(text_parts), "pymupdf"
            except Exception as exc:
                logger.warning("PyMuPDF extraction failed for %s: %s", file_path, exc)

        if not PYPDF2_AVAILABLE:
            raise ValueError("PyPDF2 is not installed. Run: pip install -r requirements.txt")

        try:
            text_parts = []
            with open(file_path, "rb") as f:
                reader = PdfReader(f)
                for page in reader.pages:
                    page_text = page.extract_text() or ""
                    if page_text.strip():
                        text_parts.append(page_text.strip())
            if not text_parts:
                raise ValueError("PDF contains no extractable text")
            return "\n\n".join(text_parts), "pypdf2"
        except Exception as exc:
            logger.error("PDF extraction failed for %s: %s", file_path, exc)
            raise ValueError(f"PDF extraction failed: {exc}") from exc

    def _extract_docx_stdlib(self, file_path: Path) -> str:
        """Extract DOCX text using only the Python standard library."""
        with zipfile.ZipFile(file_path) as zf:
            if "word/document.xml" not in zf.namelist():
                raise ValueError("Invalid DOCX file: word/document.xml not found")
            root = ET.fromstring(zf.read("word/document.xml"))

        paragraphs = []
        for para in root.iter(f"{WORDML_NS}p"):
            parts = [node.text for node in para.iter(f"{WORDML_NS}t") if node.text]
            if parts:
                paragraphs.append("".join(parts))

        text = "\n\n".join(paragraphs).strip()
        if not text:
            raise ValueError("DOCX contains no extractable text")
        return text

    def _extract_docx(self, file_path: Path) -> Tuple[str, str]:
        """Extract text from DOCX; stdlib first so upload never depends on pip packages."""
        try:
            text = self._extract_docx_stdlib(file_path)
            logger.info("DOCX extracted with stdlib zip/xml: %s", file_path.name)
            return text, "stdlib-zipxml"
        except Exception as stdlib_exc:
            logger.warning("stdlib DOCX extraction failed for %s: %s", file_path, stdlib_exc)

        if PYTHON_DOCX_AVAILABLE and Document is not None:
            try:
                doc = Document(file_path)
                text_parts = []

                for block in doc.element.body:
                    if block.tag.endswith("}p"):
                        paragraph = Paragraph(block, doc)
                        if paragraph.text.strip():
                            text_parts.append(paragraph.text.strip())
                    elif block.tag.endswith("}tbl"):
                        table = Table(block, doc)
                        for row in table.rows:
                            row_text = " ".join(
                                cell.text.strip() for cell in row.cells if cell.text.strip()
                            )
                            if row_text:
                                text_parts.append(row_text)

                if text_parts:
                    logger.info("DOCX extracted with python-docx: %s", file_path.name)
                    return "\n\n".join(text_parts), "python-docx"
            except Exception as exc:
                logger.warning("python-docx extraction failed for %s: %s", file_path, exc)

        if DOCX2TXT_AVAILABLE and docx2txt is not None:
            try:
                text = docx2txt.process(str(file_path)) or ""
                if text.strip():
                    logger.info("DOCX extracted with docx2txt: %s", file_path.name)
                    return text.strip(), "docx2txt"
            except Exception as exc:
                logger.warning("docx2txt extraction failed for %s: %s", file_path, exc)

        logger.error("All DOCX extraction methods failed for %s", file_path)
        raise ValueError("Nuk u ekstraktua teksti nga skedari DOCX")
    
    def _extract_txt(self, file_path: Path) -> str:
        """Extract text from TXT file."""
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    
    def _extract_pptx(self, file_path: Path) -> str:
        """Extract text from PPTX file."""
        if not PYTHON_PPTX_AVAILABLE:
            raise ValueError("python-pptx is not installed")
        
        prs = Presentation(file_path)
        text_parts = []
        
        for slide in prs.slides:
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    text_parts.append(shape.text)
        
        return "\n".join(text_parts)
    
    def _extract_xlsx(self, file_path: Path) -> str:
        """Extract text from XLSX file."""
        if not OPENPYXL_AVAILABLE:
            raise ValueError("openpyxl is not installed")
        
        wb = load_workbook(file_path)
        text_parts = []
        
        for sheet in wb.sheetnames:
            ws = wb[sheet]
            text_parts.append(f"\n=== Sheet: {sheet} ===\n")
            for row in ws.iter_rows(values_only=True):
                row_text = " | ".join(str(cell or "") for cell in row)
                if row_text.strip():
                    text_parts.append(row_text)
        
        return "\n".join(text_parts)
    
    def _clean_text(self, text: str) -> str:
        """
        Clean and normalize text while preserving paragraph structure.
        
        Args:
            text: Raw text to clean
        
        Returns:
            Cleaned text
        """
        if not text:
            return ""

        text = text.replace("\x00", "")
        text = text.replace("\x0c", "\n")
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        paragraphs = [re.sub(r"\s+", " ", p.strip()) for p in text.split("\n")]
        paragraphs = [p for p in paragraphs if p]
        text = "\n\n".join(paragraphs)

        if len(text) > self.MAX_TEXT_LENGTH:
            logger.warning(f"Text truncated from {len(text)} to {self.MAX_TEXT_LENGTH} chars")
            text = text[: self.MAX_TEXT_LENGTH]

        return text
    
    def extract_batch(self, file_paths: List[Path]) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Extract text from multiple documents.
        
        Args:
            file_paths: List of file paths
        
        Returns:
            List of (text, metadata) tuples
        """
        results = []
        for file_path in file_paths:
            try:
                text, metadata = self.extract_text(file_path)
                results.append((text, metadata))
            except Exception as e:
                logger.error(f"Batch extraction error for {file_path}: {e}")
                results.append(("", {"error": str(e), "file_path": str(file_path)}))
        
        return results
    
    def validate_file(self, file_path: Path, max_size_mb: int = 100) -> Tuple[bool, str]:
        """
        Validate document file.
        
        Args:
            file_path: Path to file
            max_size_mb: Maximum file size in MB
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        file_path = Path(file_path)
        
        if not file_path.exists():
            return False, "File does not exist"
        
        # Check file size
        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        if file_size_mb > max_size_mb:
            return False, f"File size ({file_size_mb:.1f}MB) exceeds limit ({max_size_mb}MB)"
        
        # Check format
        if self.detect_format(file_path) == DocumentFormat.UNKNOWN:
            return False, f"Unsupported file format: {file_path.suffix}"
        
        return True, "Valid"
    
    def get_statistics(self, texts: List[str]) -> Dict[str, Any]:
        """
        Get statistics about a collection of texts.
        
        Args:
            texts: List of text documents
        
        Returns:
            Statistics dictionary
        """
        total_chars = sum(len(text) for text in texts)
        total_words = sum(len(text.split()) for text in texts)
        avg_length = total_chars / len(texts) if texts else 0
        
        return {
            "total_documents": len(texts),
            "total_characters": total_chars,
            "total_words": total_words,
            "average_length": round(avg_length, 2),
            "min_length": min((len(text) for text in texts), default=0),
            "max_length": max((len(text) for text in texts), default=0),
        }
