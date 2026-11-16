---
id: "20261112-022410-incidentforge-shared-interface-contracts-defined-before-para"
title: "IncidentForge: Shared interface contracts defined before parallel development"
category: "architecture"
date: "2026-11-12T02:24:10.245678+00:00"
tags: ["architecture", "interfaces", "contracts", "incidentforge"]
---

## Decision
All shared types and protocols for IncidentForge are defined in `contracts/types.py` and `contracts/interfaces.py` before any parallel development stream begins. This is work package IF-000, which precedes the IF-001–IF-013 sequence from the original spec.

## Rationale
Parallel agents (Lab engineer, Backend engineer, Agent engineer, Evaluator engineer, Frontend engineer) all need to compile against the same interfaces. Without frozen contracts, types are invented independently and diverge at integration time.

## Context
Key interfaces defined: `ScenarioAdapter`, `ProviderAdapter`, `ToolGateway`, `EvidenceStore`, `RunLock`, `CheckRunner`, `Evaluator`, `RunEventBus`, `ScenarioRegistry`. Key domain types defined: `RunContext`, `CheckResult`, `ApprovedRemediation`, `EvidenceItem`, `ToolDefinition`, `IncidentSummary`, `Diagnosis`, `EvaluationResult`, `RunEvent`, `InvestigationResult`. JSON Schemas defined for scenario (v1.1), diagnosis (v1.0), and run event (v1.0).