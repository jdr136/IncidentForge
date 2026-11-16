"""
IncidentForge — Shared Protocols (Interfaces)
=============================================
These Protocols define the boundaries between parallel development streams.
Every agent implements against these; the integrator wires them together.

No agent may change a Protocol signature without a tech-lead PR.
Stubs and mocks must implement the exact signatures below.

Version: 1.0
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from contracts.types import (
    ApprovedRemediation,
    CheckResult,
    CheckSpec,
    Diagnosis,
    EvidenceItem,
    IncidentSummary,
    InvestigationResult,
    RunContext,
    ToolDefinition,
)

# ---------------------------------------------------------------------------
# Scenario system
# ---------------------------------------------------------------------------

@runtime_checkable
class ScenarioAdapter(Protocol):
    """
    Trusted scenario implementation for one environment type.
    One adapter per environment (KubernetesAdapter, AWSAdapter), not per scenario.

    All methods are:
    - Bounded (timeout handled by the caller via asyncio.wait_for)
    - Idempotent where marked
    - Must NOT receive or return private_expected fields
    - Must log structured outcomes via the standard logger

    Implementations are trusted code; they may hold controller credentials.
    They must never pass those credentials to the ToolGateway or Investigator.
    """

    async def prepare(self, context: RunContext) -> CheckResult:
        """
        Apply the scenario fixture (healthy baseline) and verify it.
        Idempotent: calling twice leaves the same healthy state.
        Returns CheckResult.success=True only when all baseline_checks pass.
        On partial success, clean up and return failure.
        """
        ...

    async def inject(self, context: RunContext) -> CheckResult:
        """
        Apply the declared fault to the baseline environment.
        Does NOT verify that the fault manifested — that is confirm_fault().
        """
        ...

    async def confirm_fault(self, context: RunContext) -> CheckResult:
        """
        Verify that the injected fault produced the expected observable symptom.
        If this returns success=False, the run transitions to INVALID_SCENARIO,
        not an agent failure.
        """
        ...

    async def apply_remediation(
        self, context: RunContext, action: ApprovedRemediation
    ) -> CheckResult:
        """
        Apply a typed approved remediation action to the lab environment.
        Must reject action_types not in the scenario's allowed_remediations.
        Does NOT verify postconditions — that is verify().
        Only called in state VERIFYING after explicit user approval.
        """
        ...

    async def verify(self, context: RunContext) -> CheckResult:
        """
        Run all post_fix_checks from private_expected.
        Returns success=True only when ALL postconditions pass.
        A repair that partially restores service must still return False.
        """
        ...

    async def cleanup(self, context: RunContext) -> None:
        """
        Remove all scenario-owned resources.
        Idempotent: safe to call when no resources exist.
        Called in a finally block; must not raise unhandled exceptions.
        Verifies ownership via labels before deleting anything.
        """
        ...


@runtime_checkable
class CheckRunner(Protocol):
    """
    Executes declarative check predicates from scenario YAML.
    Maps check type strings to Python check functions.
    Used by ScenarioAdapter implementations to avoid per-scenario Python code
    for checks that can be expressed declaratively.
    """

    async def run(self, check: CheckSpec, context: RunContext) -> CheckResult:
        """
        Execute a single check predicate.
        Never raises; wraps exceptions into CheckResult.fail().
        """
        ...


# ---------------------------------------------------------------------------
# Tool gateway and evidence
# ---------------------------------------------------------------------------

@runtime_checkable
class ToolGateway(Protocol):
    """
    Executes read-only tool calls on behalf of the AI investigator.

    Responsibilities:
    1. Validate tool_name against the allowed tool list
    2. Validate arguments against the tool's JSON Schema
    3. Authorize the call against the run's allowed_resources / allowed_buckets
    4. Execute the underlying k8s/AWS query
    5. Sanitize and truncate the response
    6. Assign evidence_id, compute sha256, persist to EvidenceStore
    7. Return the EvidenceItem (including evidence_id) to the caller

    The investigator receives evidence_ids so it can cite them in Diagnosis.
    Tool output must be treated as untrusted data, not instructions.
    """

    async def call(
        self,
        context: RunContext,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> EvidenceItem:
        """
        Execute one tool call.
        Raises ToolPolicyDenied if tool_name or arguments are not permitted.
        Raises ToolExecutionError on infrastructure failure.
        Never raises for environment-level errors (e.g. 404 from k8s) —
        those are valid evidence with error payloads.
        """
        ...

    def available_tools(self, context: RunContext) -> list[ToolDefinition]:
        """
        Return the list of tools available for the given run context.
        Tools are filtered by the scenario's environment type.
        """
        ...


@runtime_checkable
class EvidenceStore(Protocol):
    """
    Persists and retrieves evidence items for a run.
    The ToolGateway uses this after each successful (or failed) tool call.
    The Evaluator uses this to validate cited evidence IDs.
    """

    async def save(self, item: EvidenceItem) -> None:
        """
        Persist an evidence item.
        Raises if evidence_id already exists (duplicate tool call handling
        is the caller's responsibility).
        """
        ...

    async def list_by_run(self, run_id: str) -> list[EvidenceItem]:
        """
        Return all evidence items for a run, ordered by captured_at.
        Returns empty list if run has no evidence (not an error).
        """
        ...

    async def validate_ids(
        self, run_id: str, claimed_ids: list[str]
    ) -> list[str]:
        """
        Return the subset of claimed_ids that do NOT exist in the run's evidence.
        Empty return = all IDs are valid.
        Used by the Evaluator to detect fabricated evidence citations.
        """
        ...

    async def next_seq(self, run_id: str) -> int:
        """
        Return the next sequence number for a new evidence item in this run.
        Used by ToolGateway to generate evidence_id.
        Implementation must be atomic (no duplicate seq values per run).
        """
        ...


# ---------------------------------------------------------------------------
# Provider adapter (LLM)
# ---------------------------------------------------------------------------

@runtime_checkable
class ProviderAdapter(Protocol):
    """
    Abstracts one LLM provider (OpenAI-compatible, Anthropic, Ollama, etc.)

    The provider adapter:
    - Hides provider-specific request/response formats from domain code
    - Enforces the tool call budget (max_tool_calls)
    - Calls ToolGateway for each tool the model requests
    - Collects ToolCallRecords for audit
    - Parses and validates the final Diagnosis JSON
    - Never passes RunContext, ground truth, or admin credentials to the model

    The investigation loop:
    1. Build system + user prompt from IncidentSummary
    2. Present tools as ToolDefinitions in provider-native format
    3. Loop: model requests tool call → ToolGateway.call() → return EvidenceItem to model
    4. Loop terminates when: model emits final answer, budget exhausted, or timeout
    5. Parse structured Diagnosis from final model output
    6. Return InvestigationResult
    """

    async def investigate(
        self,
        incident: IncidentSummary,
        tools: list[ToolDefinition],
        tool_gateway: ToolGateway,
        context: RunContext,
        max_tool_calls: int = 12,
        seed: int | None = None,
        temperature: float = 0.0,
    ) -> InvestigationResult:
        """
        Run one full investigation session.
        max_tool_calls is a hard cap; the model is informed of its remaining budget.
        On budget exhaustion, force a final diagnosis with what was gathered.
        Never reveals context.namespace labels or scenario internal names to the model.
        """
        ...


# ---------------------------------------------------------------------------
# Run lock (single active run enforcement)
# ---------------------------------------------------------------------------

@runtime_checkable
class RunLock(Protocol):
    """
    Enforces the invariant that at most one lab run is active at a time.

    Implementation: DB-backed. The lock holder is the run_id of the active run.
    An in-memory asyncio.Lock prevents concurrent requests within one process.
    The DB state survives crashes and allows startup recovery.

    LAB_BUSY: any run with a non-terminal status is considered the lock holder.
    """

    async def acquire(self, run_id: str) -> bool:
        """
        Attempt to acquire the lab lock for run_id.
        Returns True if acquired, False if another non-terminal run exists (LAB_BUSY).
        Atomically marks run_id as the active run in persistent storage.
        """
        ...

    async def release(self, run_id: str) -> None:
        """
        Release the lock held by run_id.
        Idempotent: safe to call if run_id is not the current holder.
        Does NOT change the run's status — the caller handles status transitions.
        """
        ...

    async def recover_interrupted(self) -> list[str]:
        """
        Called at process startup.
        Finds all runs with non-terminal status and transitions them to INTERRUPTED.
        Returns the list of run IDs that were recovered.
        After this call, acquire() will succeed for new runs.
        """
        ...


# ---------------------------------------------------------------------------
# Event bus (SSE fan-out)
# ---------------------------------------------------------------------------

@runtime_checkable
class RunEventBus(Protocol):
    """
    Fan-out mechanism: investigation task publishes events; SSE handlers subscribe.

    Design: in-process asyncio.Queue per subscriber, durable replay from DB.

    On new SSE connection:
    1. Query DB for all events with seq > Last-Event-ID (or 0 if no header)
    2. Send historical events to the new subscriber immediately
    3. Add subscriber to live queue for future events
    4. On run terminal state, send final event and close the subscriber queue

    The investigation task calls publish() for every RunEvent it creates.
    The event is persisted to DB first, then broadcast to live subscribers.
    """

    async def publish(self, event: RunEvent) -> None:  # noqa: F821
        """
        Persist event to DB (assigns event.id) then broadcast to live subscribers.
        """
        ...

    async def subscribe(self, run_id: str) -> AsyncIterator[RunEvent]:  # noqa: F821
        """
        Return an async iterator that yields RunEvents for run_id.
        Replays historical events from DB, then yields live events.
        Iterator terminates when the run reaches a terminal state.
        """
        ...


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

@runtime_checkable
class Evaluator(Protocol):
    """
    Scores a Diagnosis against the scenario's private expected results.

    Two evaluation phases:
    1. diagnose() — deterministic scoring without touching the lab
    2. verify() — applies remediation via ScenarioAdapter and runs postconditions

    These are separate because: a correct root cause does not imply a working repair,
    and a repair that happens to work does not prove a correct explanation.
    """

    async def score_diagnosis(
        self,
        context: RunContext,
        diagnosis: Diagnosis,
        evidence_store: EvidenceStore,
    ) -> EvaluationResult:  # noqa: F821
        """
        Score root-cause accuracy, evidence quality, and remediation correctness
        (structural only — not live verification).
        Returns partial scores with NOT_EVALUATED for remediation if no action proposed.
        Never modifies the lab environment.
        """
        ...

    async def verify_remediation(
        self,
        context: RunContext,
        diagnosis: Diagnosis,
        adapter: ScenarioAdapter,
    ) -> EvaluationResult:  # noqa: F821
        """
        Apply the proposed remediation to the lab (via trusted adapter) and
        run postcondition checks. Returns updated EvaluationResult with
        verification_status set.

        Only called after explicit user approval (state AWAITING_VERIFICATION → VERIFYING).
        A bad fix must fail; the verifier must not silently correct the agent's proposal.
        """
        ...


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------

@runtime_checkable
class ScenarioRegistry(Protocol):
    """
    Loads and validates scenario definitions.
    Maintains strict public/private separation.

    Public metadata (title, summary, environment, difficulty) may be served
    over the REST API and to the UI.

    Private data (setup fixtures, fault parameters, private_expected) must
    NEVER be returned from any public method. It is exposed only through
    trusted internal methods used by ScenarioAdapter and Evaluator.
    """

    def list_public(self) -> list[ScenarioPublicMeta]:  # noqa: F821
        """Return public metadata for all loaded scenarios. No hidden fields."""
        ...

    def get_public(self, scenario_id: str, version: int) -> ScenarioPublicMeta:  # noqa: F821
        """Return public metadata for one scenario. Raises ScenarioNotFound."""
        ...

    def get_private(
        self, scenario_id: str, version: int
    ) -> ScenarioPrivate:  # noqa: F821
        """
        Return full private scenario data for trusted callers (adapter, evaluator).
        MUST NOT be called from any public API handler.
        MUST NOT return this data in any HTTP response.
        """
        ...
