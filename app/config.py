"""Application settings — single source of truth for configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Load settings from environment variables and `.env`."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["development", "production", "test"] = "development"
    secret_key: str = Field(default="change-me-in-production", min_length=16)
    database_url: str = "mysql+pymysql://root:@localhost:3306/plagiarism_checker?charset=utf8mb4"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    allowed_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    upload_dir: Path = Path("data/uploads")
    faiss_index_dir: Path = Path("data/faiss")
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    llm_model: str = "google/flan-t5-base"
    log_level: str = "INFO"

    # Cloud storage (local | s3)
    storage_backend: str = "local"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"
    s3_prefix: str = "plagiarism/"

    # FAISS prefilter for large corpora
    faiss_prefilter_threshold: int = 20
    faiss_top_k: int = 40

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        allowed_prefixes = ("sqlite:", "postgresql", "mysql+pymysql://", "mysql://")
        if not value.startswith(allowed_prefixes):
            raise ValueError(
                "DATABASE_URL must start with sqlite:, postgresql, or mysql. "
                f"Got: {value[:40]}..."
            )
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def is_development(self) -> bool:
        return self.env == "development"

    def ensure_directories(self) -> None:
        """Create runtime directories if missing."""
        for path in (self.upload_dir, self.faiss_index_dir, PROJECT_ROOT / "data", PROJECT_ROOT / "logs"):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    if settings.is_development and settings.db_echo is False:
        settings = settings.model_copy(update={"db_echo": False})
    return settings


settings = get_settings()
