"""
FastAPI application-level dependencies.
Provides shared singletons (registry, gateway, provider) to route handlers
and the orchestrator background task.

Lazy-loaded on first use and cached for the process lifetime.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from app.scenarios.registry import ScenarioRegistry

logger = logging.getLogger(__name__)

# Scenario root is relative to the project root (two levels up from services/api/)
_SCENARIOS_ROOT = Path(__file__).resolve().parents[4] / "scenarios"


@lru_cache(maxsize=1)
def get_scenario_registry() -> ScenarioRegistry:
    """Load and cache the scenario registry (called once at first request)."""
    registry = ScenarioRegistry.load(_SCENARIOS_ROOT)
    logger.info("Scenario registry loaded: %d scenario(s)", len(registry))
    return registry


@lru_cache(maxsize=1)
def get_provider_registry() -> dict:
    """
    Return all configured LLM provider adapters keyed by provider_id.
    A provider is included only when its API key is set and non-placeholder.
    Never exposes API keys.
    """
    registry: dict = {}

    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key and not openai_key.startswith("your-"):
        try:
            from app.investigator.providers.openai_adapter import OpenAIProviderAdapter
            model = os.environ.get("OPENAI_MODEL", "gpt-4o")
            registry["openai"] = OpenAIProviderAdapter(model=model, api_key=openai_key)
            logger.info("OpenAI provider registered (model=%s)", model)
        except Exception as exc:
            logger.warning("OpenAI provider unavailable: %s", exc)

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key and not anthropic_key.startswith("your-"):
        try:
            from app.investigator.providers.anthropic_adapter import AnthropicProviderAdapter
            model = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
            registry["anthropic"] = AnthropicProviderAdapter(model=model, api_key=anthropic_key)
            logger.info("Anthropic provider registered (model=%s)", model)
        except Exception as exc:
            logger.warning("Anthropic provider unavailable: %s", exc)

    if not registry:
        logger.warning(
            "No LLM providers configured. Set OPENAI_API_KEY or ANTHROPIC_API_KEY "
            "in .env to enable real AI investigations. Running in STUB mode."
        )
    return registry


def get_provider_for_run(provider_id: str):
    """
    Look up a specific provider adapter by ID.
    Returns None if provider is not configured (triggers stub mode).
    """
    return get_provider_registry().get(provider_id)


@lru_cache(maxsize=1)
def get_tool_gateway():
    """Return the KubernetesToolGateway singleton, or None if k8s unavailable."""
    try:
        from app.labs.kubernetes.clients import get_observer_client
        from app.investigator.gateway import KubernetesToolGateway
        client = get_observer_client()
        gw = KubernetesToolGateway(observer_client=client)
        logger.info("KubernetesToolGateway initialised")
        return gw
    except Exception as exc:
        logger.warning("KubernetesToolGateway unavailable: %s", exc)
        return None


@lru_cache(maxsize=1)
def get_kubernetes_adapter():
    """Return the KubernetesAdapter singleton (scenario controller), or None."""
    try:
        from app.labs.kubernetes.clients import get_controller_client
        from app.labs.kubernetes.adapter import KubernetesAdapter
        client = get_controller_client()
        adapter = KubernetesAdapter(
            controller_client=client,
            scenarios_root=_SCENARIOS_ROOT,
        )
        logger.info("KubernetesAdapter initialised")
        return adapter
    except Exception as exc:
        logger.warning("KubernetesAdapter unavailable: %s", exc)
        return None


# Keep backward-compatible aliases used by runs.py
def get_provider_adapter():
    """Deprecated: use get_provider_for_run(provider_id) instead."""
    registry = get_provider_registry()
    return next(iter(registry.values())) if registry else None
