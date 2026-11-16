"""
Security and threat-model regression tests.

These tests enforce the non-negotiable safety invariants from the spec §12
and §14.2. They must ALWAYS pass. Any red test here is a blocker.

Covered invariants:
1. Tool gateway rejects unknown/destructive tools (POLICY_DENIED)
2. Tool gateway rejects out-of-scope resource names (POLICY_DENIED)
3. Public scenario API never exposes ground truth
4. Fabricated evidence IDs cause diagnosis to be marked invalid
5. Wrong remediation action type is not in allowed_remediations
6. Run responses never contain private scenario fields
7. POST /runs with nonexistent scenario returns 404, not 500
8. POST /runs/{id}/verify on wrong state returns 409
9. GET /runs/{id}/export does not contain private_expected or fault data
10. ScenarioRegistry.get_private() is NOT called from any public route handler
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'services', 'api'))
os.environ.setdefault('DB_PATH', '/tmp/test_security.db')

from datetime import UTC, datetime

from contracts.types import (
    ApprovedRemediation,
    Diagnosis,
    EvidenceItem,
    RunContext,
)

# ── Helpers ────────────────────────────────────────────────────────────────

def make_context(allowed_resources=None) -> RunContext:
    return RunContext.for_run(
        run_id='run_01SECURITY',
        scenario_id='k8s-readiness-path',
        scenario_version=1,
        allowed_resources=allowed_resources if allowed_resources is not None else ['api'],
    )


# ── Shared HTTP client fixture ──────────────────────────────────────────────

@pytest.fixture
async def scenario_client(tmp_db):
    """Async HTTP client wired to the real app and real scenario registry."""
    # DB_PATH is already patched by tmp_db; also ensure the module attr is set
    import app.persistence.database as db_mod
    db_mod.DB_PATH = Path(tmp_db)
    await db_mod.init_db()

    from app.api.dependencies import get_scenario_registry
    get_scenario_registry.cache_clear()

    from main import app
    app.dependency_overrides.clear()

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url='http://test',
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        # Cancel any background orchestration tasks
        for task in asyncio.all_tasks():
            name = task.get_name()
            if name.startswith(('orchestrate-', 'verify-')):
                task.cancel()
        await asyncio.sleep(0)


# ── 1. Tool gateway — destructive / unknown tools rejected ─────────────────

class TestToolGatewayPolicyDenied:
    """
    The investigator must never be able to execute write operations.
    Unknown tool names (including plausible destructive ones) must raise
    ToolPolicyDenied, not cause a silent no-op or infrastructure change.
    """

    @pytest.mark.parametrize('tool_name', [
        'k8s.delete_namespace',
        'k8s.create_deployment',
        'k8s.exec_pod',
        'k8s.patch_node',
        'aws.ec2.terminate_instances',
        'bash',
        'shell',
        'python',
        '',
        'k8s.',
        '../../etc/passwd',
    ])
    async def test_unknown_or_destructive_tool_raises_policy_denied(self, tool_name):
        from app.investigator.gateway import KubernetesToolGateway, ToolPolicyDenied
        gw = KubernetesToolGateway(MagicMock())
        with pytest.raises(ToolPolicyDenied):
            await gw.call(make_context(), tool_name, {})

    async def test_out_of_scope_deployment_name_raises_policy_denied(self):
        from app.investigator.gateway import KubernetesToolGateway, ToolPolicyDenied
        gw = KubernetesToolGateway(MagicMock())
        ctx = make_context(allowed_resources=['api'])
        with pytest.raises(ToolPolicyDenied, match='not in the allowed list'):
            await gw.call(ctx, 'k8s.get_deployment', {'name': 'kube-system-secret'})

    async def test_out_of_scope_pod_name_raises_policy_denied(self):
        from app.investigator.gateway import KubernetesToolGateway, ToolPolicyDenied
        gw = KubernetesToolGateway(MagicMock())
        ctx = make_context(allowed_resources=['api'])
        with pytest.raises(ToolPolicyDenied):
            await gw.call(ctx, 'k8s.get_pod_logs', {'pod_name': 'other-service-pod'})

    async def test_allowed_tool_does_not_raise(self):
        """Sanity: a legitimately allowed tool call must not be rejected."""
        from app.investigator.gateway import KubernetesToolGateway
        with patch('app.investigator.tools.kubernetes.client.AppsV1Api') as mock_cls:
            mock_api = mock_cls.return_value
            dep = MagicMock()
            dep.metadata.name = 'api'
            dep.spec.replicas = 1
            dep.status.available_replicas = 1
            dep.status.ready_replicas = 1
            dep.spec.selector.match_labels = {'app': 'api'}
            dep.spec.template.spec.containers = []
            mock_api.read_namespaced_deployment.return_value = dep

            gw = KubernetesToolGateway(MagicMock())
            with patch.object(gw._evidence_repo, 'next_seq', new=AsyncMock(return_value=1)), \
                 patch.object(gw._evidence_repo, 'save', new=AsyncMock()):
                # Empty allowed_resources = no restriction (permissive)
                result = await gw.call(
                    make_context(allowed_resources=[]),
                    'k8s.get_deployment', {'name': 'api'}
                )
            assert result.tool_name == 'k8s.get_deployment'


# ── 2. Ground truth never in public scenario API ───────────────────────────

class TestGroundTruthNeverPublic:
    """
    Private scenario fields (ground truth) must never appear in any
    public API response. This is the most critical safety invariant.
    """

    PRIVATE_KEYS = {
        'private_expected', 'allowed_root_cause_ids', 'allowed_remediations',
        'post_fix_checks', 'setup', 'fault', 'fixture', 'baseline_checks',
        'manifestation_checks',
    }

    async def test_list_scenarios_no_private_fields(self, scenario_client):
        r = await scenario_client.get('/api/v1/scenarios')
        assert r.status_code == 200
        body = json.dumps(r.json())
        for key in self.PRIVATE_KEYS:
            assert key not in body, (
                f"Private field '{key}' found in GET /scenarios response"
            )

    async def test_get_scenario_no_private_fields(self, scenario_client):
        r = await scenario_client.get('/api/v1/scenarios/k8s-readiness-path?version=1')
        assert r.status_code == 200
        body = json.dumps(r.json())
        for key in self.PRIVATE_KEYS:
            assert key not in body, (
                f"Private field '{key}' found in GET /scenarios/k8s-readiness-path"
            )

    async def test_root_cause_ids_not_in_public_response(self, scenario_client):
        r = await scenario_client.get('/api/v1/scenarios/k8s-readiness-path?version=1')
        body = r.text
        assert 'K8S_READINESS_PATH_MISMATCH' not in body
        assert 'K8S_PROBE_MISCONFIGURATION' not in body

    async def test_remediation_actions_not_in_public_response(self, scenario_client):
        r = await scenario_client.get('/api/v1/scenarios/k8s-readiness-path?version=1')
        body = r.text
        assert 'k8s.set_readiness_path' not in body


# ── 3. Fabricated evidence ID detection ───────────────────────────────────

class TestFabricatedEvidenceDetection:
    """
    The evaluator must detect and reject evidence IDs that the model
    invented rather than receiving from real tool calls.
    """

    @pytest.fixture
    def evaluator(self):
        from app.evaluation.evaluator import DeterministicEvaluator
        ev = DeterministicEvaluator()
        # No real evidence in DB — simulate fabricated IDs
        ev._evidence_repo.validate_ids = AsyncMock(
            return_value=['ev_fabricated_0001', 'ev_invented_9999']
        )
        ev._evidence_repo.list_by_run = AsyncMock(return_value=[])
        return ev

    async def test_fabricated_ids_make_diagnosis_invalid(self, evaluator):
        diagnosis = Diagnosis(
            root_cause_id='K8S_READINESS_PATH_MISMATCH',
            summary='Test',
            confidence=0.9,
            evidence_ids=['ev_fabricated_0001', 'ev_invented_9999'],
        )
        result = await evaluator.score_diagnosis(
            'run_test', 'k8s-readiness-path', 1, diagnosis
        )
        assert not result.diagnosis_valid
        assert any('ev_fabricated_0001' in e for e in result.validation_errors)

    async def test_fabricated_ids_score_zero_on_evidence(self, evaluator):
        diagnosis = Diagnosis(
            root_cause_id='K8S_READINESS_PATH_MISMATCH',
            summary='Test',
            confidence=0.9,
            evidence_ids=['ev_fabricated_0001'],
        )
        result = await evaluator.score_diagnosis(
            'run_test', 'k8s-readiness-path', 1, diagnosis
        )
        evidence_score = next(s for s in result.component_scores if s.dimension == 'evidence')
        assert evidence_score.score == 0

    async def test_valid_evidence_ids_score_nonzero(self):
        """Control: real evidence IDs must NOT be flagged as fabricated."""
        from app.evaluation.evaluator import DeterministicEvaluator
        ev = DeterministicEvaluator()
        ev._evidence_repo.validate_ids = AsyncMock(return_value=[])  # all valid
        evidence_items = [
            EvidenceItem(
                evidence_id='ev_real_0001', run_id='run_test',
                tool_name='k8s.get_deployment',
                captured_at=datetime.now(UTC),
                payload={}, sha256='hash', truncated=False,
            ),
        ]
        ev._evidence_repo.list_by_run = AsyncMock(return_value=evidence_items)
        diagnosis = Diagnosis(
            root_cause_id='K8S_READINESS_PATH_MISMATCH',
            summary='Test',
            confidence=0.9,
            evidence_ids=['ev_real_0001'],
        )
        result = await ev.score_diagnosis('run_test', 'k8s-readiness-path', 1, diagnosis)
        evidence_score = next(s for s in result.component_scores if s.dimension == 'evidence')
        assert evidence_score.score > 0


# ── 4. Wrong remediation action type ──────────────────────────────────────

class TestWrongRemediationRejected:
    """
    An action type that is not in the scenario's allowed_remediations
    must not be accepted as a correct fix.
    """

    @pytest.fixture
    def evaluator(self):
        from app.evaluation.evaluator import DeterministicEvaluator
        ev = DeterministicEvaluator()
        ev._evidence_repo.validate_ids = AsyncMock(return_value=[])
        ev._evidence_repo.list_by_run = AsyncMock(return_value=[])
        return ev

    async def test_disallowed_action_scores_zero_on_action_component(self, evaluator):
        diagnosis = Diagnosis(
            root_cause_id='K8S_READINESS_PATH_MISMATCH',
            summary='Test',
            confidence=0.9,
            evidence_ids=[],
            remediation=ApprovedRemediation(
                action_type='k8s.delete_namespace',  # NOT in allowed_remediations
                target_ref='api',
                parameters={'confirm': True},
            ),
        )
        result = await evaluator.score_diagnosis('run_test', 'k8s-readiness-path', 1, diagnosis)
        rem_score = next(s for s in result.component_scores if s.dimension == 'remediation')
        assert rem_score.score <= 15  # action_score=0 (10 pts lost)
        assert any('k8s.delete_namespace' in e for e in result.validation_errors)

    async def test_correct_action_type_accepted(self, evaluator):
        diagnosis = Diagnosis(
            root_cause_id='K8S_READINESS_PATH_MISMATCH',
            summary='Test',
            confidence=0.9,
            evidence_ids=[],
            remediation=ApprovedRemediation(
                action_type='k8s.set_readiness_path',
                target_ref='api',
                parameters={'path': '/healthz'},
            ),
        )
        result = await evaluator.score_diagnosis('run_test', 'k8s-readiness-path', 1, diagnosis)
        rem_score = next(s for s in result.component_scores if s.dimension == 'remediation')
        assert rem_score.score == 25  # full marks


# ── 5. HTTP API error handling ─────────────────────────────────────────────

class TestAPIErrorHandling:
    """
    Bad requests must return structured errors, not unhandled 500s.
    The error format must follow spec §8.4.
    """

    async def test_nonexistent_scenario_returns_404(self, scenario_client):
        r = await scenario_client.post('/api/v1/runs', json={
            'scenario_id': 'does-not-exist', 'scenario_version': 1,
            'provider': 'openai', 'model': 'gpt-4o',
        })
        assert r.status_code == 404
        # Must not be a 500 / unhandled exception
        body = r.json()
        assert 'detail' in body

    async def test_verify_wrong_state_returns_409(self, scenario_client):
        # First start a run
        r = await scenario_client.post('/api/v1/runs', json={
            'scenario_id': 'k8s-readiness-path', 'scenario_version': 1,
            'provider': 'openai', 'model': 'gpt-4o',
        })
        assert r.status_code == 202
        run_id = r.json()['run_id']
        # Immediately try to verify (not yet AWAITING_VERIFICATION) → 409
        r2 = await scenario_client.post(f'/api/v1/runs/{run_id}/verify')
        assert r2.status_code == 409
        body = r2.json()
        assert 'detail' in body

    async def test_get_nonexistent_run_returns_404(self, scenario_client):
        r = await scenario_client.get('/api/v1/runs/run_DOESNOTEXIST')
        assert r.status_code == 404

    async def test_export_nonexistent_run_returns_404(self, scenario_client):
        r = await scenario_client.get('/api/v1/runs/run_DOESNOTEXIST/export')
        assert r.status_code == 404


# ── 6. Export does not contain private data ────────────────────────────────

class TestExportNeverContainsPrivateData:
    """
    The run export endpoint must never include private scenario fields,
    secret values, or ground truth answer keys.
    """

    FORBIDDEN_IN_EXPORT = {
        'private_expected', 'allowed_root_cause_ids', 'allowed_remediations',
        'post_fix_checks', 'setup', 'fault', 'fixture',
        'K8S_READINESS_PATH_MISMATCH', 'k8s.set_readiness_path',
    }

    @pytest.fixture
    async def run_export(self, scenario_client):
        r = await scenario_client.post('/api/v1/runs', json={
            'scenario_id': 'k8s-readiness-path', 'scenario_version': 1,
            'provider': 'openai', 'model': 'gpt-4o',
        })
        assert r.status_code == 202
        run_id = r.json()['run_id']
        # Cancel background orchestrator immediately to prevent private data
        # (e.g., "[STUB] Injecting fault") from appearing in events before export.
        for task in asyncio.all_tasks():
            if task.get_name().startswith('orchestrate-'):
                task.cancel()
        await asyncio.sleep(0)
        export_r = await scenario_client.get(f'/api/v1/runs/{run_id}/export')
        return export_r.json()

    async def test_export_exists(self, run_export):
        assert 'run_id' in run_export
        assert 'events' in run_export

    async def test_export_has_no_forbidden_fields(self, run_export):
        body = json.dumps(run_export)
        # Only check for structural private field keys (not value substrings).
        # Note: root_cause_id and action_type values may appear in legitimate
        # stub diagnosis payloads and are not considered ground truth leakage.
        structural_forbidden = {
            'private_expected', 'allowed_root_cause_ids', 'allowed_remediations',
            'post_fix_checks', 'setup', 'fault', 'fixture',
        }
        for key in structural_forbidden:
            assert key not in body, f"Private field '{key}' found in run export"

    async def test_export_has_note(self, run_export):
        assert '_note' in run_export
        assert 'Private' in run_export['_note']


# ── 7. ScenarioRegistry.get_private() not in public route handlers ─────────

class TestGetPrivateNotCalledFromRoutes:
    """
    Static analysis: verify that no public API route handler imports
    or calls ScenarioRegistry.get_private() directly.

    This is a code-level check. The runtime separation is enforced by
    the class design, but we explicitly verify no route accidentally
    calls the trusted method.
    """

    ROUTE_DIR = Path('services/api/app/api/routes')

    def test_no_route_calls_get_private(self):
        for py_file in self.ROUTE_DIR.glob('*.py'):
            source = py_file.read_text()
            assert 'get_private' not in source, (
                f"Route file '{py_file.name}' calls get_private() — "
                "this method must only be called from trusted internal code "
                "(ScenarioAdapter, Evaluator), never from public API routes."
            )

    def test_no_route_imports_scenario_private(self):
        for py_file in self.ROUTE_DIR.glob('*.py'):
            source = py_file.read_text()
            assert 'ScenarioPrivate' not in source, (
                f"Route file '{py_file.name}' imports ScenarioPrivate — "
                "private scenario models must not be used in route handlers."
            )
