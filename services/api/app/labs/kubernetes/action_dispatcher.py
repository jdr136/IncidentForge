"""
Kubernetes ActionDispatcher — executes typed fault/remediation actions.

Each action is registered by name and receives (ref, context, parameters, api_client).
Used by KubernetesAdapter.inject() and KubernetesAdapter.apply_remediation().
"""
from __future__ import annotations

import logging

from kubernetes import client
from kubernetes.client import ApiClient

from contracts.types import CheckResult, RunContext

logger = logging.getLogger(__name__)

_ACTION_REGISTRY: dict[str, object] = {}


def _register(name: str):
    def decorator(fn):
        _ACTION_REGISTRY[name] = fn
        return fn
    return decorator


class KubernetesActionDispatcher:
    """Dispatches typed fault injection and remediation actions."""

    def __init__(self, api_client: ApiClient) -> None:
        self._api_client = api_client

    async def dispatch(
        self, action: str, ref: str, context: RunContext, parameters: dict
    ) -> CheckResult:
        fn = _ACTION_REGISTRY.get(action)
        if fn is None:
            return CheckResult.fail("UNKNOWN_ACTION", f"Unknown action: {action}")
        try:
            return await fn(ref, context, parameters, self._api_client)
        except Exception as exc:
            logger.exception("Action %s failed: %s", action, exc)
            return CheckResult.fail("ACTION_ERROR", f"Action {action} raised: {exc}")


@_register("k8s.patch_readiness_path")
@_register("k8s.set_readiness_path")
async def _patch_readiness_path(
    ref: str, context: RunContext, parameters: dict, api_client: ApiClient
) -> CheckResult:
    path = parameters.get("path")
    if not path:
        return CheckResult.fail("MISSING_PARAMETER", "parameters.path is required")
    apps_v1 = client.AppsV1Api(api_client)
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [{
                        "name": ref,
                        "readinessProbe": {"httpGet": {"path": path}},
                    }]
                }
            }
        }
    }
    apps_v1.patch_namespaced_deployment(name=ref, namespace=context.namespace, body=patch)
    return CheckResult.ok(
        "READINESS_PATH_PATCHED",
        f"Deployment '{ref}' readiness probe path set to '{path}'",
    )


@_register("k8s.patch_service_selector")
@_register("k8s.set_service_selector")
async def _patch_service_selector(
    ref: str, context: RunContext, parameters: dict, api_client: ApiClient
) -> CheckResult:
    selector = parameters.get("selector")
    if not selector:
        return CheckResult.fail("MISSING_PARAMETER", "parameters.selector is required")
    core_v1 = client.CoreV1Api(api_client)
    patch = {"spec": {"selector": selector}}
    core_v1.patch_namespaced_service(name=ref, namespace=context.namespace, body=patch)
    return CheckResult.ok(
        "SERVICE_SELECTOR_PATCHED",
        f"Service '{ref}' selector updated",
    )
