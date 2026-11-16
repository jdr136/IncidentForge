---
id: "20261112-022423-incidentforge-scenario-schema-v1-1-uses-allowed-root-cause-i"
title: "IncidentForge: Scenario schema v1.1 uses allowed_root_cause_ids (list) instead of singular root_cause_id"
category: "architecture"
date: "2026-11-12T02:24:23.389024+00:00"
tags: ["scenario", "evaluation", "schema", "incidentforge"]
---

## Decision
The scenario YAML schema v1.1 uses `private_expected.allowed_root_cause_ids: list[str]` instead of the spec's `private_expected.root_cause_id: str`. The global special value UNKNOWN is always syntactically valid and always scores zero on root-cause accuracy. The evaluator checks: is the ID UNKNOWN or in the scenario's allowed_root_cause_ids? If neither, the diagnosis is INVALID.

## Rationale
The spec mentions "permitted aliases" (§7.2) but the YAML schema used a singular root_cause_id which cannot accommodate aliases without a parallel field. Using a list makes aliases explicit and versioned. The plural also avoids special-case alias code in the evaluator.

## Context
Schema version bumped to 1.1 to reflect this change. The scenario JSON Schema at contracts/schemas/scenario.v1.1.json enforces this. All three MVP scenarios have been written with allowed_root_cause_ids.