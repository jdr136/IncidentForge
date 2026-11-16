"""
Tests for the ToolGateway and EvidenceStore.
No live Kubernetes cluster required — k8s client is mocked.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'services', 'api'))
os.environ.setdefault("DB_PATH", "/tmp/test_gateway.db")

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.investigator.gateway import KubernetesToolGateway, ToolPolicyDenied
from app.investigator.tools.registry import KUBERNETES_TOOLS, TOOLS_BY_NAME
from app.persistence.evidence_repository import EvidenceRepository

from contracts.types import EvidenceItem, RunContext


def make_context(allowed_resources=None) -> RunContext:
    return RunContext.for_run(
        run_id="run_01TESTRUN1",
        scenario_id="k8s-readiness-path",
        scenario_version=1,
        allowed_resources=["api"] if allowed_resources is None else allowed_resources,
    )


async def _create_run(run_id: str) -> None:
    """Insert a minimal run record to satisfy FK constraints in the evidence table."""
    from datetime import datetime

    from app.persistence.database import get_db
    now = datetime.now(UTC).isoformat()
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO runs "
            "(id, scenario_id, scenario_version, provider, model, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (run_id, "test", 1, "test", "test", "QUEUED", now, now),
        )
        await db.commit()
    finally:
        await db.close()


# ── Tool registry tests ──────────────────────────────────────────────────────

class TestToolRegistry:
    def test_all_k8s_tools_in_registry(self):
        expected = {"k8s.list_pods", "k8s.get_deployment", "k8s.get_service",
                    "k8s.list_events", "k8s.get_pod_logs"}
        assert expected.issubset(set(TOOLS_BY_NAME.keys()))

    def test_tools_have_required_fields(self):
        for tool in KUBERNETES_TOOLS:
            assert tool.name
            assert tool.description
            assert isinstance(tool.parameters_schema, dict)


# ── Policy enforcement tests ─────────────────────────────────────────────────

class TestToolGatewayPolicy:
    def test_unknown_tool_raises_policy_denied(self):
        gateway = KubernetesToolGateway(MagicMock())
        with pytest.raises(ToolPolicyDenied, match="Unknown tool"):
            asyncio.run(
                gateway.call(make_context(), "k8s.delete_namespace", {})
            )

    def test_out_of_scope_resource_raises_policy_denied(self):
        gateway = KubernetesToolGateway(MagicMock())
        ctx = make_context(allowed_resources=["api"])
        with pytest.raises(ToolPolicyDenied, match="not in the allowed list"):
            asyncio.run(
                gateway.call(ctx, "k8s.get_deployment", {"name": "other-deployment"})
            )

    def test_allowed_resource_passes_policy(self):
        """Allowed resource + mocked k8s call should proceed."""
        with patch("app.investigator.tools.kubernetes.client.AppsV1Api") as mock_cls:
            mock_api = mock_cls.return_value
            dep = MagicMock()
            dep.metadata.name = "api"
            dep.spec.replicas = 1
            dep.status.available_replicas = 0
            dep.status.ready_replicas = 0
            dep.spec.selector.match_labels = {"app": "api"}
            dep.spec.template.spec.containers = []
            mock_api.read_namespaced_deployment.return_value = dep

            gateway = KubernetesToolGateway(MagicMock())
            ctx = make_context(allowed_resources=["api"])

            # Should not raise — returns evidence item
            # (skips DB write in this test via patching evidence_repo)
            with patch.object(gateway._evidence_repo, 'next_seq', new=AsyncMock(return_value=1)), \
                 patch.object(gateway._evidence_repo, 'save', new=AsyncMock()):
                result = asyncio.run(
                    gateway.call(ctx, "k8s.get_deployment", {"name": "api"})
                )
            assert result.evidence_id.startswith("ev_")
            assert result.tool_name == "k8s.get_deployment"

    def test_no_allowed_resources_means_no_restriction(self):
        """Empty allowed_resources = permissive (for testing without restrictions)."""
        with patch("app.investigator.tools.kubernetes.client.AppsV1Api") as mock_cls:
            mock_api = mock_cls.return_value
            dep = MagicMock()
            dep.metadata.name = "anything"
            dep.spec.replicas = 1
            dep.status.available_replicas = 1
            dep.status.ready_replicas = 1
            dep.spec.selector.match_labels = {}
            dep.spec.template.spec.containers = []
            mock_api.read_namespaced_deployment.return_value = dep

            gateway = KubernetesToolGateway(MagicMock())
            ctx = make_context(allowed_resources=[])  # no restriction
            with patch.object(gateway._evidence_repo, 'next_seq', new=AsyncMock(return_value=1)), \
                 patch.object(gateway._evidence_repo, 'save', new=AsyncMock()):
                result = asyncio.run(
                    gateway.call(ctx, "k8s.get_deployment", {"name": "anything"})
                )
            assert result.evidence_id.startswith("ev_")


# ── Evidence ID format tests ─────────────────────────────────────────────────

class TestEvidenceIds:
    def test_evidence_id_format(self):
        ctx = make_context()
        # run_short_id is first 8 chars of "01TESTRUN1" after "run_"
        # run_id = "run_01TESTRUN1" → short = "01testrun"[:8] = "01testru"
        assert ctx.run_short_id == "01testru"

        with patch("app.investigator.tools.kubernetes.client.AppsV1Api") as mock_cls:
            mock_api = mock_cls.return_value
            dep = MagicMock()
            dep.metadata.name = "api"
            dep.spec.replicas = 1
            dep.status.available_replicas = 1
            dep.status.ready_replicas = 1
            dep.spec.selector.match_labels = {"app": "api"}
            dep.spec.template.spec.containers = []
            mock_api.read_namespaced_deployment.return_value = dep

            gateway = KubernetesToolGateway(MagicMock())
            with patch.object(gateway._evidence_repo, 'next_seq', new=AsyncMock(return_value=3)), \
                 patch.object(gateway._evidence_repo, 'save', new=AsyncMock()):
                result = asyncio.run(
                    gateway.call(ctx, "k8s.get_deployment", {"name": "api"})
                )
            # Format: ev_{run_short_id}_{seq:04d}
            assert result.evidence_id == f"ev_{ctx.run_short_id}_0003"

    def test_evidence_sha256_matches_payload(self):
        import hashlib
        ctx = make_context()
        with patch("app.investigator.tools.kubernetes.client.AppsV1Api") as mock_cls:
            mock_api = mock_cls.return_value
            dep = MagicMock()
            dep.metadata.name = "api"
            dep.spec.replicas = 1
            dep.status.available_replicas = 1
            dep.status.ready_replicas = 1
            dep.spec.selector.match_labels = {}
            dep.spec.template.spec.containers = []
            mock_api.read_namespaced_deployment.return_value = dep

            gateway = KubernetesToolGateway(MagicMock())
            with patch.object(gateway._evidence_repo, 'next_seq', new=AsyncMock(return_value=1)), \
                 patch.object(gateway._evidence_repo, 'save', new=AsyncMock()):
                result = asyncio.run(
                    gateway.call(ctx, "k8s.get_deployment", {"name": "api"})
                )
            expected_hash = hashlib.sha256(
                json.dumps(result.payload, sort_keys=True).encode()
            ).hexdigest()
            assert result.sha256 == expected_hash


# ── EvidenceRepository tests (uses tmp DB) ───────────────────────────────────

class TestEvidenceRepository:
    @pytest.fixture(autouse=True)
    def use_tmp_db(self, tmp_db):
        pass  # tmp_db fixture from conftest.py patches DB_PATH

    def test_save_and_retrieve(self):
        from datetime import datetime
        repo = EvidenceRepository()
        asyncio.run(_create_run("run_test"))
        item = EvidenceItem(
            evidence_id="ev_test0001_0001",
            run_id="run_test",
            tool_name="k8s.list_pods",
            captured_at=datetime.now(UTC),
            payload={"pods": [], "count": 0},
            sha256="abc123",
            truncated=False,
        )
        asyncio.run(repo.save(item))
        items = asyncio.run(repo.list_by_run("run_test"))
        assert len(items) == 1
        assert items[0].evidence_id == "ev_test0001_0001"

    def test_validate_ids_returns_missing(self):
        repo = EvidenceRepository()
        missing = asyncio.run(
            repo.validate_ids("run_test", ["ev_missing_0001", "ev_missing_0002"])
        )
        assert "ev_missing_0001" in missing
        assert "ev_missing_0002" in missing

    def test_validate_ids_returns_empty_for_valid(self):
        from datetime import datetime
        repo = EvidenceRepository()
        asyncio.run(_create_run("run_test2"))
        item = EvidenceItem(
            evidence_id="ev_test0001_0001",
            run_id="run_test2",
            tool_name="k8s.list_pods",
            captured_at=datetime.now(UTC),
            payload={},
            sha256="xyz",
            truncated=False,
        )
        asyncio.run(repo.save(item))
        missing = asyncio.run(
            repo.validate_ids("run_test2", ["ev_test0001_0001"])
        )
        assert missing == []

    def test_next_seq_increments(self):
        from datetime import datetime
        repo = EvidenceRepository()
        asyncio.run(_create_run("run_seq_test"))
        seq1 = asyncio.run(repo.next_seq("run_seq_test"))
        assert seq1 == 1
        item = EvidenceItem(
            evidence_id=f"ev_seqtest_{seq1:04d}",
            run_id="run_seq_test",
            tool_name="k8s.list_pods",
            captured_at=datetime.now(UTC),
            payload={},
            sha256="hash1",
            truncated=False,
        )
        asyncio.run(repo.save(item))
        seq2 = asyncio.run(repo.next_seq("run_seq_test"))
        assert seq2 == 2
