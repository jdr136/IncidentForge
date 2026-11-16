"""
DeterministicEvaluator — implements the Evaluator Protocol.

Scores AI diagnoses against hidden ground truth WITHOUT calling the lab.
Verification (applying the fix and checking postconditions) is handled
separately in the orchestrator.

This is a TRUSTED component. It calls ScenarioRegistry.get_private() and
EvidenceStore.validate_ids(). Never passes private data to the AI.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..'))
from app.persistence.evidence_repository import EvidenceRepository
from app.scenarios.registry import ScenarioRegistry
from contracts.types import (
    ComponentScore,
    ComponentScoreStatus,
    Diagnosis,
    EvaluationResult,
)

logger = logging.getLogger(__name__)

RUBRIC_VERSION = "1.0"
_SCENARIOS_ROOT = Path(__file__).resolve().parents[4] / "scenarios"

# Score weights
WEIGHT_ROOT_CAUSE = 40
WEIGHT_EVIDENCE = 25
WEIGHT_REMEDIATION = 25
WEIGHT_EFFICIENCY = 10
MAX_TOOL_BUDGET = 12
MAX_INVESTIGATION_S = 120


class DeterministicEvaluator:
    """
    Implements the Evaluator Protocol.
    All scoring is deterministic — no LLM calls.
    """

    def __init__(self) -> None:
        self._evidence_repo = EvidenceRepository()
        self._registry = ScenarioRegistry.load(_SCENARIOS_ROOT)

    async def score_diagnosis(
        self,
        run_id: str,
        scenario_id: str,
        scenario_version: int,
        diagnosis: Diagnosis,
        tool_call_count: int = 0,
        investigation_ms: int = 0,
    ) -> EvaluationResult:
        """
        Score root-cause, evidence, and remediation (structural only).
        Does NOT touch the lab environment.
        """
        private = self._registry.get_private(scenario_id, scenario_version)
        expected = private.private_expected

        scores: list[ComponentScore] = []
        validation_errors: list[str] = []

        # ── Root-cause accuracy (40 pts) ─────────────────────────────────────
        rc_id = diagnosis.root_cause_id
        if rc_id == "UNKNOWN":
            scores.append(ComponentScore(
                dimension="root_cause", score=0, max=WEIGHT_ROOT_CAUSE,
                status=ComponentScoreStatus.SCORED,
                reason="UNKNOWN root_cause_id — insufficient evidence",
            ))
        elif rc_id in expected.allowed_root_cause_ids:
            scores.append(ComponentScore(
                dimension="root_cause", score=WEIGHT_ROOT_CAUSE, max=WEIGHT_ROOT_CAUSE,
                status=ComponentScoreStatus.SCORED,
                reason=f"Correct: '{rc_id}' is in allowed_root_cause_ids",
            ))
        else:
            scores.append(ComponentScore(
                dimension="root_cause", score=0, max=WEIGHT_ROOT_CAUSE,
                status=ComponentScoreStatus.SCORED,
                reason=f"Incorrect: '{rc_id}' not in {expected.allowed_root_cause_ids}",
            ))
            validation_errors.append(f"Unknown root_cause_id: '{rc_id}'")

        # ── Evidence quality (25 pts) ─────────────────────────────────────────
        # 10 pts: all cited IDs are real (no fabrication)
        # 15 pts: required_evidence_types covered
        fabricated = await self._evidence_repo.validate_ids(run_id, diagnosis.evidence_ids)
        evidence_valid = not fabricated

        id_score = 0 if fabricated else 10
        if fabricated:
            validation_errors.append(f"Fabricated evidence IDs: {fabricated}")

        # Check required evidence types are covered
        all_evidence = await self._evidence_repo.list_by_run(run_id)
        cited_tools = {
            ev.tool_name for ev in all_evidence
            if ev.evidence_id in diagnosis.evidence_ids
        }
        required = set(expected.required_evidence_types)
        covered = required & cited_tools
        type_score = round(15 * (len(covered) / len(required))) if required else 15

        scores.append(ComponentScore(
            dimension="evidence", score=id_score + type_score, max=WEIGHT_EVIDENCE,
            status=ComponentScoreStatus.SCORED,
            reason=(
                f"ID validity: {'OK' if evidence_valid else f'fabricated {fabricated}'}; "
                f"type coverage: {len(covered)}/{len(required)}"
            ),
        ))

        # ── Remediation correctness (25 pts, structural only) ────────────────
        if diagnosis.remediation is None:
            scores.append(ComponentScore(
                dimension="remediation", score=0, max=WEIGHT_REMEDIATION,
                status=ComponentScoreStatus.NOT_EVALUATED,
                reason="No remediation proposed",
            ))
        else:
            rem = diagnosis.remediation
            action_ok = rem.action_type in expected.allowed_remediations
            ref_ok = bool(rem.target_ref)
            params_ok = bool(rem.parameters)

            action_score = 10 if action_ok else 0
            ref_score = 5 if ref_ok else 0
            params_score = 10 if params_ok else 0

            if not action_ok:
                validation_errors.append(
                    f"action_type '{rem.action_type}' not in allowed_remediations"
                )

            scores.append(ComponentScore(
                dimension="remediation", score=action_score + ref_score + params_score,
                max=WEIGHT_REMEDIATION, status=ComponentScoreStatus.SCORED,
                reason=(
                    f"action={'OK' if action_ok else 'WRONG'}; "
                    f"ref={'OK' if ref_ok else 'MISSING'}; "
                    f"params={'OK' if params_ok else 'EMPTY'}"
                ),
            ))

        # ── Investigation efficiency (10 pts) ────────────────────────────────
        budget_ok = tool_call_count <= MAX_TOOL_BUDGET
        time_ok = (investigation_ms / 1000) <= MAX_INVESTIGATION_S
        eff_score = WEIGHT_EFFICIENCY if (budget_ok and time_ok) else round(
            WEIGHT_EFFICIENCY * min(
                MAX_TOOL_BUDGET / max(tool_call_count, 1),
                MAX_INVESTIGATION_S / max(investigation_ms / 1000, 1),
            )
        )
        scores.append(ComponentScore(
            dimension="efficiency", score=eff_score, max=WEIGHT_EFFICIENCY,
            status=ComponentScoreStatus.SCORED,
            reason=f"tools={tool_call_count}/{MAX_TOOL_BUDGET}; time={investigation_ms}ms",
        ))

        return EvaluationResult(
            run_id=run_id,
            rubric_version=RUBRIC_VERSION,
            diagnosis_valid=not validation_errors,
            validation_errors=validation_errors,
            component_scores=scores,
            verification_status=None,
            completed_at=datetime.now(UTC),
        )

    def update_verification_status(
        self,
        result: EvaluationResult,
        passed: bool,
    ) -> EvaluationResult:
        """Return updated result with verification_status set."""
        return EvaluationResult(
            run_id=result.run_id,
            rubric_version=result.rubric_version,
            diagnosis_valid=result.diagnosis_valid,
            validation_errors=result.validation_errors,
            component_scores=result.component_scores,
            verification_status="passed" if passed else "failed",
            completed_at=datetime.now(UTC),
        )
