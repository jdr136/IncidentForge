"""Health check endpoints."""
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version="0.1.0")


@router.get("/api/v1/labs/health")
async def labs_health() -> dict:
    """Explicit per-lab degraded/unsupported status. Stubs for now."""
    return {
        "kubernetes": {"status": "unknown", "message": "not yet initialised"},
        "aws_emulator": {"status": "unknown", "message": "not yet initialised"},
    }
