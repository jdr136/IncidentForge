---
id: "20261115-222759-incidentforge-kubernetes-namespace-cleanup-is-lazy-kept-unti"
title: "IncidentForge: Kubernetes namespace cleanup is LAZY — kept until next run starts"
category: "architecture"
date: "2026-11-15T22:27:59.614128+00:00"
tags: ["kubernetes", "cleanup", "namespace", "incidentforge"]
---

## Decision
Kubernetes namespace cleanup policy is LAZY: the namespace from a completed run is kept until the next run starts (or until `make clean`). The next run's prepare() step deletes any leftover namespace from the previous run before creating a new one. Configurable via CLEANUP_POLICY env var (lazy|immediate|manual), default: lazy.

## Rationale
Lazy cleanup lets developers and demo viewers inspect the broken/fixed state in kubectl after a run completes. This is useful for demos, debugging, and understanding what the AI actually saw.

## Context
The ScenarioAdapter.prepare() method is responsible for cleaning up any existing namespace for this scenario before provisioning the baseline. The namespace name includes the previous run_short_id, so prepare() should clean up any namespace with the incidentforge label selector, not just a specific name.
