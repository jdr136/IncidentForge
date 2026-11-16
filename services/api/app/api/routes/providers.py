"""Provider registry endpoint."""
import os

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["providers"])


class ProviderInfo(BaseModel):
    provider_id: str
    display_name: str
    models: list[str]
    available: bool


@router.get("/providers", response_model=list[ProviderInfo])
async def list_providers() -> list[ProviderInfo]:
    """Return configured LLM providers. Never exposes API keys."""
    providers = []

    openai_key = os.environ.get("OPENAI_API_KEY", "")
    providers.append(ProviderInfo(
        provider_id="openai",
        display_name="OpenAI",
        models=[os.environ.get("OPENAI_MODEL", "gpt-4o")],
        available=bool(openai_key and not openai_key.startswith("your-")),
    ))

    return providers
