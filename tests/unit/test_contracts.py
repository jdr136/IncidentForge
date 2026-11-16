"""Smoke-test that the shared contracts are importable and coherent."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from contracts.types import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    ApprovedRemediation,
    CheckResult,
    CleanupPolicy,
    Diagnosis,
    RunContext,
    RunStatus,
)


def test_all_terminal_statuses_have_no_outbound_transitions():
    for status in TERMINAL_STATUSES:
        assert status not in ALLOWED_TRANSITIONS, (
            f"{status} is terminal but has outbound transitions"
        )


def test_all_non_terminal_statuses_have_transitions():
    non_terminal = set(RunStatus) - TERMINAL_STATUSES
    for status in non_terminal:
        assert status in ALLOWED_TRANSITIONS, (
            f"{status} is non-terminal but has no outbound transitions"
        )


def test_run_context_factory():
    ctx = RunContext.for_run(
        run_id="run_01JABCDEF",
        scenario_id="k8s-readiness-path",
        scenario_version=1,
        allowed_resources=["api", "api-service"],
    )
    assert ctx.namespace == "if-01jabcde"
    assert ctx.run_short_id == "01jabcde"
    assert ctx.scenario_id == "k8s-readiness-path"


def test_check_result_helpers():
    ok = CheckResult.ok("DEPLOYMENT_AVAILABLE", "Deployment is available")
    assert ok.success is True
    fail = CheckResult.fail("POD_NOT_READY", "Pod stuck")
    assert fail.success is False


def test_diagnosis_confidence_validation():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Diagnosis(
            root_cause_id="X",
            summary="test",
            confidence=1.5,
            evidence_ids=[],
        )


def test_approved_remediation_rejects_empty_action():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ApprovedRemediation(action_type="  ", target_ref="api", parameters={})


def test_cleanup_policy_values():
    assert CleanupPolicy.LAZY == "lazy"
    assert CleanupPolicy.IMMEDIATE == "immediate"
    assert CleanupPolicy.MANUAL == "manual"
