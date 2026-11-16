# Decision Log

## architecture

- [IncidentForge: SSE architecture confirmed — asyncio background task + RunEventBus + DB replay](architecture/20261115-222804-incidentforge-sse-architecture-confirmed-asyncio-background-task-runeventbus-db.md) — 2026-11-15
- [IncidentForge: Kubernetes namespace cleanup is LAZY — kept until next run starts](architecture/20261115-222759-incidentforge-kubernetes-namespace-cleanup-is-lazy-kept-until-next-run-starts.md) — 2026-11-15
- [IncidentForge: contracts/ directory is owned exclusively by the tech lead; changes require PR review](architecture/20261115-222759-incidentforge-contracts-directory-is-owned-exclusively-by-the-tech-lead-changes.md) — 2026-11-15
- [IncidentForge: kind cluster is persistent (created once at bootstrap, not per-run)](architecture/20261115-222752-incidentforge-kind-cluster-is-persistent-created-once-at-bootstrap-not-per-run.md) — 2026-11-15
- [IncidentForge: Model selector is a runtime UI control; GET /providers lists configured providers](architecture/20261115-222752-incidentforge-model-selector-is-a-runtime-ui-control-get-providers-lists-configu.md) — 2026-11-15
- [IncidentForge: AWS Emulator Docker image targets Apple Silicon (aarch64) only for MVP](architecture/20261115-222745-incidentforge-aws-emulator-docker-image-targets-apple-silicon-aarch64-only-for-m.md) — 2026-11-15
- [IncidentForge: LLM provider — OpenAI first, Claude later via ProviderAdapter](architecture/20261115-222745-incidentforge-llm-provider-openai-first-claude-later-via-provideradapter.md) — 2026-11-15
- [IncidentForge: AWS Emulator is P1 by default; promoted to P0 only after smoke test passes](architecture/20261112-022432-incidentforge-aws-emulator-is-p1-by-default-promoted-to-p0-only-after-smoke-test.md) — 2026-11-12
- [IncidentForge: Run lock is DB-backed via non-terminal run status; asyncio.Lock for intra-process safety](architecture/20261112-022427-incidentforge-run-lock-is-db-backed-via-non-terminal-run-status-asyncio-lock-for.md) — 2026-11-12
- [IncidentForge: Scenario schema v1.1 uses allowed_root_cause_ids (list) instead of singular root_cause_id](architecture/20261112-022423-incidentforge-scenario-schema-v1-1-uses-allowed-root-cause-ids-list-instead-of-s.md) — 2026-11-12
- [IncidentForge: ScenarioAdapter is per-environment, not per-scenario; uses CheckRunner + ActionDispatcher](architecture/20261112-022418-incidentforge-scenarioadapter-is-per-environment-not-per-scenario-uses-checkrunn.md) — 2026-11-12
- [IncidentForge: SSE fan-out via asyncio background task + RunEventBus + DB replay](architecture/20261112-022414-incidentforge-sse-fan-out-via-asyncio-background-task-runeventbus-db-replay.md) — 2026-11-12
- [IncidentForge: Shared interface contracts defined before parallel development](architecture/20261112-022410-incidentforge-shared-interface-contracts-defined-before-parallel-development.md) — 2026-11-12

## tooling

- [IncidentForge: apps/web npm install requires --legacy-peer-deps](tooling/20261115-223827-incidentforge-apps-web-npm-install-requires-legacy-peer-deps.md) — 2026-11-15
