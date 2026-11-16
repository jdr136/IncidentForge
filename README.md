# IncidentForge

> **Break infrastructure safely. Challenge AI to fix it. Prove whether it was right.**

IncidentForge is a local-first reliability lab that injects reproducible faults into controlled environments, asks an LLM to diagnose them using read-only tools only, then independently verifies whether the diagnosis and proposed fix are correct.

A fluent-sounding AI diagnosis is not a verified diagnosis. IncidentForge proves the difference.

---

## How it works

```
 1. INJECT   Controller breaks something in a real local k8s cluster or AWS Emulator
 2. OBSERVE  AI investigator calls read-only tools (list pods, read logs, check events…)
 3. DIAGNOSE LLM returns a structured diagnosis with root_cause_id, evidence IDs, proposed fix
 4. EVALUATE Deterministic scorer checks the diagnosis against hidden ground truth
 5. VERIFY   Trusted controller applies the fix in a disposable sandbox and checks postconditions
 6. SCORE    Pass/fail per dimension — root cause, evidence quality, remediation, efficiency
```

The investigator **cannot** write to the cluster, call real AWS, execute shell commands, or see the hidden answer key. Its tool output is treated as untrusted data throughout.

---

## Architecture

```
┌─────────────┐  REST + SSE   ┌──────────────────────────────────────────────────────┐
│  Next.js UI │──────────────▶│                 FastAPI Orchestrator                  │
└─────────────┘               │                                                      │
                               │  ┌─────────────┐  ┌─────────────┐  ┌────────────┐  │
                               │  │  Scenario   │  │     AI      │  │ Evaluator  │  │
                               │  │  Controller │  │ Investigator│  │(trusted,   │  │
                               │  │  (trusted)  │  │(read-only   │  │ no LLM)    │  │
                               │  └──────┬──────┘  │ tools only) │  └────┬───────┘  │
                               │         │          └──────┬──────┘       │          │
                               └─────────┼─────────────────┼──────────────┼──────────┘
                                         │                 │              │
                               ┌─────────▼──────┐  ┌───────▼──────┐  ┌───▼────────┐
                               │  kind Cluster   │  │  Tool       │  │  SQLite    │
                               │  (local k8s)    │  │  Gateway    │  │  (WAL)     │
                               └────────────────┘  │  (gated,    │  └────────────┘
                                                    │  sanitised) │
                               ┌────────────────┐  └────────────-┘
                               │  AWS Emulator   │
                               │  (loopback, P1) │
                               └────────────────┘
```

**Trust boundary:** The Scenario Controller and Evaluator are trusted services that hold ground truth and write-access credentials. The LLM Investigator receives only read-only tool access and the public incident summary — never the answer key, fixture paths, or fault parameters.

---

## MVP scenarios

| ID | Environment | What breaks | Ground-truth fix |
|---|---|---|---|
| `k8s-readiness-path` | Kubernetes | Readiness probe points at `/health` (app only serves `/healthz`) | Set probe path back to `/healthz` |
| `k8s-service-selector` | Kubernetes | Service selector label mismatch — pods healthy, zero endpoints | Restore correct label selector |
| `aws-s3-missing-object` | AWS Emulator | Config object deleted from S3 bucket | Restore fixture object (checksum verified) |

The AWS scenario (P1) requires the AWS Emulator to pass a smoke test first.

---

## Prerequisites

| Tool | Minimum | Install |
|---|---|---|
| Docker Desktop | 4.x | https://www.docker.com/products/docker-desktop |
| `kind` | 0.24 | `brew install kind` |
| `kubectl` | 1.31 | `brew install kubectl` |
| Python | 3.12 | `brew install python@3.12` |
| `uv` | 0.4 | `brew install uv` |
| Node.js | 20 | `brew install node` |

Verify everything:

```bash
make doctor
```

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/your-org/incidentforge.git
cd incidentforge

# 2. Configure
cp .env.example .env
# Edit .env — set OPENAI_API_KEY at minimum

# 3. Install deps and create the kind cluster (once)
make bootstrap

# 4. Start servers (two terminals)
make dev-api      # terminal 1 — FastAPI on :8000
make dev-web      # terminal 2 — Next.js on :3000

# 5. Run the test suite
make test

# 6. Run the demo (API must be running)
make demo
```

Open **http://localhost:3000** to see the UI.

---

## Demo walkthrough

With both servers running and `OPENAI_API_KEY` set:

1. Open http://localhost:3000
2. The **Scenario Library** shows three scenario cards
3. Click **"API pods never become ready"** to select it
4. Choose your model from the selector (defaults to `gpt-4o`)
5. Click **Run** — you're redirected to the live run page
6. Watch the event timeline:
   - `PREPARING` → healthy baseline deployed to `kind` cluster
   - `BASELINE_READY` → deployment available, service has endpoints
   - `INJECTING` → readiness probe path changed to `/health`
   - `FAULT_CONFIRMED` → pods not ready, service has zero endpoints
   - `INVESTIGATING` → AI calls `k8s.list_pods`, `k8s.get_deployment`, `k8s.list_events`…
   - `DIAGNOSED` → AI returns structured diagnosis with root_cause_id and proposed fix
7. The right panel shows the diagnosis with root cause ID and cited evidence IDs
8. Click **"✓ Verify fix in sandbox"** — the controller applies the fix and checks postconditions
9. `COMPLETED` — evaluation scores appear

> **Without `OPENAI_API_KEY`:** The system runs in STUB mode — the same lifecycle but with a scripted stub diagnosis. All UI flows, SSE streaming, and state machine transitions work identically. STUB labels are shown clearly.

> **Without `kind`:** The Kubernetes adapter returns a controlled error and the run fails gracefully. Only the kind cluster scenarios are affected.

---

## Make targets

| Target | What it does |
|---|---|
| `make doctor` | Check all prerequisites are installed and ports are free |
| `make bootstrap` | Install Python + Node deps, create kind cluster, apply RBAC |
| `make dev-api` | Start FastAPI dev server on :8000 (auto-reload) |
| `make dev-web` | Start Next.js dev server on :3000 |
| `make test` | Unit + contract + security tests (no live infra required) |
| `make test-integration` | Integration tests (requires kind cluster) |
| `make lint` | Ruff linter |
| `make format` | Ruff auto-format |
| `make demo` | Run the k8s-readiness-path scenario end-to-end (API must be running) |
| `make clean` | Remove scenario-owned k8s namespaces and local DB files |
| `make clean-cluster` | Delete the kind cluster entirely |
| `make emulator-build` | Build AWS Emulator image (Apple Silicon, P1) |
| `make emulator-up` | Start emulator on 127.0.0.1:9297 (P1) |
| `make emulator-smoke` | Verify emulator + required S3 operations (P1) |

---

## Security model

- **Read-only investigator.** The LLM calls only `get/list/watch` operations on approved resources. Write verbs, Secrets, and system namespaces are explicitly blocked.
- **Ground truth separation.** `private_expected`, fixture paths, fault parameters, and answer keys are never serialised into API responses, investigator prompts, or tool outputs.
- **Fail-closed AWS adapter.** The Boto3 adapter checks all five safety conditions before making any call. No silent fallback to real AWS endpoints.
- **Typed remediations only.** The investigator proposes an `action_type` from the scenario's allowlist. The trusted controller executes it — never arbitrary shell or `kubectl` commands.
- **Explicit verification gate.** The UI requires a click to trigger `POST /runs/{id}/verify`. The fix is not applied automatically.
- **Local by default.** No production credentials are required, used, or stored.

---

## Repository layout

```
contracts/          Shared types, protocols, JSON schemas (tech-lead owned)
services/api/       FastAPI backend
  app/
    api/            Routes and SSE endpoints
    domain/         State machine, event bus, orchestrator
    scenarios/      Loader, schema validation, registry (public/private split)
    labs/           Kubernetes and AWS Emulator adapters
    investigator/   Provider adapter (OpenAI) and tool gateway
    evaluation/     Deterministic evaluator and scoring rubric
    persistence/    SQLite repositories
apps/web/           Next.js frontend
scenarios/          Scenario YAML definitions + fixtures
infra/              kind cluster config and AWS Emulator Dockerfile
tests/unit/         Unit, contract and security tests (make test)
tests/integration/  Integration tests (requires live cluster)
docs/               Architecture review, open questions, decisions
scripts/            Bootstrap, smoke and demo scripts
```

---

## Configuration (`.env`)

```bash
# LLM — required for real AI investigations
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o

# Server
API_HOST=127.0.0.1
API_PORT=8000

# Lab behaviour
CLEANUP_POLICY=lazy      # keep namespace until next run (good for demos)
TOOL_BUDGET=12           # max tool calls per investigation
INVESTIGATION_TIMEOUT_S=120
```

See `.env.example` for all options.

---

## Troubleshooting

**`make doctor` shows `kind` missing:**
```bash
brew install kind
```

**API won't start — port 8000 in use:**
```bash
lsof -ti :8000 | xargs kill -9
```

**Scenario runs in STUB mode / AI says "no key":**
Set `OPENAI_API_KEY` in `.env` and restart `make dev-api`.

**kind cluster not found after bootstrap:**
```bash
make clean-cluster
make bootstrap
```

**Run stuck in PREPARING / BASELINE_READY:**
Check that the kind cluster is running: `kind get clusters`. If missing, `make bootstrap`.

**`k8s.set_readiness_path` returns POLICY_DENIED:**
The proposed action is not in `private_expected.allowed_remediations` for this scenario. The AI suggested an unauthorised fix — this is working as designed.

**SSE events stop arriving:**
Reconnect by refreshing the run page. The SSE endpoint replays all historical events using `Last-Event-ID`, so no events are lost.

---

## Contributing

See [`AGENTS.md`](AGENTS.md) for the agent collaboration rules, work-package ownership, and PR checklist.

**Key rule:** `contracts/` is owned exclusively by the tech lead. All changes to `contracts/types.py`, `contracts/interfaces.py`, or `contracts/schemas/*.json` require a PR review.

---

## Decisions log

Architectural decisions are recorded in `.agents/decisions/`. Key ones:

- LLM provider: OpenAI first; Anthropic/Ollama later via `ProviderAdapter` abstraction
- Platform: Apple Silicon (aarch64) only for MVP
- kind cluster: persistent, created once at `make bootstrap`
- Model selector: runtime UI control (`GET /api/v1/providers`)
- Namespace cleanup: lazy (kept until next run starts)
- SSE: `asyncio.create_task` + `RunEventBus` + DB replay on reconnect
- ScenarioAdapter: one per environment type (not per scenario)
- Run lock: DB-backed via non-terminal run status

Full rationale: [`docs/architecture-review.md`](docs/architecture-review.md)

---

## Validation status

This is a **working implementation**, not a paper spec. The test suite (`make test`) validates the contracts, security invariants, evaluation logic, and state machine. Run counts in `GET /api/v1/benchmarks/summary` reflect actual completed runs. Any benchmark figure presented externally must cite the specific model, temperature, scenario version, and environment used.
