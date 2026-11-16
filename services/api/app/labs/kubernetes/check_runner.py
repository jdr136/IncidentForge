"""
Kubernetes CheckRunner — implements the CheckRunner protocol.

Maps check type strings from scenario YAML to Python check functions.
All check functions are async, bounded, and return CheckResult.
They never raise unhandled exceptions.
"""
from __future__ import annotations

import logging

from kubernetes import client
from kubernetes.client import ApiClient

from contracts.types import CheckResult, CheckSpec, RunContext

logger = logging.getLogger(__name__)

# Registry: check type string → async function
_CHECK_REGISTRY: dict[str, object] = {}


def _register(name: str):
    def decorator(fn):
        _CHECK_REGISTRY[name] = fn
        return fn
    return decorator


class KubernetesCheckRunner:
    """Implements the CheckRunner protocol for Kubernetes checks."""

    def __init__(self, api_client: ApiClient) -> None:
        self._api_client = api_client

    async def run(self, check: CheckSpec, context: RunContext) -> CheckResult:
        fn = _CHECK_REGISTRY.get(check.type)
        if fn is None:
            return CheckResult.fail(
                "UNKNOWN_CHECK_TYPE",
                f"Unknown check type: {check.type}",
            )
        try:
            return await fn(check, context, self._api_client)
        except Exception as exc:
            logger.exception("Check %s failed with exception: %s", check.type, exc)
            return CheckResult.fail("CHECK_ERROR", f"Check {check.type} raised: {exc}")


@_register("k8s.deployment_available")
async def _deployment_available(
    check: CheckSpec, context: RunContext, api_client: ApiClient
) -> CheckResult:
    apps_v1 = client.AppsV1Api(api_client)
    try:
        dep = apps_v1.read_namespaced_deployment(name=check.ref, namespace=context.namespace)
        available = dep.status.available_replicas or 0
        if available >= 1:
            return CheckResult.ok(
                "DEPLOYMENT_AVAILABLE",
                f"Deployment '{check.ref}' has {available} available replica(s)",
            )
        return CheckResult.fail(
            "DEPLOYMENT_NOT_AVAILABLE",
            f"Deployment '{check.ref}' has {available} available replicas",
        )
    except client.ApiException as exc:
        if exc.status == 404:
            return CheckResult.fail("DEPLOYMENT_NOT_FOUND", f"Deployment '{check.ref}' not found")
        raise


@_register("k8s.pod_not_ready")
async def _pod_not_ready(
    check: CheckSpec, context: RunContext, api_client: ApiClient
) -> CheckResult:
    core_v1 = client.CoreV1Api(api_client)
    pods = core_v1.list_namespaced_pod(
        namespace=context.namespace,
        label_selector=f"app={check.ref}",
    )
    if not pods.items:
        return CheckResult.fail("NO_PODS_FOUND", f"No pods found with label app={check.ref}")
    for pod in pods.items:
        for cond in pod.status.conditions or []:
            if cond.type == "Ready" and cond.status == "False":
                return CheckResult.ok(
                    "POD_NOT_READY",
                    f"Pod '{pod.metadata.name}' is not ready (Ready=False)",
                )
    return CheckResult.fail(
        "PODS_ARE_READY",
        f"All pods with label app={check.ref} are ready — fault not confirmed",
    )


@_register("k8s.service_has_ready_endpoint")
async def _service_has_ready_endpoint(
    check: CheckSpec, context: RunContext, api_client: ApiClient
) -> CheckResult:
    discovery_v1 = client.DiscoveryV1Api(api_client)
    slices = discovery_v1.list_namespaced_endpoint_slice(
        namespace=context.namespace,
        label_selector=f"kubernetes.io/service-name={check.ref}",
    )
    for es in slices.items:
        for ep in es.endpoints or []:
            if ep.conditions and ep.conditions.ready:
                return CheckResult.ok(
                    "SERVICE_HAS_READY_ENDPOINT",
                    f"Service '{check.ref}' has at least one ready endpoint",
                )
    return CheckResult.fail(
        "SERVICE_NO_READY_ENDPOINTS",
        f"Service '{check.ref}' has no ready endpoints",
    )


@_register("k8s.service_no_ready_endpoints")
async def _service_no_ready_endpoints(
    check: CheckSpec, context: RunContext, api_client: ApiClient
) -> CheckResult:
    # Inverse of _service_has_ready_endpoint
    result = await _service_has_ready_endpoint(check, context, api_client)
    if result.success:
        return CheckResult.fail(
            "SERVICE_HAS_READY_ENDPOINTS",
            f"Service '{check.ref}' still has ready endpoints — fault not confirmed",
        )
    return CheckResult.ok(
        "SERVICE_NO_READY_ENDPOINTS",
        f"Service '{check.ref}' has no ready endpoints — fault confirmed",
    )
