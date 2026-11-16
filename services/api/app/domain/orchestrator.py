"""
Run orchestrator — drives the lifecycle background task.

Accepts optional real implementations of:
  - scenario_adapter: KubernetesAdapter (for lab setup/teardown/verify)
  - tool_gateway: KubernetesToolGateway (for read-only evidence gathering)
  - provider_adapter: OpenAIProviderAdapter (for AI investigation)

When all three are provided (and OPENAI_API_KEY is configured), runs the
full real pipeline. When any is None, falls back to stub behaviour so the
demo works even without a live k8s cluster or API key.

Stubs are clearly labelled in events/diagnoses so it is obvious when
running in stub mode.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
from app.domain.event_bus import event_bus
from app.domain.state_machine import InvalidTransitionError, transition  # noqa: F401
from app.persistence.event_repository import EventRepository
from app.persistence.run_repository import RunRepository
from contracts.types import (
    IncidentSummary,
    RunContext,
    RunEvent,
    RunEventType,
    RunStatus,
)

logger = logging.getLogger(__name__)

_SCENARIOS_ROOT = Path(__file__).resolve().parents[5] / "scenarios"


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

async def _publish(
    run_id: str,
    event_repo: EventRepository,
    event_type: RunEventType,
    stage: RunStatus,
    message: str,
    payload: dict | None = None,
) -> None:
    """Persist and broadcast one RunEvent."""
    event = await event_repo.append(RunEvent(
        id=0,
        run_id=run_id,
        seq=0,
        at=datetime.now(UTC),
        type=event_type,
        stage=stage,
        message=message,
        payload=payload or {},
    ))
    await event_bus.publish(event)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

async def run_scenario(
    run_id: str,
    scenario_id: str,
    run_repo: RunRepository,
    event_repo: EventRepository,
    scenario_adapter: Any = None,
    tool_gateway: Any = None,
    provider_adapter: Any = None,
) -> None:
    """
    Main lifecycle background task.
    Pass real adapter/gateway/provider for full execution;
    pass None to use stub behaviour.
    """
    try:
        await _run_lifecycle(
            run_id, scenario_id, run_repo, event_repo,
            scenario_adapter, tool_gateway, provider_adapter,
        )
    except Exception as exc:
        logger.exception("Orchestrator error for run %s: %s", run_id, exc)
        try:
            await run_repo.update_status(run_id, RunStatus.FAILED, error_code="INTERNAL_ERROR")
            await _publish(run_id, event_repo, RunEventType.RUN_FAILED,
                           RunStatus.FAILED, f"Internal error: {exc}")
        except Exception:
            logger.exception("Failed to record error for run %s", run_id)


async def run_verification(
    run_id: str,
    run_repo: RunRepository,
    event_repo: EventRepository,
    scenario_adapter: Any = None,
    diagnosis_dict: dict | None = None,
) -> None:
    """
    Verification background task — triggered by POST /runs/{id}/verify.
    Applies the proposed fix via scenario_adapter, then runs postconditions.
    Falls back to stub if no adapter.
    """
    try:
        await run_repo.update_status(run_id, RunStatus.VERIFYING)
        await _publish(run_id, event_repo, RunEventType.STAGE_CHANGED,
                       RunStatus.VERIFYING, "Applying fix and running postcondition checks")

        if scenario_adapter and diagnosis_dict:
            await _real_verification(run_id, run_repo, event_repo, scenario_adapter, diagnosis_dict)
        else:
            await _stub_verification(run_id, run_repo, event_repo)
    except Exception as exc:
        logger.exception("Verification error for run %s: %s", run_id, exc)
        await run_repo.update_status(run_id, RunStatus.FAILED, error_code="INTERNAL_ERROR")
        await _publish(run_id, event_repo, RunEventType.RUN_FAILED,
                       RunStatus.FAILED, f"Verification error: {exc}")


# ---------------------------------------------------------------------------
# Lifecycle implementation
# ---------------------------------------------------------------------------

async def _run_lifecycle(
    run_id: str,
    scenario_id: str,
    run_repo: RunRepository,
    event_repo: EventRepository,
    scenario_adapter: Any,
    tool_gateway: Any,
    provider_adapter: Any,
) -> None:
    real_mode = bool(scenario_adapter and tool_gateway and provider_adapter)
    mode_label = "REAL" if real_mode else "STUB"
    logger.info("Run %s starting in %s mode", run_id, mode_label)

    run = await run_repo.get(run_id)
    scenario_version = run.scenario_version if run else 1

    context = RunContext.for_run(
        run_id=run_id,
        scenario_id=scenario_id,
        scenario_version=scenario_version,
        allowed_resources=["api"],
        allowed_buckets=[],
    )

    async def advance(status: RunStatus, event_type: RunEventType, message: str, payload: dict | None = None):
        await run_repo.update_status(run_id, status)
        await _publish(run_id, event_repo, event_type, status, message, payload)

    if real_mode:
        await _real_lifecycle(
            run_id, context, run_repo, event_repo, advance,
            scenario_adapter, tool_gateway, provider_adapter,
        )
    else:
        await _stub_lifecycle(run_id, context, run_repo, event_repo, advance, scenario_id)


async def _real_lifecycle(
    run_id: str,
    context: RunContext,
    run_repo: RunRepository,
    event_repo: EventRepository,
    advance,
    scenario_adapter: Any,
    tool_gateway: Any,
    provider_adapter: Any,
) -> None:
    """Full real pipeline with live k8s and OpenAI."""

    # ── PREPARING ───────────────────────────────────────────────────────────
    await advance(RunStatus.PREPARING, RunEventType.STAGE_CHANGED, "Preparing lab environment")
    prepare_result = await asyncio.wait_for(
        scenario_adapter.prepare(context), timeout=90
    )
    if not prepare_result.success:
        await run_repo.update_status(run_id, RunStatus.FAILED, error_code="PREPARE_FAILED")
        await _publish(run_id, event_repo, RunEventType.RUN_FAILED, RunStatus.FAILED,
                       f"Prepare failed: {prepare_result.message}")
        return

    # ── BASELINE_READY ───────────────────────────────────────────────────────
    await advance(RunStatus.BASELINE_READY, RunEventType.BASELINE_CONFIRMED,
                  "Healthy baseline confirmed", {"message": prepare_result.message})

    # ── INJECTING ────────────────────────────────────────────────────────────
    await advance(RunStatus.INJECTING, RunEventType.STAGE_CHANGED, "Injecting fault")
    inject_result = await asyncio.wait_for(
        scenario_adapter.inject(context), timeout=30
    )
    if not inject_result.success:
        await run_repo.update_status(run_id, RunStatus.FAILED, error_code="INJECT_FAILED")
        await _publish(run_id, event_repo, RunEventType.RUN_FAILED, RunStatus.FAILED,
                       f"Inject failed: {inject_result.message}")
        return

    # ── FAULT_CONFIRMED ──────────────────────────────────────────────────────
    confirm_result = await asyncio.wait_for(
        scenario_adapter.confirm_fault(context), timeout=60
    )
    if not confirm_result.success:
        await run_repo.update_status(run_id, RunStatus.INVALID_SCENARIO)
        await _publish(run_id, event_repo, RunEventType.RUN_FAILED, RunStatus.INVALID_SCENARIO,
                       f"Fault did not manifest: {confirm_result.message}")
        return

    await advance(RunStatus.FAULT_CONFIRMED, RunEventType.FAULT_CONFIRMED,
                  "Fault confirmed — observable symptoms present",
                  {"message": confirm_result.message})

    # ── INVESTIGATING ────────────────────────────────────────────────────────
    from app.scenarios.registry import ScenarioRegistry
    registry = ScenarioRegistry.load(_SCENARIOS_ROOT)
    public_meta = registry.get_public(context.scenario_id, context.scenario_version)

    incident = IncidentSummary(
        scenario_id=context.scenario_id,
        title=public_meta.title,
        objective=public_meta.objective,
        environment=public_meta.environment,
    )

    await advance(RunStatus.INVESTIGATING, RunEventType.STAGE_CHANGED,
                  f"AI investigator starting ({public_meta.environment})")

    investigation_result = await asyncio.wait_for(
        provider_adapter.investigate(
            incident=incident,
            tools=tool_gateway.available_tools(context),
            tool_gateway=tool_gateway,
            context=context,
            max_tool_calls=int(os.environ.get("TOOL_BUDGET", "12")),
            seed=None,
            temperature=0.0,
        ),
        timeout=int(os.environ.get("INVESTIGATION_TIMEOUT_S", "120")),
    )

    # Emit tool observations
    for tc in investigation_result.tool_calls:
        await _publish(run_id, event_repo, RunEventType.TOOL_OBSERVATION,
                       RunStatus.INVESTIGATING,
                       f"Tool: {tc.tool_name}",
                       {"tool": tc.tool_name,
                        "evidence_ids": [tc.evidence_id] if tc.evidence_id else [],
                        "error": tc.error})

    # ── DIAGNOSED ────────────────────────────────────────────────────────────
    diagnosis = investigation_result.diagnosis
    await advance(RunStatus.DIAGNOSED, RunEventType.DIAGNOSIS_COMPLETED,
                  "AI diagnosis complete",
                  {"diagnosis": diagnosis.model_dump()})

    # Score the diagnosis (no lab needed)
    from app.evaluation.evaluator import DeterministicEvaluator
    from app.persistence.diagnosis_repository import DiagnosisRepository
    from app.persistence.evaluation_repository import EvaluationRepository

    await DiagnosisRepository().save(run_id, diagnosis)
    evaluator = DeterministicEvaluator()
    eval_result = await evaluator.score_diagnosis(
        run_id=run_id,
        scenario_id=context.scenario_id,
        scenario_version=context.scenario_version,
        diagnosis=diagnosis,
        tool_call_count=len(investigation_result.tool_calls),
        investigation_ms=investigation_result.total_duration_ms,
    )
    await EvaluationRepository().save(eval_result)
    logger.info("Diagnosis scored: total=%s/%s",
                eval_result.total_scored, eval_result.total_max_scored)

    # ── AWAITING_VERIFICATION ────────────────────────────────────────────────
    await advance(RunStatus.AWAITING_VERIFICATION, RunEventType.STAGE_CHANGED,
                  "Awaiting user approval to verify fix in sandbox")


async def _stub_lifecycle(
    run_id: str,
    context: RunContext,
    run_repo: RunRepository,
    event_repo: EventRepository,
    advance,
    scenario_id: str,
) -> None:
    """Stub pipeline — no real k8s or OpenAI. Clearly labelled."""

    await advance(RunStatus.PREPARING, RunEventType.STAGE_CHANGED,
                  "[STUB] Preparing lab environment")
    await asyncio.sleep(0.8)

    await advance(RunStatus.BASELINE_READY, RunEventType.BASELINE_CONFIRMED,
                  "[STUB] Healthy baseline confirmed",
                  {"checks_passed": ["k8s.deployment_available", "k8s.service_has_ready_endpoint"],
                   "stub": True})
    await asyncio.sleep(0.5)

    await advance(RunStatus.INJECTING, RunEventType.STAGE_CHANGED,
                  "[STUB] Injecting fault")
    await asyncio.sleep(0.5)

    await advance(RunStatus.FAULT_CONFIRMED, RunEventType.FAULT_CONFIRMED,
                  "[STUB] Fault confirmed: pods are not ready",
                  {"checks_passed": ["k8s.pod_not_ready"], "stub": True})
    await asyncio.sleep(0.3)

    await advance(RunStatus.INVESTIGATING, RunEventType.STAGE_CHANGED,
                  "[STUB] AI investigator running (no OPENAI_API_KEY configured)")
    await asyncio.sleep(1.0)

    stub_tools = [
        ("k8s.list_pods", "ev_stub_0001", "[STUB] 1 pod, Ready=False, restarts=0"),
        ("k8s.get_deployment", "ev_stub_0002", "[STUB] readinessProbe.path=/health"),
        ("k8s.list_events", "ev_stub_0003", "[STUB] Readiness probe failed"),
    ]
    for tool, ev_id, msg in stub_tools:
        await _publish(run_id, event_repo, RunEventType.TOOL_OBSERVATION,
                       RunStatus.INVESTIGATING, msg,
                       {"tool": tool, "evidence_ids": [ev_id], "stub": True})
        await asyncio.sleep(0.6)

    mock_diagnosis = {
        "schema_version": "1.0",
        "root_cause_id": "K8S_READINESS_PATH_MISMATCH",
        "summary": "[STUB] Readiness probe path /health is wrong; correct path is /healthz.",
        "confidence": 0.92,
        "evidence_ids": ["ev_stub_0001", "ev_stub_0002", "ev_stub_0003"],
        "remediation": {
            "action_type": "k8s.set_readiness_path",
            "target_ref": "api",
            "parameters": {"path": "/healthz"},
        },
        "limitations": "STUB mode — set OPENAI_API_KEY and start kind cluster for real diagnosis",
    }
    await advance(RunStatus.DIAGNOSED, RunEventType.DIAGNOSIS_COMPLETED,
                  "[STUB] Diagnosis complete", {"diagnosis": mock_diagnosis, "stub": True})

    await advance(RunStatus.AWAITING_VERIFICATION, RunEventType.STAGE_CHANGED,
                  "Awaiting user approval to verify fix in sandbox")


# ---------------------------------------------------------------------------
# Verification implementations
# ---------------------------------------------------------------------------

async def _real_verification(
    run_id: str,
    run_repo: RunRepository,
    event_repo: EventRepository,
    scenario_adapter: Any,
    diagnosis_dict: dict,
) -> None:
    from contracts.types import ApprovedRemediation
    remediation_data = diagnosis_dict.get("remediation")
    if not remediation_data:
        await run_repo.update_status(run_id, RunStatus.COMPLETED)
        await _publish(run_id, event_repo, RunEventType.EVALUATION_COMPLETED,
                       RunStatus.COMPLETED,
                       "No remediation proposed — diagnostic evaluation only",
                       {"verification_status": "not_evaluated"})
        return

    remediation = ApprovedRemediation(
        action_type=remediation_data.get("action_type", ""),
        target_ref=remediation_data.get("target_ref", ""),
        parameters=remediation_data.get("parameters", {}),
    )

    # We need a RunContext — rebuild from run record
    run = await run_repo.get(run_id)
    context = RunContext.for_run(
        run_id=run_id,
        scenario_id=run.scenario_id,
        scenario_version=run.scenario_version,
        allowed_resources=["api"],
    )

    # Apply remediation
    fix_result = await asyncio.wait_for(
        scenario_adapter.apply_remediation(context, remediation), timeout=30
    )
    if not fix_result.success:
        await run_repo.update_status(run_id, RunStatus.COMPLETED)
        await _publish(run_id, event_repo, RunEventType.EVALUATION_COMPLETED,
                       RunStatus.COMPLETED,
                       f"Remediation failed: {fix_result.message}",
                       {"verification_status": "failed", "reason": fix_result.message})
        return

    # Verify postconditions
    verify_result = await asyncio.wait_for(
        scenario_adapter.verify(context), timeout=90
    )
    status = "passed" if verify_result.success else "failed"

    # Update verification status on existing evaluation
    from app.persistence.evaluation_repository import EvaluationRepository
    eval_repo = EvaluationRepository()
    existing_eval = await eval_repo.get(run_id)
    if existing_eval:
        from app.evaluation.evaluator import DeterministicEvaluator
        updated = DeterministicEvaluator().update_verification_status(existing_eval, verify_result.success)
        await eval_repo.save(updated)

    await run_repo.update_status(run_id, RunStatus.COMPLETED)
    await _publish(run_id, event_repo, RunEventType.EVALUATION_COMPLETED,
                   RunStatus.COMPLETED,
                   f"Verification {status}: {verify_result.message}",
                   {"verification_status": status})


async def _stub_verification(
    run_id: str,
    run_repo: RunRepository,
    event_repo: EventRepository,
) -> None:
    await asyncio.sleep(1.5)
    eval_result = {
        "root_cause_accuracy": {"score": 40, "max": 40, "status": "scored"},
        "evidence_quality": {"score": 20, "max": 25, "status": "scored"},
        "remediation": {"score": 23, "max": 25, "status": "scored"},
        "efficiency": {"score": 9, "max": 10, "status": "scored"},
        "verification_status": "passed",
        "stub": True,
    }
    await run_repo.update_status(run_id, RunStatus.COMPLETED)
    await _publish(run_id, event_repo, RunEventType.EVALUATION_COMPLETED,
                   RunStatus.COMPLETED,
                   "[STUB] Verification passed — fix confirmed healthy",
                   {"evaluation": eval_result})
