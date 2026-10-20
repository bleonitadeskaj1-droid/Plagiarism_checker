"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.v1 import agent, checks, documents

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(documents.router)
api_router.include_router(checks.router)
api_router.include_router(agent.router)
