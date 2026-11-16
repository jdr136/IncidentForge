# Open Questions — IncidentForge Pre-Build

**Status: ALL RESOLVED** — answered 15 November 2026.
Decisions recorded as ADRs in `.agents/decisions/architecture/`.

---

| # | Question | Decision |
|---|---|---|
| Q1 | Which model provider for MVP? | **OpenAI first** (`gpt-4o`). Claude later via `ProviderAdapter`. `.env.example`: `OPENAI_API_KEY`, `OPENAI_MODEL`. |
| Q2 | Apple Silicon only or cross-platform? | **Apple Silicon (aarch64) only** for MVP. Documented as a known limitation. |
| Q3 | kind cluster lifecycle? | **Persistent.** `make bootstrap` creates once, idempotent. Scenario runs use namespaces inside the cluster. |
| Q4 | Model selector runtime or config-only? | **Runtime UI control.** `GET /providers` lists configured providers. Never expose API keys to the frontend. |
| Q5 | Namespace cleanup policy? | **Lazy.** Namespace kept until next run's `prepare()` cleans it up. Default `CLEANUP_POLICY=lazy`. |
| Q6 | Who owns `contracts/`? | **Tech lead only.** All changes via PR. Documented in README and `contracts/__init__.py`. |
| Q7 | SSE fan-out architecture? | **Confirmed.** `asyncio.create_task` + `RunEventBus` (per-run `asyncio.Queue`) + DB replay on reconnect. |

---

No open questions remain. Development can proceed.
