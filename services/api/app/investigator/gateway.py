"""
Tool gateway — KubernetesToolGateway implements the ToolGateway Protocol.

Exceptions defined here are shared with the provider adapter and can be
imported without circular dependencies.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
from app.investigator.tools.kubernetes import (
    k8s_get_deployment,
    k8s_get_pod_logs,
    k8s_get_service,
    k8s_list_events,
    k8s_list_pods,
)
from app.investigator.tools.registry import KUBERNETES_TOOLS, TOOLS_BY_NAME
from app.persistence.evidence_repository import EvidenceRepository
from contracts.types import EvidenceItem, RunContext, ToolDefinition

logger = logging.getLogger(__name__)

# Payload size cap — truncate at 16 KiB to protect the model's context window
MAX_PAYLOAD_BYTES = 16 * 1024

# Maps tool names to their handler coroutines
_K8S_HANDLERS = {
    "k8s.list_pods": k8s_list_pods,
    "k8s.get_deployment": k8s_get_deployment,
    "k8s.get_service": k8s_get_service,
    "k8s.list_events": k8s_list_events,
    "k8s.get_pod_logs": k8s_get_pod_logs,
}

# For tools that target a named resource, this maps tool_name → argument key.
# The named resource is checked against context.allowed_resources.
_RESOURCE_ARG: dict[str, str] = {
    "k8s.get_deployment": "name",
    "k8s.get_service": "name",
    "k8s.get_pod_logs": "pod_name",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ToolPolicyDenied(Exception):
    """Raised when a tool call is not permitted by the run's policy."""


class ToolExecutionError(Exception):
    """Raised on infrastructure failure during tool execution (not env-level errors)."""


# ---------------------------------------------------------------------------
# Gateway implementation
# ---------------------------------------------------------------------------

class KubernetesToolGateway:
    """
    Implements the ToolGateway Protocol for Kubernetes environments.

    Responsibilities:
    1. Validate tool_name against the allowed tool list
    2. Enforce allowed_resources policy on named-resource arguments
    3. Execute the underlying k8s query via the tool handler
    4. Truncate the payload if it exceeds MAX_PAYLOAD_BYTES
    5. Assign evidence_id, compute sha256, persist via EvidenceRepository
    6. Return the EvidenceItem (including evidence_id) to the caller
    """

    def __init__(self, api_client=None) -> None:
        self._api_client = api_client
        self._evidence_repo = EvidenceRepository()

    async def call(
        self,
        context: RunContext,
        tool_name: str,
        arguments: dict,
    ) -> EvidenceItem:
        # 1. Validate tool exists
        if tool_name not in TOOLS_BY_NAME:
            raise ToolPolicyDenied(f"Unknown tool: {tool_name!r}")

        # 2. Enforce resource policy
        _enforce_resource_policy(context, tool_name, arguments)

        # 3. Execute the tool
        handler = _K8S_HANDLERS[tool_name]
        payload = await handler(context, arguments, self._api_client)

        # 4. Truncate if payload exceeds the size cap
        truncated = False
        raw_bytes = json.dumps(payload, sort_keys=True).encode()
        if len(raw_bytes) > MAX_PAYLOAD_BYTES:
            payload = {
                "_truncated": True,
                "_note": f"Payload exceeded {MAX_PAYLOAD_BYTES // 1024} KiB limit and was dropped.",
            }
            truncated = True

        # 5. Compute sha256, assign evidence_id, persist
        sha256_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        seq = await self._evidence_repo.next_seq(context.run_id)
        evidence_id = f"ev_{context.run_short_id}_{seq:04d}"

        item = EvidenceItem(
            evidence_id=evidence_id,
            run_id=context.run_id,
            tool_name=tool_name,
            captured_at=datetime.now(UTC),
            payload=payload,
            sha256=sha256_hash,
            truncated=truncated,
        )
        await self._evidence_repo.save(item)
        return item

    def available_tools(self, context: RunContext) -> list[ToolDefinition]:
        """Return all Kubernetes tools available for investigation."""
        return KUBERNETES_TOOLS


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _enforce_resource_policy(
    context: RunContext,
    tool_name: str,
    arguments: dict,
) -> None:
    """
    When allowed_resources is non-empty, tools targeting a named resource
    must have that resource in the allowed list.
    Empty allowed_resources = no restriction (permissive, for testing).
    """
    if not context.allowed_resources:
        return

    arg_key = _RESOURCE_ARG.get(tool_name)
    if arg_key is None:
        return  # tool has no targeted resource argument

    resource_name = arguments.get(arg_key, "")
    if resource_name and resource_name not in context.allowed_resources:
        raise ToolPolicyDenied(
            f"Resource {resource_name!r} is not in the allowed list for this run."
        )
