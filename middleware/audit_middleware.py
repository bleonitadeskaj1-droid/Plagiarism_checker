"""ASGI middleware — records successful mutating API calls by admin/professor."""

from __future__ import annotations

import logging
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_SKIP_PREFIXES = (
    "/api/admin/audit",
    "/health",
    "/docs",
    "/openapi",
    "/static",
)


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    if request.client:
        return request.client.host
    return None


def _decode_bearer(request: Request) -> dict | None:
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        import jwt
        from auth import SECRET_KEY, ALGORITHM

        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        return None


def _action_label(method: str, path: str) -> str:
    parts = [p for p in path.split("/") if p]
    # e.g. api theses 12 review → theses.review
    if len(parts) >= 2 and parts[0] == "api":
        resource = parts[1]
        tail = parts[-1] if len(parts) > 2 and not parts[-1].isdigit() else method.lower()
        return f"{resource}.{tail}"
    return f"{method.lower()}.{path}"


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        try:
            if request.method not in _MUTATING:
                return response
            if response.status_code >= 400:
                return response
            path = request.url.path or ""
            if not path.startswith("/api/"):
                return response
            if any(path.startswith(p) for p in _SKIP_PREFIXES):
                return response

            payload = _decode_bearer(request)
            if not payload:
                return response

            role = str(payload.get("role") or "").lower()
            if role not in {"admin", "professor"}:
                return response

            from database import SessionLocal
            from services.audit_service import record_audit

            db = SessionLocal()
            try:
                record_audit(
                    db,
                    user_id=int(payload["sub"]) if payload.get("sub") else None,
                    username=payload.get("username") or payload.get("email"),
                    role=role,
                    action=_action_label(request.method, path),
                    resource_type=path.strip("/").split("/")[1] if path.count("/") >= 2 else None,
                    resource_id=next(
                        (p for p in path.split("/") if p.isdigit()),
                        None,
                    ),
                    method=request.method,
                    path=path[:500],
                    status_code=response.status_code,
                    ip_address=_client_ip(request),
                    details={"query": str(request.url.query)[:300] or None},
                )
            finally:
                db.close()
        except Exception as exc:
            logger.debug("Audit middleware skipped: %s", exc)

        return response
