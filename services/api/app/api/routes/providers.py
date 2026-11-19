"""Provider registry endpoint."""
from __future__ import annotations

import os

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["providers"])


class ProviderInfo(BaseModel):
    provider_id: str
    display_name: str
    models: list[str]
    available: bool


_PROVIDER_CATALOGUE = [
    {
        "provider_id": "openai",
        "display_name": "OpenAI",
        "env_key": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "default_model": "gpt-4o",
        "extra_models": ["gpt-4o-mini", "o1", "o1-mini"],
    },
    {
        "provider_id": "anthropic",
        "display_name": "Anthropic",
        "env_key": "ANTHROPIC_API_KEY",
        "model_env": "ANTHROPIC_MODEL",
        "default_model": "claude-3-5-sonnet-20241022",
        "extra_models": ["claude-3-5-haiku-20241022", "claude-3-opus-20240229"],
    },
]


@router.get("/providers", response_model=list[ProviderInfo])
async def list_providers() -> list[ProviderInfo]:
    """
    Return all supported LLM providers and their availability.
    A provider is 'available' when its API key is set in the server environment.
    Never exposes API keys to the client.
    """
    result = []
    for p in _PROVIDER_CATALOGUE:
        key = os.environ.get(p["env_key"], "")
        available = bool(key and not key.startswith("your-"))
        configured_model = os.environ.get(p["model_env"], p["default_model"])
        models = [configured_model]
        for m in p["extra_models"]:
            if m != configured_model:
                models.append(m)
        result.append(ProviderInfo(
            provider_id=p["provider_id"],
            display_name=p["display_name"],
            models=models,
            available=available,
        ))
    return result
