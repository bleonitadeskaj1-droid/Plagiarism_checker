"""
services package for plagiarism analysis and legacy compatibility.
"""

import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("detector")


try:
    from django.conf import settings
except ImportError:
    settings = None


def _get_cache_dir() -> Path:
    if settings is not None:
        return Path(getattr(settings, "PLAGIARISM_CACHE_DIR", "/tmp/plagiarism_cache"))
    return Path(os.getenv("PLAGIARISM_CACHE_DIR", "/tmp/plagiarism_cache"))


def _get_sbert_model() -> str:
    if settings is not None:
        return getattr(settings, "SBERT_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
    return os.getenv("SBERT_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")


@lru_cache(maxsize=1)
def _get_agent():
    from plagiarism_agent import PlagiarismAgent

    cache_dir = _get_cache_dir()
    sbert_model = _get_sbert_model()

    logger.info("Po ngarkohet PlagiarismAgent (herën e parë)...")
    agent = PlagiarismAgent(sbert_model=sbert_model, cache_dir=cache_dir)
    logger.info("PlagiarismAgent u ngarkua me sukses.")
    return agent


class PlagiarismService:
    """Shërbimi Django për analizën e plagjiaturës.
    Merr të dhënat nga DB dhe i kalon te PlagiarismAgent.
    """

    def run_analysis(self, thesis, include_confidential: bool = True) -> dict:
        from .models import Thesis, ConfidentialDocument

        agent = _get_agent()

        public_theses = list(
            Thesis.objects.filter(is_public=True)
            .exclude(pk=thesis.pk)
            .values("id", "title", "content")
        )

        conf_docs = []
        if include_confidential:
            conf_docs = list(
                ConfidentialDocument.objects.all().values("id", "title", "content", "source_url")
            )

        logger.debug(
            f"Analizë: '{thesis.title}' | "
            f"{len(public_theses)} teza publike | "
            f"{len(conf_docs)} dok. konfidenciale"
        )

        result = agent.analyze_with_confidential(
            thesis_text=thesis.content,
            thesis_title=thesis.title,
            public_theses=public_theses,
            conf_docs=conf_docs,
        )

        if hasattr(result, "to_dict"):
            return result.to_dict()
        return result


__all__ = ["PlagiarismService"]
