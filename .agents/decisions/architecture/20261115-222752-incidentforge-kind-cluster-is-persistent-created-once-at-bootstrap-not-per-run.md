---
id: "20261115-222752-incidentforge-kind-cluster-is-persistent-created-once-at-boo"
title: "IncidentForge: kind cluster is persistent (created once at bootstrap, not per-run)"
category: "architecture"
date: "2026-11-15T22:27:52.752637+00:00"
tags: ["kubernetes", "kind", "bootstrap", "incidentforge"]
---

## Decision
The kind cluster named `incidentforge` is created once during `make bootstrap` and kept running between sessions. `make dev` assumes the cluster already exists. Scenario runs create and clean up temporary namespaces inside the cluster; they never create or destroy the cluster itself.

## Rationale
Creating a kind cluster takes ~30 seconds. For demo and development workflows, persistent is significantly faster. The cluster is local and disposable — it can always be torn down with `make clean-cluster` if needed.

## Context
`make bootstrap` must be idempotent: if the cluster already exists, skip creation. Use `kind get clusters | grep incidentforge` to check. Cluster config lives in `infra/kind/cluster.yaml`.
