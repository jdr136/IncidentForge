# Architecture Review — IncidentForge v1.0 Spec

**Reviewer:** Kovar  
**Spec version:** 1.0 — Proposed  
**Date reviewed:** 12 November 2026  
**Status:** Pre-build design challenge. This document must be resolved before parallel development begins.

---

## 1. What the Spec Gets Right

The design has a strong foundation. These decisions are sound and should not be relitigated:

- **Ground-truth isolation** (§5.1, §7.1) — separating private expected results from the public API is the correct invariant and the right thing to make non-negotiable.
- **Typed remediations via allowlist** (ADR-003) — prevents an LLM from proposing arbitrary shell commands; correct choice.
- **Independent evaluation** (§7.1) — scoring root-cause accuracy separately from remediation outcome prevents a fluent but wrong diagnosis from being marked as a pass.
- **State machine with confirmation gates** (§4.2) — requiring `FAULT_CONFIRMED` before `INVESTIGATING` and an explicit UI approval before `VERIFYING` are both correct.
- **SSE over WebSockets** (§3.3) — appropriate for one-way ordered progression events; reduces frontend complexity.
- **SQLite + WAL** (§3.3) — right for MVP; the repository interface makes it replaceable.
- **Fail-closed AWS safety** (§10.3) — the five-condition guard is the right model; must be implemented exactly.
- **Evidence ID integrity** (§6.3) — requiring cited IDs to be real run-scoped IDs before scoring is the right anti-hallucination control.

---

## 2. Architecture Challenges

These are gaps, contradictions, or unstated assumptions in the spec that will block parallel development or cause integration failures if not resolved now.

---

### 2.1 CRITICAL: Async investigation loop vs SSE is unspecified

**Location:** §4.1 (lifecycle), §8.1 (SSE), §3.3 (FastAPI)

**Problem:** The spec describes a FastAPI server that both runs the investigation loop and streams SSE events to the browser. These cannot happen in the same coroutine. If the investigation loop runs synchronously on the event loop, SSE frames stop arriving.

The spec does not define how the investigation loop is executed or how it emits events to waiting SSE subscribers.

**Assumption being made:** The investigation loop runs as a FastAPI background task (`asyncio.create_task`) and writes `RunEvent` rows to SQLite. The SSE endpoint reads from SQLite and polls/watches for new rows. This is the only feasible single-process model.

**Decision required before backend work starts:**
- Investigation loop runs as `asyncio.create_task` launched from `POST /runs`
- Events are persisted to `events` table as they occur
- SSE endpoint uses a fan-out mechanism: in-process `asyncio.Queue` per subscriber populated by the investigation task, with SQLite as the durable replay source for reconnection
- On reconnect, SSE replays all events with `seq` greater than the client's `Last-Event-ID`, then joins the live queue

This needs to be a contract decision (see §4 below), not discovered during integration.

---

### 2.2 CRITICAL: ScenarioAdapter vs ScenarioRegistry relationship not defined

**Location:** §5.1 (YAML contract), §5.2 (ScenarioAdapter Protocol)

**Problem:** The spec shows a declarative YAML scenario file and a Python `ScenarioAdapter` Protocol, but does not define how they relate:

- Is there one `ScenarioAdapter` per environment type (`KubernetesAdapter`, `AWSAdapter`) that reads any scenario YAML for that environment?
- Or is there one adapter per scenario ID (`ReadinessPathAdapter`, `ServiceSelectorAdapter`)?
- Who registers adapters to scenario IDs? The registry? A factory?

**Problem:** The YAML has `fault.action: k8s.patch_readiness_path`. Who maps this string to Python code? The spec does not define an action registry.

**Decision required:** Adapters should be one per environment type, not per scenario. The environment type in the YAML (`environment: kubernetes` / `environment: aws`) selects the adapter class. The adapter reads the declarative fault/setup/check fields from the loaded scenario and executes them via typed action dispatch. This requires an `ActionDispatcher` within each environment adapter.

---

### 2.3 CRITICAL: Missing Protocol definitions for three key shared interfaces

**Location:** §6.1 (investigator), §6.2 (tool gateway), §9 (persistence)

The spec defines `ScenarioAdapter` as a Protocol, but does not define Protocols for:

1. **`ProviderAdapter`** — the interface the AI investigator uses to call the LLM. The spec says "the provider adapter must hide provider-specific response formats" (§6.1) but gives no protocol definition.
2. **`ToolGateway`** — the interface between the investigator and the read-only tool execution layer. The spec describes this in prose (§6.2) but defines no Python interface.
3. **`EvidenceStore`** — the interface for persisting and retrieving evidence. Evidence IDs are generated here; validation at evaluation time requires a query by run ID.

Without these three Protocols, the agent engineer (D) and evaluation engineer (E) cannot work in parallel against stubs.

**Resolution:** Define all three Protocols in `contracts/interfaces.py` before any parallel stream begins. See §4 of this document.

---

### 2.4 CRITICAL: `RunContext`, `CheckResult`, `ApprovedRemediation` are undefined types

**Location:** §5.2 (ScenarioAdapter Protocol)

The ScenarioAdapter Protocol uses three domain types that are never defined in the spec:

```python
async def prepare(self, context: RunContext) -> CheckResult: ...
async def apply_remediation(self, context: RunContext, action: ApprovedRemediation) -> CheckResult: ...
```

Every agent that touches the lifecycle needs these. Without definitions, they will be invented independently and diverge.

**Resolution:** Define in `contracts/types.py`. See §4.

---

### 2.5 HIGH: Run lock mechanism is unspecified

**Location:** §4.3 ("One lab run at a time"), §8.1 (409 LAB_BUSY)

**Problem:** How is "one active run" enforced across process restarts? An in-memory `asyncio.Lock` is lost on crash. The spec says on startup, in-flight runs are marked `INTERRUPTED` (§4.3), but doesn't say how the new process knows a run was in-flight.

**Decision required:**
- The `runs` table encodes the lock: a run with any non-terminal status is the lock holder
- `POST /runs` queries for any run with a non-terminal status; if found, returns 409
- On startup, any run with a non-terminal status is transitioned to `INTERRUPTED`
- The in-memory `asyncio.Lock` prevents concurrent requests within the same process lifetime

This must be agreed before the backend engineer and lab engineer start, as it affects both the state machine transitions and the `POST /runs` handler.

---

### 2.6 HIGH: Evidence ID lifecycle and generation scheme not specified

**Location:** §6.2, §6.3, §9

**Problem:** The spec shows `evidence_ids: ["ev_0003", "ev_0007"]` in the diagnosis but does not specify:
- How are IDs generated? Sequential counter scoped to run? UUID? Run-prefixed?
- Where is the ID assigned — in the Tool Gateway before or after DB write?
- Can the investigator receive the evidence ID in the tool response, so it can cite it in the diagnosis? (This seems implied but is not stated.)

**Decision required:**
- IDs are run-scoped sequential integers: `ev_{run_short_id}_{seq:04d}`, e.g., `ev_a1b2_0003`
- The Tool Gateway generates the ID, writes evidence to DB, then returns the sanitized evidence payload **including the evidence_id** to the model
- The model is explicitly instructed to cite the evidence IDs it received; it cannot invent IDs it did not receive
- Evaluation validates cited IDs against the run's evidence rows

---

### 2.7 HIGH: Root-cause ID registry location unclear

**Location:** §6.3, §7.2

**Problem:** The spec says "unknown root-cause IDs make the diagnosis invalid" (§6.3). But where is the registry of valid root-cause IDs? The evaluator needs to know:
- Global valid IDs (a registry)
- Per-scenario expected ID (private expected)
- Permitted aliases (§7.2 mentions these)

If validity is checked only against the scenario's `private_expected.root_cause_id`, then `UNKNOWN` (the special case for insufficient evidence) also needs to be a declared valid special case.

**Decision required:**
- Valid root-cause IDs are declared at the scenario level in `private_expected`, including permitted aliases
- `UNKNOWN` is a globally reserved special value that is always syntactically valid but always scores zero on root-cause accuracy
- The evaluator checks: (a) is the ID `UNKNOWN` or in the scenario's `allowed_root_cause_ids`? If neither, the diagnosis is `INVALID`
- Rename `private_expected.root_cause_id` to `private_expected.allowed_root_cause_ids: list[str]` to make aliases explicit

---

### 2.8 HIGH: AWS Emulator timeline risk is underweighted

**Location:** §10.1, §16.2

**Problem:** The spec notes AWS Emulator was announced on 11 November 2026 — the day before this spec was written. The risk register rates it "Medium / Medium." This is too low.

A day-one product means:
- Docker build process may have undocumented requirements
- S3 operation fidelity is unverified by anyone outside AWS
- The documentation URLs (Appendix C, S3–S5) may not be fully published or stable
- The `aarch64` package for Apple Silicon may not be available in the first release

**Revised assessment:** "High / Medium." The mitigation (smoke gate, UNSUPPORTED path) is correct, but the delivery plan should treat the AWS scenario as P1 by default and only promote it to P0 if the smoke test passes within the first day of work.

**Decision required:** AWS Emulator smoke test (IF-010) must succeed before any IF-011 work begins. No conditional P0 — treat it as P1 until verified.

---

### 2.9 MEDIUM: Scoring schema doesn't account for `NOT_EVALUATED`

**Location:** §7.2, §9 (`evaluations` table)

**Problem:** The scoring rubric shows four numeric dimensions summing to 100. §7.2 correctly says remediation score should be `NOT_EVALUATED` if verification is not requested. But the `evaluations` table schema (`component_scores`) does not define how partial scores are stored.

A total score computed from only some components is misleading. A run that gets full marks on root-cause + evidence but skips verification should not display a "75%" score.

**Decision required:**
- `component_scores` stores each dimension as `{"score": N, "max": M, "status": "scored"|"not_evaluated"|"excluded"}`
- Display computed scores only over scored dimensions; never impute zero for `not_evaluated`
- The benchmark export (§7.4) already correctly separates `eligible_verified_runs`; the per-run record must match

---

### 2.10 MEDIUM: Two Kubernetes kubeconfig contexts need provisioning plan

**Location:** §11.2

**Problem:** The spec says "provide two Kubernetes clients" (observation vs controller) with separate ServiceAccounts and RBAC. This is correct. But there is no specification of:
- When these ServiceAccounts are created (during `make bootstrap`? Per-run?)
- How kubeconfig contexts are managed (single kubeconfig with two named contexts?)
- Whether the kind cluster config in `infra/kind/` includes RBAC definitions

**Decision required:**
- ServiceAccounts and ClusterRoles are defined in `infra/kind/rbac.yaml`, applied once during `make bootstrap`
- The bootstrap script writes two kubeconfig contexts: `incidentforge-observer` and `incidentforge-controller`
- The application loads these from paths in `.env`, never from ambient `~/.kube/config`

---

### 2.11 MEDIUM: Scenario YAML `private_expected` and Python `ScenarioAdapter` overlap

**Location:** §5.1, §5.2

**Problem:** The declarative YAML has `post_fix_checks` in `private_expected`. The `ScenarioAdapter.verify()` method must also run post-fix checks. Who owns this logic?

If `verify()` reads checks from the YAML and executes them generically (a check runner), then there's no need for per-scenario Python code for simple cases. But if `verify()` is overridden per-scenario, the YAML checks are redundant.

**Decision required:**
- `ScenarioAdapter` is per-environment (not per-scenario); `verify()` reads `private_expected.post_fix_checks` from the loaded scenario and dispatches to a `CheckRunner`
- The `CheckRunner` maps `type` strings (e.g., `k8s.deployment_available`) to Python check functions
- Per-scenario Python code is only needed when checks cannot be expressed declaratively
- This keeps the YAML as the single source of truth for scenario behavior

---

### 2.12 LOW: "Weekend timeline" scope/complexity mismatch

**Location:** §15.3

**Problem:** The delivery plan lists 12 sequential work packages across 7 agent roles. Even with parallel work, the spec covers: a Kubernetes operator, an AWS adapter, a provider-agnostic LLM client with structured output, a scoring engine, a Next.js SSE UI, and E2E tests. This is 2–3 weeks of focused work, not a weekend.

**This is not a blocker** — the minimum credible demo (§15.3: "one real Kubernetes incident, a validated AI diagnosis and a real passing/failing repair test") is achievable in a weekend. But the full scope is not.

**Recommendation:** The delivery plan should explicitly define what is in the minimum credible demo and treat everything beyond it as a second sprint. Attempting the full 12-ticket scope in a weekend risks delivering none of it correctly.

---

## 3. Contract Gaps — Items Not Defined in the Spec

The following must exist before parallel streams start. Agents cannot work from prose descriptions of these items.

| Gap | Required for | Location to define |
|---|---|---|
| `RunContext` dataclass | All agents using `ScenarioAdapter` | `contracts/types.py` |
| `CheckResult` dataclass | All agents using `ScenarioAdapter` | `contracts/types.py` |
| `ApprovedRemediation` model | Evaluator, controller, frontend | `contracts/types.py` |
| `EvidenceItem` model | Tool Gateway, Evidence Store, Diagnosis | `contracts/types.py` |
| `ToolDefinition` model | Investigator, Tool Gateway | `contracts/types.py` |
| `IncidentSummary` model | Investigator input | `contracts/types.py` |
| `ProviderAdapter` Protocol | Agent engineer (D) | `contracts/interfaces.py` |
| `ToolGateway` Protocol | Agent engineer (D), Lab engineer (B) | `contracts/interfaces.py` |
| `EvidenceStore` Protocol | Backend (C), Lab (B), Evaluator (E) | `contracts/interfaces.py` |
| `RunLock` Protocol | Backend (C) | `contracts/interfaces.py` |
| `CheckRunner` interface | Lab engineer (B) | `contracts/interfaces.py` |
| Evidence ID generation scheme | Tool Gateway, Evaluator | Decision record |
| `allowed_root_cause_ids` schema | Scenario loader, Evaluator | YAML schema update |
| `component_scores` DB schema | Backend (C), Evaluator (E) | Schema update |
| Kubeconfig provisioning | Infra/Lab bootstrap | `infra/kind/rbac.yaml` |
| Background task / SSE fan-out | Backend (C) | Architecture decision |

---

## 4. Shared Interface Definitions

The following interfaces and types are proposed. These must be agreed and frozen before parallel work begins. See `contracts/interfaces.py` and `contracts/types.py` for the implementations.

### 4.1 Canonical domain types

```python
# contracts/types.py

@dataclass(frozen=True)
class RunContext:
    run_id: str               # full ULID e.g. "run_01JABCDE..."
    run_short_id: str         # first 8 chars, used for naming
    scenario_id: str
    scenario_version: int
    namespace: str            # "if-{run_short_id}" for k8s scenarios
    allowed_resources: list[str]   # resource names the tool gateway may query

@dataclass(frozen=True) 
class CheckResult:
    success: bool
    code: str                 # machine-readable e.g. "DEPLOYMENT_AVAILABLE"
    message: str              # human-readable
    details: dict[str, Any] = field(default_factory=dict)

class ComponentScoreStatus(str, Enum):
    SCORED = "scored"
    NOT_EVALUATED = "not_evaluated"
    EXCLUDED = "excluded"       # environment failure, invalid scenario

@dataclass
class ComponentScore:
    score: int
    max: int
    status: ComponentScoreStatus
    reason: str | None = None

class EvidenceItem(BaseModel):  # Pydantic
    evidence_id: str            # ev_{run_short_id}_{seq:04d}
    run_id: str
    tool_name: str
    captured_at: datetime
    payload: dict[str, Any]     # sanitized tool output
    sha256: str                 # hash of serialized payload
    truncated: bool = False

class ToolDefinition(BaseModel):
    name: str                   # e.g. "k8s.list_pods"
    description: str
    parameters_schema: dict     # JSON Schema for arguments

class IncidentSummary(BaseModel):
    scenario_id: str
    title: str
    objective: str              # from public.summary in YAML

class ApprovedRemediation(BaseModel):
    action_type: str            # e.g. "k8s.set_readiness_path"
    target_ref: str
    parameters: dict[str, Any]
```

### 4.2 Protocols

```python
# contracts/interfaces.py

class ProviderAdapter(Protocol):
    """Abstracts LLM provider (OpenAI-compatible, Anthropic, Ollama, etc.)"""
    async def investigate(
        self,
        incident: IncidentSummary,
        tools: list[ToolDefinition],
        max_tool_calls: int,
        seed: int | None,
        temperature: float,
    ) -> InvestigationResult: ...
    # InvestigationResult = (tool_calls: list[ToolCallRecord], diagnosis: Diagnosis)

class ToolGateway(Protocol):
    """Executes one tool call; validates args; persists and returns evidence."""
    async def call(
        self,
        context: RunContext,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> EvidenceItem: ...

class EvidenceStore(Protocol):
    """Persists and retrieves evidence for a run."""
    async def save(self, item: EvidenceItem) -> None: ...
    async def list_by_run(self, run_id: str) -> list[EvidenceItem]: ...
    async def validate_ids(
        self, run_id: str, claimed_ids: list[str]
    ) -> list[str]: ...  # returns IDs that do NOT exist in the run

class RunLock(Protocol):
    """Enforces one active run. Persistent across restarts via DB."""
    async def acquire(self, run_id: str) -> bool: ...  # False = lab busy
    async def release(self, run_id: str) -> None: ...
    async def recover_interrupted(self) -> list[str]: ...  # run IDs marked INTERRUPTED

class CheckRunner(Protocol):
    """Executes declarative check predicates from scenario YAML."""
    async def run(
        self, check: CheckSpec, context: RunContext
    ) -> CheckResult: ...
```

### 4.3 Scenario YAML schema change (additive)

Replace:
```yaml
private_expected:
  root_cause_id: "K8S_READINESS_PATH_MISMATCH"
```

With:
```yaml
private_expected:
  allowed_root_cause_ids:
    - "K8S_READINESS_PATH_MISMATCH"   # canonical
    # aliases may be added here without changing scoring logic
```

---

## 5. Recommended Pre-Build Order (revised from §15.2)

The original IF-001–IF-013 order is good but must be preceded by one additional gate:

| Order | Ticket | Deliverable | Blocks |
|---:|---|---|---|
| **0** | **IF-000 Freeze shared interfaces and types** | `contracts/types.py`, `contracts/interfaces.py`, YAML schema v1.1, evidence ID scheme, SSE fan-out decision, run lock decision | Everything |
| 1 | IF-001 Monorepo, tooling, .env.example | Dev environment | All agents |
| 2 | IF-002 Scenario loader + schema validation | Public/private separation tests | B, C, D, E |
| 3 | IF-003 Run lifecycle engine + state machine | State transition unit tests | C |
| 4 | IF-004 kind cluster + k8s scenario adapters | Real fault inject/reset/verify | B, D |
| 5 | IF-005 Observation gateway (read-only k8s) | RBAC + denied-scope tests | D |
| 6 | IF-006 AI investigator + provider adapter | Diagnosis validates, budget enforced | E |
| 7 | IF-007 Deterministic evaluator + remediation test | Good/bad fix classification | Integration |
| 8 | IF-008 FastAPI + SQLite + SSE | End-to-end via curl | F |
| 9 | IF-009 Next.js UI | Browser demo | Demo |
| 10 | IF-010 AWS Emulator smoke test | ← **P1 by default; promotes to P0 only if smoke passes day 1** | IF-011 |
| 11 | IF-011 AWS S3 scenario | Conditional on IF-010 | Demo |
| 12 | IF-012 QA, security audit, demo | All DoD | Release |

**Key change from spec:** IF-000 is a new required gate. No parallel work begins without frozen contracts. IF-010 is demoted to default P1 given the new-product risk.

---

## 6. Open Questions Requiring Answers

Before parallel development starts, the following decisions must be made:

1. **Model provider for MVP:** Which specific provider and model ID? This determines which `ProviderAdapter` stub gets built first. (Spec §16.3 says "one provider that reliably supports tool calls and structured output" — nominate one.)
2. **Apple Silicon only or cross-platform?** The AWS Emulator `aarch64` package targets Apple Silicon. If contributors use `x86_64` machines, the Dockerfile needs both arches or a build-arg.
3. **`kind` cluster lifecycle:** Create once per machine (persistent) or per test run (ephemeral)? The spec implies persistent for the demo, ephemeral for CI. The bootstrap script needs to handle both.
4. **Frontend model selector:** Is the model/provider configurable via the UI at run time, or is it set via `.env` only? The spec shows a "model selector" on the overview page (§13.1) — if this is a runtime UI control, the backend needs a provider registry, not just a fixed env var.
5. **Namespace cleanup policy:** After a run completes, does the namespace get deleted immediately, on the next run start, or manually? This affects the "Replay scenario" flow (§13.2).
6. **Who owns the scenario YAML schema JSON Schema file?** The spec says "Agent A (tech lead)" owns `contracts/`. Establish this explicitly so agents B/E don't drift the schema independently.
