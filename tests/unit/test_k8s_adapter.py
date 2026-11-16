"""Unit tests for Kubernetes adapter, check runner, and action dispatcher."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from app.labs.kubernetes.action_dispatcher import KubernetesActionDispatcher
from app.labs.kubernetes.check_runner import KubernetesCheckRunner

from contracts.types import CheckSpec, RunContext

SCENARIOS_ROOT = Path("scenarios")


def make_context(scenario_id: str = "k8s-readiness-path") -> RunContext:
    return RunContext.for_run(
        run_id="run_01TESTRUN",
        scenario_id=scenario_id,
        scenario_version=1,
        allowed_resources=["api"],
    )


# ── CheckRunner unit tests (mocked k8s client) ──────────────────────────────

class TestKubernetesCheckRunner:
    async def test_unknown_check_type_returns_fail(self):
        runner = KubernetesCheckRunner(MagicMock())
        check = CheckSpec(type="k8s.nonexistent_check", ref="api")
        result = await runner.run(check, make_context())
        assert not result.success
        assert result.code == "UNKNOWN_CHECK_TYPE"

    @patch("app.labs.kubernetes.check_runner.client.AppsV1Api")
    async def test_deployment_available_returns_ok(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        dep = MagicMock()
        dep.status.available_replicas = 1
        mock_api.read_namespaced_deployment.return_value = dep

        runner = KubernetesCheckRunner(MagicMock())
        check = CheckSpec(type="k8s.deployment_available", ref="api")
        result = await runner.run(check, make_context())
        assert result.success
        assert result.code == "DEPLOYMENT_AVAILABLE"

    @patch("app.labs.kubernetes.check_runner.client.AppsV1Api")
    async def test_deployment_not_available_returns_fail(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        dep = MagicMock()
        dep.status.available_replicas = 0
        mock_api.read_namespaced_deployment.return_value = dep

        runner = KubernetesCheckRunner(MagicMock())
        check = CheckSpec(type="k8s.deployment_available", ref="api")
        result = await runner.run(check, make_context())
        assert not result.success
        assert result.code == "DEPLOYMENT_NOT_AVAILABLE"

    @patch("app.labs.kubernetes.check_runner.client.CoreV1Api")
    async def test_pod_not_ready_returns_ok_when_pod_not_ready(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        pod = MagicMock()
        pod.metadata.name = "api-abc123"
        cond = MagicMock()
        cond.type = "Ready"
        cond.status = "False"
        pod.status.conditions = [cond]
        mock_api.list_namespaced_pod.return_value = MagicMock(items=[pod])

        runner = KubernetesCheckRunner(MagicMock())
        check = CheckSpec(type="k8s.pod_not_ready", ref="api")
        result = await runner.run(check, make_context())
        assert result.success
        assert result.code == "POD_NOT_READY"

    @patch("app.labs.kubernetes.check_runner.client.CoreV1Api")
    async def test_pod_not_ready_returns_fail_when_no_pods(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        mock_api.list_namespaced_pod.return_value = MagicMock(items=[])

        runner = KubernetesCheckRunner(MagicMock())
        check = CheckSpec(type="k8s.pod_not_ready", ref="api")
        result = await runner.run(check, make_context())
        assert not result.success
        assert result.code == "NO_PODS_FOUND"

    @patch("app.labs.kubernetes.check_runner.client.DiscoveryV1Api")
    async def test_service_no_ready_endpoints_returns_ok_when_no_endpoints(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        mock_api.list_namespaced_endpoint_slice.return_value = MagicMock(items=[])

        runner = KubernetesCheckRunner(MagicMock())
        check = CheckSpec(type="k8s.service_no_ready_endpoints", ref="api")
        result = await runner.run(check, make_context())
        assert result.success
        assert result.code == "SERVICE_NO_READY_ENDPOINTS"

    @patch("app.labs.kubernetes.check_runner.client.DiscoveryV1Api")
    async def test_service_has_ready_endpoint_returns_ok(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        ep = MagicMock()
        ep.conditions.ready = True
        es = MagicMock()
        es.endpoints = [ep]
        mock_api.list_namespaced_endpoint_slice.return_value = MagicMock(items=[es])

        runner = KubernetesCheckRunner(MagicMock())
        check = CheckSpec(type="k8s.service_has_ready_endpoint", ref="api")
        result = await runner.run(check, make_context())
        assert result.success
        assert result.code == "SERVICE_HAS_READY_ENDPOINT"


# ── ActionDispatcher unit tests ──────────────────────────────────────────────

class TestKubernetesActionDispatcher:
    async def test_unknown_action_returns_fail(self):
        dispatcher = KubernetesActionDispatcher(MagicMock())
        result = await dispatcher.dispatch("k8s.unknown_action", "api", make_context(), {})
        assert not result.success
        assert result.code == "UNKNOWN_ACTION"

    @patch("app.labs.kubernetes.action_dispatcher.client.AppsV1Api")
    async def test_patch_readiness_path_calls_patch(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        dispatcher = KubernetesActionDispatcher(MagicMock())
        result = await dispatcher.dispatch(
                "k8s.patch_readiness_path", "api", make_context(), {"path": "/health"}
        )
        assert result.success
        mock_api.patch_namespaced_deployment.assert_called_once()

    @patch("app.labs.kubernetes.action_dispatcher.client.AppsV1Api")
    async def test_set_readiness_path_is_alias(self, mock_api_cls):
        """k8s.set_readiness_path and k8s.patch_readiness_path are the same."""
        dispatcher = KubernetesActionDispatcher(MagicMock())
        result = await dispatcher.dispatch(
                "k8s.set_readiness_path", "api", make_context(), {"path": "/healthz"}
        )
        assert result.success

    async def test_patch_readiness_path_missing_param_returns_fail(self):
        dispatcher = KubernetesActionDispatcher(MagicMock())
        result = await dispatcher.dispatch("k8s.patch_readiness_path", "api", make_context(), {})
        assert not result.success
        assert result.code == "MISSING_PARAMETER"

    @patch("app.labs.kubernetes.action_dispatcher.client.CoreV1Api")
    async def test_patch_service_selector_calls_patch(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        dispatcher = KubernetesActionDispatcher(MagicMock())
        result = await dispatcher.dispatch(
                "k8s.patch_service_selector",
                "api",
                make_context(),
                {"selector": {"app": "api-broken"}},
        )
        assert result.success
        mock_api.patch_namespaced_service.assert_called_once()

    @patch("app.labs.kubernetes.action_dispatcher.client.CoreV1Api")
    async def test_set_service_selector_is_alias(self, mock_api_cls):
        """k8s.set_service_selector and k8s.patch_service_selector are the same."""
        dispatcher = KubernetesActionDispatcher(MagicMock())
        result = await dispatcher.dispatch(
                "k8s.set_service_selector",
                "api",
                make_context(),
                {"selector": {"app": "api"}},
        )
        assert result.success

    async def test_patch_service_selector_missing_param_returns_fail(self):
        dispatcher = KubernetesActionDispatcher(MagicMock())
        result = await dispatcher.dispatch("k8s.patch_service_selector", "api", make_context(), {})
        assert not result.success
        assert result.code == "MISSING_PARAMETER"


# ── Fixture tests ────────────────────────────────────────────────────────────

class TestFixtures:
    def test_k8s_readiness_path_fixture_exists(self):
        path = SCENARIOS_ROOT / "k8s-readiness-path" / "v1" / "fixtures" / "api-healthy.yaml"
        assert path.exists(), f"Fixture not found: {path}"

    def test_k8s_service_selector_fixture_exists(self):
        path = SCENARIOS_ROOT / "k8s-service-selector" / "v1" / "fixtures" / "api-healthy.yaml"
        assert path.exists(), f"Fixture not found: {path}"

    def test_fixture_yaml_is_valid(self):
        import yaml
        path = SCENARIOS_ROOT / "k8s-readiness-path" / "v1" / "fixtures" / "api-healthy.yaml"
        docs = list(yaml.safe_load_all(path.read_text()))
        assert len(docs) >= 3, "Expected at least ConfigMap, Deployment, Service"
        kinds = {d["kind"] for d in docs if d}
        assert "Deployment" in kinds
        assert "Service" in kinds
        assert "ConfigMap" in kinds

    def test_fixture_deployment_has_healthz_probe(self):
        import yaml
        path = SCENARIOS_ROOT / "k8s-readiness-path" / "v1" / "fixtures" / "api-healthy.yaml"
        docs = list(yaml.safe_load_all(path.read_text()))
        deployment = next(d for d in docs if d and d.get("kind") == "Deployment")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        probe_path = container["readinessProbe"]["httpGet"]["path"]
        assert probe_path == "/healthz", f"Expected /healthz, got {probe_path}"
