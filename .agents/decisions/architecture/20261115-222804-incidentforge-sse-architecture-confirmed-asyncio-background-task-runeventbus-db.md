---
id: "20261115-222804-incidentforge-sse-architecture-confirmed-asyncio-background"
title: "IncidentForge: SSE architecture confirmed — asyncio background task + RunEventBus + DB replay"
category: "architecture"
date: "2026-11-15T22:28:04.282369+00:00"
tags: ["sse", "async", "fastapi", "incidentforge"]
---

## Decision
SSE streaming uses asyncio.create_task for the investigation background loop. Events are persisted to the SQLite events table first, then broadcast to live SSE subscribers via an in-process RunEventBus (per-run asyncio.Queue). SSE reconnect replays all historical events from DB using seq > Last-Event-ID, then joins the live queue. The connection closes when the run reaches a terminal state.

## Rationale
Confirmed by the team. This is the only viable single-process model that keeps FastAPI's event loop free while the investigation runs in the background.

## Context
Implementation notes: investigation task calls RunEventBus.publish() for every RunEvent. SSE endpoint calls RunEventBus.subscribe(run_id) which returns an AsyncIterator. The bus maintains a dict of {run_id: list[asyncio.Queue]} for live subscribers. On terminal event, the bus drains and closes all queues for that run_id.
