"""
IncidentForge — Canonical Domain Types
=======================================
This file is the single source of truth for all shared data structures.
No agent may define these independently. Contract changes require a PR
against this file reviewed by the tech lead (Agent A).

Version: 1.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------

class RunStatus(str, Enum):  # noqa: UP042
    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    BASELINE_READY = "BASELINE_READY"
    INJECTING = "INJECTING"
    FAULT_CONFIRMED = "FAULT_CONFIRMED"
    INVESTIGATING = "INVESTIGATING"
    DIAGNOSED = "DIAGNOSED"
    AWAITING_VERIFICATION = "AWAITING_VERIFICATION"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    # Terminal failure states
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"      # process crash recovery
    UNSUPPORTED = "UNSUPPORTED"      # environment / emulator limitation
    INVALID_SCENARIO = "INVALID_SCENARIO"  # fault did not manifest

TERMINAL_STATUSES: frozenset[RunStatus] = frozenset({
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.TIMED_OUT,
    RunStatus.CANCELLED,
    RunStatus.INTERRUPTED,
    RunStatus.UNSUPPORTED,
    RunStatus.INVALID_SCENARIO,
})

# Valid forward transitions (no other transitions are allowed)
ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.PREPARING, RunStatus.CANCELLED}),
    RunStatus.PREPARING: frozenset({
        RunStatus.BASELINE_READY, RunStatus.FAILED,
        RunStatus.TIMED_OUT, RunStatus.CANCELLED,
    }),
    RunStatus.BASELINE_READY: frozenset({
        RunStatus.INJECTING, RunStatus.FAILED,
        RunStatus.TIMED_OUT, RunStatus.CANCELLED,
    }),
    RunStatus.INJECTING: frozenset({
        RunStatus.FAULT_CONFIRMED, RunStatus.INVALID_SCENARIO,
        RunStatus.FAILED, RunStatus.TIMED_OUT, RunStatus.CANCELLED,
    }),
    RunStatus.FAULT_CONFIRMED: frozenset({
        RunStatus.INVESTIGATING, RunStatus.CANCELLED,
    }),
    RunStatus.INVESTIGATING: frozenset({
        RunStatus.DIAGNOSED, RunStatus.FAILED,
        RunStatus.TIMED_OUT, RunStatus.CANCELLED,
    }),
    RunStatus.DIAGNOSED: frozenset({
        RunStatus.AWAITING_VERIFICATION, RunStatus.COMPLETED,
    }),
    RunStatus.AWAITING_VERIFICATION: frozenset({
        RunStatus.VERIFYING, RunStatus.CANCELLED,
    }),
    RunStatus.VERIFYING: frozenset({
        RunStatus.COMPLETED, RunStatus.FAILED,
        RunStatus.TIMED_OUT, RunStatus.CANCELLED,
    }),
}
# Terminal states have no outbound transitions.


# ---------------------------------------------------------------------------
# Run context — passed to all ScenarioAdapter methods
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunContext:
    """
    Immutable context for a single lab run.
    Passed to ScenarioAdapter, ToolGateway, and Evaluator.
    Never passed to the AI Investigator directly.
    """
    run_id: str               # Full ULID, e.g. "run_01JABCDEFG..."
    run_short_id: str         # First 8 chars, used for k8s naming
    scenario_id: str
    scenario_version: int
    namespace: str            # "if-{run_short_id}" for k8s scenarios
    allowed_resources: list[str]  # resource names the tool gateway may query
    allowed_buckets: list[str]    # S3 bucket names allowed for this run

    @classmethod
    def for_run(
        cls,
        run_id: str,
        scenario_id: str,
        scenario_version: int,
        allowed_resources: list[str] | None = None,
        allowed_buckets: list[str] | None = None,
    ) -> RunContext:
        short = run_id.replace("run_", "")[:8].lower()
        return cls(
            run_id=run_id,
            run_short_id=short,
            scenario_id=scenario_id,
            scenario_version=scenario_version,
            namespace=f"if-{short}",
            allowed_resources=allowed_resources or [],
            allowed_buckets=allowed_buckets or [],
        )


# ---------------------------------------------------------------------------
# Check system — ScenarioAdapter return type and declarative check spec
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CheckResult:
    """
    Result of any idempotent check predicate.
    Returned by ScenarioAdapter.prepare(), inject(), confirm_fault(),
    apply_remediation(), verify().
    """
    success: bool
    code: str         # machine-readable, e.g. "DEPLOYMENT_AVAILABLE"
    message: str      # human-readable, logged in run events
    details: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def ok(code: str, message: str, **details: Any) -> CheckResult:
        return CheckResult(success=True, code=code, message=message, details=dict(details))

    @staticmethod
    def fail(code: str, message: str, **details: Any) -> CheckResult:
        return CheckResult(success=False, code=code, message=message, details=dict(details))


class CheckSpec(BaseModel):
    """
    Declarative check predicate from scenario YAML.
    Example: {type: "k8s.deployment_available", ref: "api"}
    """
    type: str               # e.g. "k8s.deployment_available"
    ref: str                # resource reference within the scenario
    parameters: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

class EvidenceItem(BaseModel):
    """
    A single piece of evidence produced by one tool call.
    Persisted to the evidence table; evidence_id is cited in Diagnosis.
    """
    evidence_id: str           # format: ev_{run_short_id}_{seq:04d}
    run_id: str
    tool_name: str             # e.g. "k8s.list_pods"
    captured_at: datetime
    payload: dict[str, Any]    # sanitized, bounded tool output
    sha256: str                # SHA-256 of JSON-serialized payload (canonical)
    truncated: bool = False    # payload was truncated to fit size limit

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Tool definitions — provided to the AI investigator
# ---------------------------------------------------------------------------

class ToolDefinition(BaseModel):
    """
    Defines a tool the AI investigator may call.
    Presented to the LLM as part of the investigation context.
    """
    name: str               # e.g. "k8s.list_pods"
    description: str        # natural language description for the model
    parameters_schema: dict[str, Any]  # JSON Schema for arguments


class ToolCallRecord(BaseModel):
    """
    Records one tool call made during investigation (for audit/benchmarking).
    """
    sequence: int
    tool_name: str
    arguments: dict[str, Any]
    evidence_id: str | None   # None if call failed
    error: str | None = None
    duration_ms: int


# ---------------------------------------------------------------------------
# Incident summary — the ONLY scenario information passed to the investigator
# ---------------------------------------------------------------------------

class IncidentSummary(BaseModel):
    """
    Public-only scenario information given to the AI investigator.
    Must never include setup, fault, fixture, or private_expected fields.
    """
    scenario_id: str
    title: str
    objective: str            # from scenario YAML public.summary
    environment: str          # "kubernetes" or "aws"


# ---------------------------------------------------------------------------
# Remediation
# ---------------------------------------------------------------------------

class ApprovedRemediation(BaseModel):
    """
    A typed remediation action proposed by the AI investigator.
    Only action_types in the scenario's private_expected.allowed_remediations
    are accepted. All others are marked POLICY_DENIED.
    """
    action_type: str              # e.g. "k8s.set_readiness_path"
    target_ref: str               # resource reference
    parameters: dict[str, Any]    # action-specific parameters

    @field_validator("action_type")
    @classmethod
    def action_type_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("action_type must not be empty")
        return v


# ---------------------------------------------------------------------------
# Diagnosis — structured output from the AI investigator
# ---------------------------------------------------------------------------

class AlternativeHypothesis(BaseModel):
    root_cause_id: str
    why_less_likely: str


class Diagnosis(BaseModel):
    """
    Final structured output from the AI investigator.
    Schema version must match the evaluator's supported versions.
    Unknown root_cause_ids, nonexistent evidence_ids, unapproved actions,
    and invalid target_refs make this INVALID — not silently repaired.
    """
    schema_version: str = "1.0"
    root_cause_id: str              # "UNKNOWN" is valid when evidence is insufficient
    summary: str
    confidence: float               # model's self-reported [0.0, 1.0]; NOT calibrated
    evidence_ids: list[str]         # must reference real run-scoped evidence IDs
    alternatives: list[AlternativeHypothesis] = Field(default_factory=list)
    remediation: ApprovedRemediation | None = None
    limitations: str | None = None

    @field_validator("confidence")
    @classmethod
    def confidence_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be in [0.0, 1.0]")
        return v


# ---------------------------------------------------------------------------
# Evaluation scoring
# ---------------------------------------------------------------------------

class ComponentScoreStatus(str, Enum):  # noqa: UP042
    SCORED = "scored"
    NOT_EVALUATED = "not_evaluated"   # verification not requested
    EXCLUDED = "excluded"             # environment failure or invalid scenario


class ComponentScore(BaseModel):
    """
    Score for one rubric dimension.
    Use status=NOT_EVALUATED instead of score=0 when a dimension was not tested.
    Never compute a total from NOT_EVALUATED or EXCLUDED dimensions.
    """
    dimension: str                # "root_cause", "evidence", "remediation", "efficiency"
    score: int
    max: int
    status: ComponentScoreStatus
    reason: str | None = None


class EvaluationResult(BaseModel):
    """
    Full evaluation outcome for one run.
    Stored in the evaluations table.
    """
    run_id: str
    rubric_version: str = "1.0"
    diagnosis_valid: bool
    validation_errors: list[str] = Field(default_factory=list)
    component_scores: list[ComponentScore]
    verification_status: str | None = None   # "passed", "failed", "not_evaluated"
    completed_at: datetime | None = None

    @property
    def total_scored(self) -> int | None:
        """Sum of scored dimensions only. None if no dimensions were scored."""
        scored = [c for c in self.component_scores if c.status == ComponentScoreStatus.SCORED]
        return sum(c.score for c in scored) if scored else None

    @property
    def total_max_scored(self) -> int | None:
        scored = [c for c in self.component_scores if c.status == ComponentScoreStatus.SCORED]
        return sum(c.max for c in scored) if scored else None


# ---------------------------------------------------------------------------
# Run events (SSE)
# ---------------------------------------------------------------------------

class RunEventType(str, Enum):  # noqa: UP042
    RUN_CREATED = "run_created"
    STAGE_CHANGED = "stage_changed"
    BASELINE_CONFIRMED = "baseline_confirmed"
    FAULT_CONFIRMED = "fault_confirmed"
    TOOL_OBSERVATION = "tool_observation"
    DIAGNOSIS_COMPLETED = "diagnosis_completed"
    EVALUATION_COMPLETED = "evaluation_completed"
    WARNING = "warning"
    RUN_FAILED = "run_failed"


class RunEvent(BaseModel):
    """
    One SSE event in a run's event stream.
    seq is monotonically increasing per run; used as SSE event ID for reconnect.
    """
    id: int                     # DB auto-increment (global across all runs)
    run_id: str
    seq: int                    # monotonic per run; used as SSE Last-Event-ID
    at: datetime
    type: RunEventType
    stage: RunStatus
    message: str                # human-readable
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Investigation result (returned by ProviderAdapter)
# ---------------------------------------------------------------------------

class InvestigationResult(BaseModel):
    """
    Full output of one investigation session.
    Returned by ProviderAdapter.investigate() and stored before evaluation.
    """
    run_id: str
    tool_calls: list[ToolCallRecord]
    diagnosis: Diagnosis
    total_duration_ms: int
    provider: str
    model: str


# ---------------------------------------------------------------------------
# Provider registry (GET /providers)
# ---------------------------------------------------------------------------

class CleanupPolicy(str, Enum):  # noqa: UP042
    LAZY = "lazy"           # keep namespace until next run's prepare() cleans it
    IMMEDIATE = "immediate" # delete namespace on run terminal state
    MANUAL = "manual"       # never auto-delete; requires make clean


class ProviderInfo(BaseModel):
    """
    Public metadata about one configured LLM provider.
    Returned by GET /providers. Never includes API keys.
    """
    provider_id: str         # e.g. "openai", "anthropic", "ollama"
    display_name: str        # e.g. "OpenAI"
    models: list[str]        # e.g. ["gpt-4o", "gpt-4o-mini"]
    available: bool          # False if API key not configured
