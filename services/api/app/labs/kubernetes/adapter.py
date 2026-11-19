"""
KubernetesAdapter — trusted Kubernetes scenario controller.

Implements the ScenarioAdapter Protocol. One instance handles ALL Kubernetes
scenarios by reading the declarative YAML and dispatching to CheckRunner and
ActionDispatcher.

This adapter holds the controller client (write-access). It must NEVER
pass this client to the ToolGateway or Investigator.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import yaml
from kubernetes import client
from kubernetes import utils as k8s_utils
from kubernetes.client import ApiClient

from app.labs.kubernetes.action_dispatcher import KubernetesActionDispatcher
from app.labs.kubernetes.check_runner import KubernetesCheckRunner
from contracts.types import ApprovedRemediation, CheckResult, CheckSpec, RunContext

logger = logging.getLogger(__name__)

# Label used to identify and clean up scenario-owned resources
OWNER_LABEL = "app.kubernetes.io/part-of=incidentforge"


class KubernetesAdapter:
    """
    Implements ScenarioAdapter for Kubernetes environments.
    Loaded once per application; shared across runs.
    """

    def __init__(self, controller_client: ApiClient, scenarios_root: Path) -> None:
        self._controller = controller_client
        self._scenarios_root = scenarios_root
        self._check_runner = KubernetesCheckRunner(controller_client)
        self._action_dispatcher = KubernetesActionDispatcher(controller_client)

    async def prepare(self, context: RunContext) -> CheckResult:
        """
        1. Clean up any leftover namespace from a previous run (lazy cleanup).
        2. Create the scenario namespace.
        3. Apply the scenario fixture.
        4. Wait for and verify the healthy baseline.
        """
        core_v1 = client.CoreV1Api(self._controller)

        # Lazy cleanup: delete any existing namespace for this scenario (not this run)
        await self._cleanup_stale_namespaces(core_v1, context)

        # Create namespace
        ns = client.V1Namespace(
            metadata=client.V1ObjectMeta(
                name=context.namespace,
                labels={
                    "app.kubernetes.io/part-of": "incidentforge",
                    "incidentforge.io/scenario": context.scenario_id,
                    "incidentforge.io/run": context.run_id,
                },
            )
        )
        try:
            core_v1.create_namespace(ns)
        except client.ApiException as exc:
            if exc.status != 409:
                raise

        # Apply fixture
        fixture_path = (
            self._scenarios_root
            / context.scenario_id
            / f"v{context.scenario_version}"
            / "fixtures"
            / "api-healthy.yaml"
        )
        result = await self._apply_fixture(fixture_path, context.namespace)
        if not result.success:
            return result

        # Verify baseline with timeout
        return await self._run_checks_with_timeout(
            [
                CheckSpec(type="k8s.deployment_available", ref="api"),
                CheckSpec(type="k8s.service_has_ready_endpoint", ref="api"),
            ],
            context,
            timeout=90,
        )

    async def inject(self, context: RunContext) -> CheckResult:
        """Apply the fault action from the scenario YAML."""
        private = self._load_scenario_private(context)
        return await self._action_dispatcher.dispatch(
            private["fault"]["action"],
            private["fault"]["ref"],
            context,
            private["fault"].get("parameters", {}),
        )

    async def confirm_fault(self, context: RunContext) -> CheckResult:
        """Verify the injected fault produced the expected observable symptom."""
        private = self._load_scenario_private(context)
        checks = [CheckSpec(**c) for c in private["fault"]["manifestation_checks"]]
        return await self._run_checks_with_timeout(checks, context, timeout=60)

    async def apply_remediation(
        self, context: RunContext, action: ApprovedRemediation
    ) -> CheckResult:
        """Apply an approved typed remediation action."""
        private = self._load_scenario_private(context)
        allowed = private["private_expected"]["allowed_remediations"]
        if action.action_type not in allowed:
            return CheckResult.fail(
                "POLICY_DENIED",
                f"Action '{action.action_type}' not in allowed_remediations: {allowed}",
            )
        parameters = dict(action.parameters)
        # For restore actions, populate content from the fixture (trusted controller only).
        if action.action_type == "k8s.restore_configmap_from_fixture":
            fixture_content = self._load_configmap_data_from_fixture(
                context, action.target_ref, parameters.get("key", "")
            )
            if fixture_content is not None:
                parameters["content"] = fixture_content
        return await self._action_dispatcher.dispatch(
            action.action_type, action.target_ref, context, parameters
        )

    async def verify(self, context: RunContext) -> CheckResult:
        """Run all post_fix_checks from private_expected."""
        private = self._load_scenario_private(context)
        checks = [CheckSpec(**c) for c in private["private_expected"]["post_fix_checks"]]
        return await self._run_checks_with_timeout(checks, context, timeout=90)

    async def cleanup(self, context: RunContext) -> None:
        """Delete the scenario namespace. Idempotent."""
        core_v1 = client.CoreV1Api(self._controller)
        try:
            core_v1.delete_namespace(
                name=context.namespace,
                body=client.V1DeleteOptions(propagation_policy="Foreground"),
            )
            logger.info("Deleted namespace %s", context.namespace)
        except client.ApiException as exc:
            if exc.status != 404:
                logger.warning("Could not delete namespace %s: %s", context.namespace, exc)

    # ── Private helpers ──────────────────────────────────────────────────────

    def _load_scenario_private(self, context: RunContext) -> dict:
        """Load raw scenario YAML (private fields needed for adapter logic)."""
        path = (
            self._scenarios_root
            / context.scenario_id
            / f"v{context.scenario_version}"
            / "scenario.yaml"
        )
        return yaml.safe_load(path.read_text())

    def _load_configmap_data_from_fixture(
        self, context: RunContext, configmap_name: str, key: str
    ) -> str | None:
        """
        Load the original value for a ConfigMap key from the scenario fixture.
        Used by k8s.restore_configmap_from_fixture so the trusted controller
        can restore content without the AI ever seeing the correct value.
        """
        private = self._load_scenario_private(context)
        fixture_path = (
            self._scenarios_root
            / context.scenario_id
            / f"v{context.scenario_version}"
            / private["setup"]["fixture"]
        )
        if not fixture_path.exists():
            logger.warning("Fixture not found for restore: %s", fixture_path)
            return None
        for doc in yaml.safe_load_all(fixture_path.read_text()):
            if not doc:
                continue
            if doc.get("kind") == "ConfigMap" and doc.get("metadata", {}).get("name") == configmap_name:
                return doc.get("data", {}).get(key)
        logger.warning("ConfigMap '%s' key '%s' not found in fixture", configmap_name, key)
        return None

    async def _apply_fixture(self, fixture_path: Path, namespace: str) -> CheckResult:
        if not fixture_path.exists():
            return CheckResult.fail("FIXTURE_NOT_FOUND", f"Fixture not found: {fixture_path}")
        try:
            k8s_utils.create_from_yaml(
                self._controller,
                str(fixture_path),
                namespace=namespace,
            )
            return CheckResult.ok("FIXTURE_APPLIED", f"Applied fixture: {fixture_path.name}")
        except Exception as exc:
            return CheckResult.fail("FIXTURE_ERROR", f"Failed to apply fixture: {exc}")

    async def _run_checks_with_timeout(
        self,
        checks: list[CheckSpec],
        context: RunContext,
        timeout: int,
    ) -> CheckResult:
        """Poll checks until all pass or timeout."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        last_result: CheckResult = CheckResult.fail(
            "TIMEOUT", f"Checks did not pass within {timeout}s"
        )
        while loop.time() < deadline:
            all_passed = True
            for check in checks:
                result = await self._check_runner.run(check, context)
                if not result.success:
                    all_passed = False
                    last_result = result
                    break
            if all_passed:
                return CheckResult.ok("ALL_CHECKS_PASSED", "All checks passed")
            await asyncio.sleep(5)
        return last_result

    async def _cleanup_stale_namespaces(self, core_v1, context: RunContext) -> None:
        """Delete any existing incidentforge namespaces for this scenario (lazy cleanup)."""
        try:
            namespaces = core_v1.list_namespace(
                label_selector=f"incidentforge.io/scenario={context.scenario_id}"
            )
            for ns in namespaces.items:
                name = ns.metadata.name
                if name != context.namespace:
                    logger.info("Lazy cleanup: deleting stale namespace %s", name)
                    try:
                        core_v1.delete_namespace(name)
                    except client.ApiException as exc:
                        if exc.status != 404:
                            logger.warning(
                                "Could not delete stale namespace %s: %s", name, exc
                            )
        except Exception as exc:
            logger.warning("Stale namespace cleanup failed: %s", exc)
