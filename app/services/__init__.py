"""Business logic services."""

from app.services.plagiarism_service import PlagiarismService, get_plagiarism_service
from app.services.text_processing import (
    compute_content_hash,
    count_words,
    extract_text_from_file,
)

__all__ = [
    "PlagiarismService",
    "get_plagiarism_service",
    "compute_content_hash",
    "count_words",
    "extract_text_from_file",
]
