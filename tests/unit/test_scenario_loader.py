"""
IF-002: Scenario loader and registry tests.

These are the most security-critical unit tests in the project.
The public/private separation test must remain green forever.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services", "api"))

import json
from pathlib import Path

import pytest
import yaml
from app.scenarios.loader import ScenarioLoadError, load_scenario
from app.scenarios.registry import ScenarioNotFound, ScenarioRegistry

SCENARIOS_ROOT = Path("scenarios")
SCHEMA_PATH = Path("contracts/schemas/scenario.v1.1.json")

# ── Loader tests ──────────────────────────────────────────────────────────────

class TestScenarioLoader:
    def test_loads_k8s_readiness_path(self):
        path = SCENARIOS_ROOT / "k8s-readiness-path" / "v1" / "scenario.yaml"
        scenario = load_scenario(path)
        assert scenario.public.id == "k8s-readiness-path"
        assert scenario.public.version == 1
        assert scenario.public.environment == "kubernetes"
        assert scenario.public.difficulty == "beginner"
        assert scenario.public.summary != ""
        assert scenario.public.target != ""

    def test_loads_k8s_service_selector(self):
        path = SCENARIOS_ROOT / "k8s-service-selector" / "v1" / "scenario.yaml"
        scenario = load_scenario(path)
        assert scenario.public.id == "k8s-service-selector"

    def test_loads_aws_s3_missing_object(self):
        path = SCENARIOS_ROOT / "aws-s3-missing-object" / "v1" / "scenario.yaml"
        scenario = load_scenario(path)
        assert scenario.public.id == "aws-s3-missing-object"
        assert scenario.public.environment == "aws"

    def test_private_data_accessible_from_full(self):
        path = SCENARIOS_ROOT / "k8s-readiness-path" / "v1" / "scenario.yaml"
        scenario = load_scenario(path)
        assert len(scenario.private.private_expected.allowed_root_cause_ids) >= 1
        assert "K8S_READINESS_PATH_MISMATCH" in scenario.private.private_expected.allowed_root_cause_ids
        assert len(scenario.private.private_expected.allowed_remediations) >= 1
        assert len(scenario.private.private_expected.post_fix_checks) >= 1

    def test_fault_data_accessible_from_private(self):
        path = SCENARIOS_ROOT / "k8s-readiness-path" / "v1" / "scenario.yaml"
        scenario = load_scenario(path)
        assert scenario.private.fault.action == "k8s.patch_readiness_path"
        assert scenario.private.fault.ref == "api"
        assert scenario.private.setup.fixture != ""

    def test_rejects_missing_file(self):
        with pytest.raises(ScenarioLoadError, match="not found"):
            load_scenario(Path("scenarios/nonexistent/v1/scenario.yaml"))

    def test_rejects_invalid_yaml(self, tmp_path):
        bad = tmp_path / "scenario.yaml"
        bad.write_text(": : : invalid yaml ::::")
        with pytest.raises(ScenarioLoadError):
            load_scenario(bad)

    def test_rejects_schema_violation(self, tmp_path):
        """Missing required field 'environment' should fail validation."""
        bad_scenario = {
            "schema_version": "1.1",
            "id": "test-broken",
            "version": 1,
            "title": "Broken scenario",
            # 'environment' is missing — should fail JSON Schema validation
            "difficulty": "beginner",
            "public": {"summary": "test", "target": "test"},
            "setup": {
                "fixture": "fixtures/test.yaml",
                "baseline_checks": [{"type": "k8s.deployment_available", "ref": "api"}],
            },
            "fault": {
                "action": "k8s.patch_readiness_path",
                "ref": "api",
                "manifestation_checks": [{"type": "k8s.pod_not_ready", "ref": "api"}],
            },
            "private_expected": {
                "allowed_root_cause_ids": ["TEST_ROOT_CAUSE"],
                "required_evidence_types": ["k8s.get_deployment"],
                "allowed_remediations": ["k8s.set_readiness_path"],
                "post_fix_checks": [{"type": "k8s.deployment_available", "ref": "api"}],
            },
        }
        p = tmp_path / "scenario.yaml"
        p.write_text(yaml.dump(bad_scenario))
        with pytest.raises(ScenarioLoadError, match="Schema validation failed"):
            load_scenario(p)

    def test_rejects_unsupported_schema_version(self, tmp_path):
        """Loader must reject scenarios with unknown schema versions."""
        # Build a valid v1.1 scenario but patch the schema_version to something unknown
        real_path = SCENARIOS_ROOT / "k8s-readiness-path" / "v1" / "scenario.yaml"
        raw = yaml.safe_load(real_path.read_text())
        raw["schema_version"] = "9.9"  # not in the enum
        p = tmp_path / "scenario.yaml"
        p.write_text(yaml.dump(raw))
        with pytest.raises(ScenarioLoadError):
            load_scenario(p)


# ── Registry tests ────────────────────────────────────────────────────────────

class TestScenarioRegistry:
    @pytest.fixture(scope="class")
    @classmethod
    def registry(cls):
        return ScenarioRegistry.load(SCENARIOS_ROOT)

    def test_loads_all_three_scenarios(self, registry):
        assert len(registry) == 3

    def test_list_public_returns_all(self, registry):
        metas = registry.list_public()
        assert len(metas) == 3
        ids = {m.id for m in metas}
        assert "k8s-readiness-path" in ids
        assert "k8s-service-selector" in ids
        assert "aws-s3-missing-object" in ids

    def test_get_public_returns_correct_scenario(self, registry):
        meta = registry.get_public("k8s-readiness-path", 1)
        assert meta.id == "k8s-readiness-path"
        assert meta.version == 1

    def test_get_public_raises_for_unknown(self, registry):
        with pytest.raises(ScenarioNotFound):
            registry.get_public("does-not-exist", 1)

    def test_get_private_returns_private_data(self, registry):
        private = registry.get_private("k8s-readiness-path", 1)
        assert private.id == "k8s-readiness-path"
        assert len(private.private_expected.allowed_root_cause_ids) >= 1

    def test_get_private_raises_for_unknown(self, registry):
        with pytest.raises(ScenarioNotFound):
            registry.get_private("does-not-exist", 1)

    def test_empty_registry_on_missing_root(self):
        registry = ScenarioRegistry.load(Path("nonexistent-dir"))
        assert len(registry) == 0
        assert registry.list_public() == []


# ── PUBLIC/PRIVATE SEPARATION — most critical tests ──────────────────────────

class TestPublicPrivateSeparation:
    """
    These tests verify that private scenario data NEVER appears in
    the public-facing API objects. These tests must remain green
    at all times. Any regression here means ground truth could leak.
    """

    PRIVATE_FIELD_NAMES = {
        "private_expected",
        "allowed_root_cause_ids",
        "allowed_remediations",
        "post_fix_checks",
        "setup",
        "fault",
        "fixture",
        "baseline_checks",
        "manifestation_checks",
    }

    @pytest.fixture(scope="class")
    @classmethod
    def registry(cls):
        return ScenarioRegistry.load(SCENARIOS_ROOT)

    def test_public_meta_has_no_private_fields(self, registry):
        """ScenarioPublicMeta model fields must not include any private field names."""
        from app.scenarios.models import ScenarioPublicMeta
        public_field_names = set(ScenarioPublicMeta.model_fields.keys())
        overlap = public_field_names & self.PRIVATE_FIELD_NAMES
        assert not overlap, (
            f"ScenarioPublicMeta contains private fields: {overlap}. "
            "This would leak ground truth through the API."
        )

    def test_list_public_serializes_without_private_fields(self, registry):
        """Serialized public metadata must not contain any private field keys."""
        for meta in registry.list_public():
            serialized = json.loads(meta.model_dump_json())
            for field in self.PRIVATE_FIELD_NAMES:
                assert field not in serialized, (
                    f"Private field '{field}' found in serialized public metadata "
                    f"for scenario '{meta.id}'. Ground truth leak!"
                )

    def test_public_meta_does_not_contain_root_cause(self, registry):
        """The specific ground-truth root cause IDs must not appear in public metadata."""
        for meta in registry.list_public():
            serialized = meta.model_dump_json()
            # Grab actual root cause IDs from private data
            private = registry.get_private(meta.id, meta.version)
            for rc_id in private.private_expected.allowed_root_cause_ids:
                assert rc_id not in serialized, (
                    f"Root cause ID '{rc_id}' found in public metadata for '{meta.id}'. "
                    "Ground truth leak!"
                )

    def test_public_meta_does_not_contain_remediation_actions(self, registry):
        """Allowed remediation action types must not appear in public metadata."""
        for meta in registry.list_public():
            serialized = meta.model_dump_json()
            private = registry.get_private(meta.id, meta.version)
            for action in private.private_expected.allowed_remediations:
                assert action not in serialized, (
                    f"Remediation action '{action}' found in public metadata for '{meta.id}'. "
                    "Ground truth leak!"
                )
