"""
Kubernetes client factory.

Provides two clients:
  observer   — read-only ServiceAccount (get/list/watch on pods, deployments, services, events, pod/log)
  controller — trusted ServiceAccount (create/patch/delete of scenario-owned resources)

Credentials NEVER cross between clients. The observer client is used by the
ToolGateway; the controller client is used by the ScenarioAdapter only.
"""
from __future__ import annotations

import os

from kubernetes import client, config
from kubernetes.client import ApiClient


def _load_kubeconfig(env_var: str, context: str) -> ApiClient:
    path = os.environ.get(env_var)
    if path and os.path.exists(path):
        cfg = client.Configuration()
        config.load_kube_config(config_file=path, client_configuration=cfg)
        return ApiClient(configuration=cfg)
    # Fall back: try in-cluster, then default kubeconfig with specific context
    try:
        cfg = client.Configuration()
        config.load_incluster_config(client_configuration=cfg)
        return ApiClient(configuration=cfg)
    except config.ConfigException:
        pass
    cfg = client.Configuration()
    config.load_kube_config(context=context, client_configuration=cfg)
    return ApiClient(configuration=cfg)


def get_observer_client() -> ApiClient:
    return _load_kubeconfig("KUBECONFIG_OBSERVER", "incidentforge-observer")


def get_controller_client() -> ApiClient:
    return _load_kubeconfig("KUBECONFIG_CONTROLLER", "incidentforge-controller")
