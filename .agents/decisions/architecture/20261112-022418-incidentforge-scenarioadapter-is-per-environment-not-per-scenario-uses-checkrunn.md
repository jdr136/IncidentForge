---
id: "20261112-022418-incidentforge-scenarioadapter-is-per-environment-not-per-sce"
title: "IncidentForge: ScenarioAdapter is per-environment, not per-scenario; uses CheckRunner + ActionDispatcher"
category: "architecture"
date: "2026-11-12T02:24:18.853785+00:00"
tags: ["scenario", "adapter", "incidentforge"]
---

## Decision
ScenarioAdapter implementations are one per environment type (KubernetesAdapter, AWSAdapter), not one per scenario. The adapter reads declarative fault/setup/check fields from the loaded scenario YAML and dispatches to a CheckRunner (for check predicates) and an ActionDispatcher (for fault/remediation actions). Post-fix checks in private_expected.post_fix_checks are executed declaratively, not via bespoke Python per scenario.

## Rationale
One adapter per scenario would require a new Python file for every scenario added. The declarative YAML schema already captures the needed parameters. The adapter + CheckRunner + ActionDispatcher pattern keeps scenario authors working in YAML, not Python, for standard cases.

## Context
The scenario YAML's fault.action string (e.g., k8s.patch_readiness_path) is dispatched by the ActionDispatcher within the environment adapter. The CheckRunner maps check type strings (e.g., k8s.deployment_available) to Python check functions. Custom Python only needed for scenarios that cannot be expressed declaratively.