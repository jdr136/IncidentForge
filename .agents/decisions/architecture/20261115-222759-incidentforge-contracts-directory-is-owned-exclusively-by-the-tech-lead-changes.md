---
id: "20261115-222759-incidentforge-contracts-directory-is-owned-exclusively-by-th"
title: "IncidentForge: contracts/ directory is owned exclusively by the tech lead; changes require PR review"
category: "architecture"
date: "2026-11-15T22:27:59.614104+00:00"
tags: ["contracts", "governance", "incidentforge"]
---

## Decision
The contracts/ directory (types.py, interfaces.py, schemas/) is owned exclusively by the tech lead / integrator role. No other agent or contributor may modify these files directly. All contract changes go through a pull request reviewed by the tech lead. This policy is documented in the README and in contracts/__init__.py.

## Rationale
Contracts are the shared build surface for all parallel development streams. Unreviewed changes to contracts break every other agent's work. Central ownership prevents drift.

## Context
This applies to: contracts/types.py, contracts/interfaces.py, contracts/schemas/*.json. It does NOT apply to scenario YAML files in scenarios/ — those follow the schema but are authored by the lab/scenario engineer.
