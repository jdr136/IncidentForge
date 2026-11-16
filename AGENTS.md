# AGENTS.md — IncidentForge Collaboration Rules

This document governs how coding agents (and human contributors) work together on IncidentForge. These rules exist to prevent integration failures, ground-truth leakage, and conflicting schema changes.

---

## Work package ownership

| Agent / role | Owns | Ticket |
|---|---|---|
| **A — Tech lead / integrator** | `contracts/`, `docs/`, project config, PR reviews | IF-000, IF-002 |
| **B — Lab engineer** | `infra/`, `services/api/app/labs/`, `scenarios/` | IF-004, IF-005, IF-010, IF-011 |
| **C — Backend engineer** | `services/api/app/api/`, `app/domain/`, `app/persistence/` | IF-003, IF-008 |
| **D — Agent engineer** | `services/api/app/investigator/` | IF-006 |
| **E — Evaluation engineer** | `services/api/app/evaluation/`, `tests/` | IF-007 |
| **F — Frontend** | `apps/web/` | IF-009 |
| **G — QA / docs** | `tests/`, `README.md`, `docs/` | IF-012 |

With fewer agents, combine A+C, B+E, and F+G.

---

## Non-negotiable rules

### 1. Contracts are tech-lead only
`contracts/types.py`, `contracts/interfaces.py`, and `contracts/schemas/*.json` may only be changed via a PR reviewed by the tech lead. No agent modifies these directly. If you need a contract change, open a discussion — do not edit the file.

### 2. Ground truth must never reach the model
`private_expected`, `setup`, `fault`, fixture paths and verification predicates are **private** to the scenario controller and evaluator. They must not appear in:
- Any HTTP response from the public API
- Any investigator prompt or tool response
- Any log that is surfaced in the UI
- Any debug trace or error message shown to the user

Write a regression test for every code path that touches private fields.

### 3. No fake test results
If an integration test cannot run because the kind cluster is unavailable, mark it `pytest.mark.skip` with a clear reason. Do not mock the cluster and call it an integration test. Mark any external/emulator capability `UNVERIFIED` until it has been smoke-tested against the real thing.

### 4. Typed remediations only
The investigator may never execute arbitrary shell commands, kubectl commands, or AWS CLI calls. The only actions allowed are those defined in `private_expected.allowed_remediations` for the active scenario. Any other action_type returns `POLICY_DENIED`.

### 5. AWS adapter fails closed
The AWS Boto3 adapter checks all five safety conditions (§10.3 of the spec) before making any call. If any condition fails, the call is rejected — no silent fallback to real AWS endpoints.

---

## Before writing code

Every agent must write a brief (can be a comment block or a short markdown file) covering:
- Scope: exactly which files will be created or modified
- Interfaces: which contracts/ types and protocols you depend on
- Tests: what tests you will write and what they prove
- Assumptions: anything not covered by the spec that you are deciding

---

## Pull request checklist

- [ ] Contract tests pass (`make test`)
- [ ] Negative-path tests included (wrong inputs, denied actions, boundary cases)
- [ ] No `private_expected` data reachable via public API (run leakage check)
- [ ] Commands actually run and observed output included in PR description
- [ ] No `verify=False`, no disabled TLS, no `0.0.0.0` AWS port binding
- [ ] No real credentials in fixtures, test data, or comments
- [ ] Tech lead reviewed if `contracts/` touched

---

## Integration order

Schemas → labs/backend → investigator/evaluation → UI → E2E

Do not claim integration success from mocks. Mark capabilities `UNVERIFIED` until smoke-tested.

---

## Decisions log

Architectural decisions are recorded in `.agents/decisions/`. Before proposing a design, check there first. Key decisions already made:

- LLM provider: OpenAI first, Claude later (`ProviderAdapter` abstraction)
- Platform: Apple Silicon (aarch64) only for MVP
- kind cluster: persistent, created once at `make bootstrap`
- Model selector: runtime UI control (`GET /api/v1/providers`)
- Namespace cleanup: lazy (kept until next run starts)
- SSE: `asyncio.create_task` + `RunEventBus` + DB replay on reconnect
- ScenarioAdapter: one per environment type, not per scenario
- Run lock: DB-backed via non-terminal run status
- Evidence IDs: `ev_{run_short_id}_{seq:04d}`, scoped per run

Full rationale: `docs/architecture-review.md`
