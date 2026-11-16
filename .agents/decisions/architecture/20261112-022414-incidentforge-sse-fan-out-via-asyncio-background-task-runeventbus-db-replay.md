---
id: "20261112-022414-incidentforge-sse-fan-out-via-asyncio-background-task-runeve"
title: "IncidentForge: SSE fan-out via asyncio background task + RunEventBus + DB replay"
category: "architecture"
date: "2026-11-12T02:24:14.118279+00:00"
tags: ["sse", "async", "fastapi", "incidentforge"]
---

## Decision
The SSE architecture uses asyncio.create_task for the investigation background loop, event persistence to SQLite events table, and an in-process RunEventBus that fans out to per-run asyncio.Queue subscribers. SSE reconnect replays historical events from DB using `seq > Last-Event-ID`.

## Rationale
FastAPI's event loop cannot be blocked by a synchronous investigation loop. The investigation must run as a background task. Events must be persisted first (for reconnect/crash recovery), then broadcast to live SSE subscribers. This is the only viable single-process model.

## Context
Affects Backend engineer (Agent C). The investigation task calls `RunEventBus.publish()` for every RunEvent it creates. The SSE endpoint subscribes via `RunEventBus.subscribe()`. On terminal state, the bus signals the queue closed and the SSE connection ends.