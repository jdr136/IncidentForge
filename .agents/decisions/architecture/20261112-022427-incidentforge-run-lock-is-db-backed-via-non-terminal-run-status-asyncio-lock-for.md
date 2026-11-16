---
id: "20261112-022427-incidentforge-run-lock-is-db-backed-via-non-terminal-run-sta"
title: "IncidentForge: Run lock is DB-backed via non-terminal run status; asyncio.Lock for intra-process safety"
category: "architecture"
date: "2026-11-12T02:24:27.297074+00:00"
tags: ["state-machine", "concurrency", "incidentforge"]
---

## Decision
The run lock is DB-backed: a run with any non-terminal status is considered the lock holder. POST /runs queries for any non-terminal run; if found, returns 409 LAB_BUSY. An in-memory asyncio.Lock prevents concurrent requests within one process lifetime. On startup, any run with a non-terminal status is transitioned to INTERRUPTED by RunLock.recover_interrupted().

## Rationale
An in-memory asyncio.Lock is lost on crash. The DB state survives crashes and allows startup recovery without additional state files. The pattern is simple and correct for single-process MVP.

## Context
Terminal statuses: COMPLETED, FAILED, TIMED_OUT, CANCELLED, INTERRUPTED, UNSUPPORTED, INVALID_SCENARIO. The RunLock Protocol is defined in contracts/interfaces.py.