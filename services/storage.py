"""
Cloud-ready document storage abstraction.

Backends:
  - local  (default): filesystem under uploads/
  - s3     : S3-compatible (AWS S3, MinIO, DigitalOcean Spaces, …)
"""

from __future__ import annotations

import logging
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class StorageBackend(ABC):
    @abstractmethod
    def save(self, key: str, data: BinaryIO | bytes, *, content_type: str | None = None) -> str:
        """Store object; return storage URI / relative path used by the app."""

    @abstractmethod
    def open_local_path(self, key_or_uri: str) -> Path:
        """Resolve to a local filesystem path (download to cache if remote)."""

    @abstractmethod
    def delete(self, key_or_uri: str) -> bool:
        ...

    @abstractmethod
    def exists(self, key_or_uri: str) -> bool:
        ...


class LocalStorageBackend(StorageBackend):
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or PROJECT_ROOT / "uploads").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        key = key.replace("\\", "/").lstrip("/")
        if key.startswith("uploads/"):
            return (PROJECT_ROOT / key).resolve()
        return (self.root / key).resolve()

    def save(self, key: str, data: BinaryIO | bytes, *, content_type: str | None = None) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, (bytes, bytearray)):
            path.write_bytes(data)
        else:
            with open(path, "wb") as fh:
                shutil.copyfileobj(data, fh)
        try:
            rel = path.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            rel = path.as_posix()
        return rel

    def open_local_path(self, key_or_uri: str) -> Path:
        raw = key_or_uri or ""
        if raw.startswith("s3://"):
            raise FileNotFoundError("Local backend cannot open s3:// URI")
        p = Path(raw)
        if p.is_file():
            return p.resolve()
        return self._path(raw)

    def delete(self, key_or_uri: str) -> bool:
        try:
            path = self.open_local_path(key_or_uri)
            if path.is_file():
                path.unlink()
                return True
        except Exception as exc:
            logger.debug("Local delete failed: %s", exc)
        return False

    def exists(self, key_or_uri: str) -> bool:
        try:
            return self.open_local_path(key_or_uri).is_file()
        except Exception:
            return False


class S3StorageBackend(StorageBackend):
    """S3-compatible object storage. Requires boto3."""

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
        prefix: str = "plagiarism/",
        cache_dir: Path | None = None,
    ) -> None:
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "boto3 required for S3 storage. Install with: pip install boto3"
            ) from exc

        self.bucket = bucket
        self.prefix = prefix.lstrip("/")
        if self.prefix and not self.prefix.endswith("/"):
            self.prefix += "/"
        self.cache_dir = (cache_dir or PROJECT_ROOT / "data" / "s3_cache").resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            region_name=region,
        )

    def _object_key(self, key: str) -> str:
        key = key.replace("\\", "/").lstrip("/")
        if key.startswith("s3://"):
            # s3://bucket/key
            parts = key[5:].split("/", 1)
            return parts[1] if len(parts) > 1 else parts[0]
        if key.startswith(self.prefix):
            return key
        return f"{self.prefix}{key}"

    def save(self, key: str, data: BinaryIO | bytes, *, content_type: str | None = None) -> str:
        obj_key = self._object_key(key)
        extra = {}
        if content_type:
            extra["ContentType"] = content_type
        if isinstance(data, (bytes, bytearray)):
            self._client.put_object(Bucket=self.bucket, Key=obj_key, Body=bytes(data), **extra)
        else:
            self._client.upload_fileobj(data, self.bucket, obj_key, ExtraArgs=extra or None)
        return f"s3://{self.bucket}/{obj_key}"

    def open_local_path(self, key_or_uri: str) -> Path:
        obj_key = self._object_key(key_or_uri)
        local = self.cache_dir / quote(obj_key, safe="")
        local.parent.mkdir(parents=True, exist_ok=True)
        if not local.is_file():
            self._client.download_file(self.bucket, obj_key, str(local))
        return local

    def delete(self, key_or_uri: str) -> bool:
        try:
            obj_key = self._object_key(key_or_uri)
            self._client.delete_object(Bucket=self.bucket, Key=obj_key)
            return True
        except Exception as exc:
            logger.warning("S3 delete failed: %s", exc)
            return False

    def exists(self, key_or_uri: str) -> bool:
        try:
            obj_key = self._object_key(key_or_uri)
            self._client.head_object(Bucket=self.bucket, Key=obj_key)
            return True
        except Exception:
            return False


_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _storage
    if _storage is not None:
        return _storage

    backend = (os.getenv("STORAGE_BACKEND") or "local").strip().lower()
    if backend == "s3":
        bucket = (os.getenv("S3_BUCKET") or "").strip()
        if not bucket:
            logger.warning("STORAGE_BACKEND=s3 but S3_BUCKET missing — using local")
            _storage = LocalStorageBackend()
        else:
            _storage = S3StorageBackend(
                bucket,
                endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
                access_key=os.getenv("S3_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID"),
                secret_key=os.getenv("S3_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY"),
                region=os.getenv("S3_REGION") or "us-east-1",
                prefix=os.getenv("S3_PREFIX") or "plagiarism/",
            )
            logger.info("Using S3 storage backend bucket=%s", bucket)
    else:
        root = os.getenv("LOCAL_UPLOAD_ROOT")
        _storage = LocalStorageBackend(Path(root) if root else None)
    return _storage


def reset_storage_for_tests() -> None:
    global _storage
    _storage = None
