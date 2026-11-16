"""Tests for the DeterministicEvaluator."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'services', 'api'))
os.environ.setdefault("DB_PATH", "/tmp/test_evaluator.db")

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from app.evaluation.evaluator import DeterministicEvaluator

from contracts.types import (
    ApprovedRemediation,
    ComponentScoreStatus,
    Diagnosis,
    EvidenceItem,
)


def make_diagnosis(root_cause_id="K8S_READINESS_PATH_MISMATCH",
                   evidence_ids=None, remediation=True) -> Diagnosis:
    rem = None
    if remediation:
        rem = ApprovedRemediation(
            action_type="k8s.set_readiness_path",
            target_ref="api",
            parameters={"path": "/healthz"},
        )
    return Diagnosis(
        root_cause_id=root_cause_id,
        summary="Test diagnosis",
        confidence=0.9,
        evidence_ids=evidence_ids or ["ev_test_0001", "ev_test_0002"],
        remediation=rem,
    )


def make_evidence(tool_name="k8s.get_deployment", ev_id="ev_test_0001") -> EvidenceItem:
    return EvidenceItem(
        evidence_id=ev_id,
        run_id="run_test",
        tool_name=tool_name,
        captured_at=datetime.now(UTC),
        payload={},
        sha256="hash",
        truncated=False,
    )


@pytest.fixture
def evaluator_with_mocks():
    ev = DeterministicEvaluator()
    # Mock evidence repo
    evidence_items = [
        make_evidence("k8s.get_deployment", "ev_test_0001"),
        make_evidence("k8s.list_pods", "ev_test_0002"),
    ]
    ev._evidence_repo.validate_ids = AsyncMock(return_value=[])  # no fabrication
    ev._evidence_repo.list_by_run = AsyncMock(return_value=evidence_items)
    return ev


class TestRootCauseScoring:
    async def test_correct_root_cause_scores_full(self, evaluator_with_mocks):
        result = await evaluator_with_mocks.score_diagnosis(
            "run_test", "k8s-readiness-path", 1,
            make_diagnosis("K8S_READINESS_PATH_MISMATCH"),
        )
        rc = next(s for s in result.component_scores if s.dimension == "root_cause")
        assert rc.score == 40
        assert rc.status == ComponentScoreStatus.SCORED

    async def test_wrong_root_cause_scores_zero(self, evaluator_with_mocks):
        result = await evaluator_with_mocks.score_diagnosis(
            "run_test", "k8s-readiness-path", 1,
            make_diagnosis("WRONG_CAUSE"),
        )
        rc = next(s for s in result.component_scores if s.dimension == "root_cause")
        assert rc.score == 0
        assert not result.diagnosis_valid

    async def test_unknown_root_cause_scores_zero_but_valid(self, evaluator_with_mocks):
        """UNKNOWN is a valid special value but scores zero."""
        result = await evaluator_with_mocks.score_diagnosis(
            "run_test", "k8s-readiness-path", 1,
            make_diagnosis("UNKNOWN"),
        )
        rc = next(s for s in result.component_scores if s.dimension == "root_cause")
        assert rc.score == 0
        # UNKNOWN does NOT add to validation_errors
        assert "UNKNOWN" not in str(result.validation_errors)

    async def test_alias_root_cause_scores_full(self, evaluator_with_mocks):
        """K8S_PROBE_MISCONFIGURATION is an alias for the readiness path scenario."""
        result = await evaluator_with_mocks.score_diagnosis(
            "run_test", "k8s-readiness-path", 1,
            make_diagnosis("K8S_PROBE_MISCONFIGURATION"),
        )
        rc = next(s for s in result.component_scores if s.dimension == "root_cause")
        assert rc.score == 40


class TestEvidenceScoring:
    async def test_fabricated_evidence_scores_zero(self):
        ev = DeterministicEvaluator()
        ev._evidence_repo.validate_ids = AsyncMock(return_value=["ev_fake_0001"])
        ev._evidence_repo.list_by_run = AsyncMock(return_value=[])
        result = await ev.score_diagnosis(
            "run_test", "k8s-readiness-path", 1,
            make_diagnosis(evidence_ids=["ev_fake_0001"]),
        )
        evidence = next(s for s in result.component_scores if s.dimension == "evidence")
        assert evidence.score == 0
        assert not result.diagnosis_valid
        assert any("ev_fake_0001" in e for e in result.validation_errors)

    async def test_full_evidence_coverage_scores_max(self, evaluator_with_mocks):
        """Both required types (k8s.get_deployment, k8s.list_pods) are cited."""
        result = await evaluator_with_mocks.score_diagnosis(
            "run_test", "k8s-readiness-path", 1,
            make_diagnosis(evidence_ids=["ev_test_0001", "ev_test_0002"]),
        )
        evidence = next(s for s in result.component_scores if s.dimension == "evidence")
        assert evidence.score == 25  # 10 (IDs valid) + 15 (full type coverage)


class TestRemediationScoring:
    async def test_no_remediation_is_not_evaluated(self, evaluator_with_mocks):
        result = await evaluator_with_mocks.score_diagnosis(
            "run_test", "k8s-readiness-path", 1,
            make_diagnosis(remediation=False),
        )
        rem = next(s for s in result.component_scores if s.dimension == "remediation")
        assert rem.status == ComponentScoreStatus.NOT_EVALUATED

    async def test_correct_remediation_scores_full(self, evaluator_with_mocks):
        result = await evaluator_with_mocks.score_diagnosis(
            "run_test", "k8s-readiness-path", 1,
            make_diagnosis(),
        )
        rem = next(s for s in result.component_scores if s.dimension == "remediation")
        assert rem.score == 25
        assert rem.status == ComponentScoreStatus.SCORED

    async def test_wrong_action_type_scores_zero_on_action(self, evaluator_with_mocks):
        diagnosis = make_diagnosis()
        diagnosis = diagnosis.model_copy(update={"remediation": ApprovedRemediation(
            action_type="k8s.delete_namespace",  # not in allowed_remediations
            target_ref="api",
            parameters={"confirm": True},
        )})
        result = await evaluator_with_mocks.score_diagnosis(
            "run_test", "k8s-readiness-path", 1, diagnosis,
        )
        rem = next(s for s in result.component_scores if s.dimension == "remediation")
        # action_score=0, ref_score=5, params_score=10 → 15 max without action credit
        assert rem.score <= 15
        assert not result.diagnosis_valid


class TestEfficiencyScoring:
    async def test_within_budget_scores_full(self, evaluator_with_mocks):
        result = await evaluator_with_mocks.score_diagnosis(
            "run_test", "k8s-readiness-path", 1,
            make_diagnosis(), tool_call_count=5, investigation_ms=30000,
        )
        eff = next(s for s in result.component_scores if s.dimension == "efficiency")
        assert eff.score == 10

    async def test_over_budget_reduces_score(self, evaluator_with_mocks):
        result = await evaluator_with_mocks.score_diagnosis(
            "run_test", "k8s-readiness-path", 1,
            make_diagnosis(), tool_call_count=20, investigation_ms=200000,
        )
        eff = next(s for s in result.component_scores if s.dimension == "efficiency")
        assert eff.score < 10


class TestTotalScoring:
    async def test_total_scored_excludes_not_evaluated(self, evaluator_with_mocks):
        """Total must not include NOT_EVALUATED dimensions."""
        result = await evaluator_with_mocks.score_diagnosis(
            "run_test", "k8s-readiness-path", 1,
            make_diagnosis(remediation=False),
        )
        total_max = result.total_max_scored
        # Remediation is NOT_EVALUATED, so max is 40+25+10=75, not 100
        assert total_max == 75

    async def test_update_verification_status(self, evaluator_with_mocks):
        result = await evaluator_with_mocks.score_diagnosis(
            "run_test", "k8s-readiness-path", 1, make_diagnosis(),
        )
        evaluator = DeterministicEvaluator()
        updated = evaluator.update_verification_status(result, passed=True)
        assert updated.verification_status == "passed"
        updated_fail = evaluator.update_verification_status(result, passed=False)
        assert updated_fail.verification_status == "failed"
