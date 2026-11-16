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
    """
    Load and cache the scenario registry.
    Called once at first request; cached for the process lifetime.
    """
    registry = ScenarioRegistry.load(_SCENARIOS_ROOT)
    logger.info("Scenario registry loaded: %d scenario(s)", len(registry))
    return registry


@lru_cache(maxsize=1)
def get_tool_gateway():
    """
    Return the KubernetesToolGateway singleton, or None if k8s is unavailable.

    Returns None when:
    - Kubeconfig files are not configured (no live cluster)
    - kubernetes package is not installed
    - Any client initialisation error
    """
    try:
        from app.investigator.gateway import KubernetesToolGateway
        from app.labs.kubernetes.clients import get_observer_client
        client = get_observer_client()
        gw = KubernetesToolGateway(observer_client=client)
        logger.info("KubernetesToolGateway initialised")
        return gw
    except Exception as exc:
        logger.warning("KubernetesToolGateway unavailable: %s", exc)
        return None


@lru_cache(maxsize=1)
def get_provider_adapter():
    """
    Return the OpenAIProviderAdapter singleton, or None if no API key configured.

    Returns None when OPENAI_API_KEY is unset or is the placeholder value.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key or api_key.startswith("your-"):
        logger.warning(
            "OPENAI_API_KEY not configured — investigator will run in STUB mode. "
            "Set OPENAI_API_KEY in .env to use the real AI investigator."
        )
        return None
    try:
        from app.investigator.providers.openai_adapter import OpenAIProviderAdapter
        model = os.environ.get("OPENAI_MODEL", "gpt-4o")
        adapter = OpenAIProviderAdapter(model=model, api_key=api_key)
        logger.info("OpenAIProviderAdapter initialised (model=%s)", model)
        return adapter
    except Exception as exc:
        logger.warning("OpenAIProviderAdapter unavailable: %s", exc)
        return None


def get_kubernetes_adapter():
    """
    Return the KubernetesAdapter singleton (scenario controller), or None.
    Uses the controller client (write-access). Never exposed to the tool gateway.
    """
    try:
        from app.labs.kubernetes.adapter import KubernetesAdapter
        from app.labs.kubernetes.clients import get_controller_client
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
