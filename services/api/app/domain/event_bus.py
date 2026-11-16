"""
RunEventBus — in-process fan-out for SSE subscribers.

Architecture:
- Investigation background task calls publish() for each RunEvent.
- SSE endpoint calls subscribe() which yields live events via asyncio.Queue.
- When run reaches terminal state, the subscriber loop breaks automatically
  because the stage is in TERMINAL_STATUSES.
- For reconnecting clients: the SSE endpoint replays from DB first,
  then subscribes here for live events.
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict
from collections.abc import AsyncIterator

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
from contracts.types import TERMINAL_STATUSES, RunEvent


class RunEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def publish(self, event: RunEvent) -> None:
        """Broadcast a persisted event to all live subscribers."""
        async with self._lock:
            for q in self._subscribers.get(event.run_id, []):
                await q.put(event)

    async def subscribe(self, run_id: str) -> AsyncIterator[RunEvent]:
        """
        Yield live events as they are published.
        Stops when a terminal-stage event is received.
        Call this AFTER replaying historical events from DB.
        """
        q: asyncio.Queue[RunEvent] = asyncio.Queue()
        async with self._lock:
            self._subscribers[run_id].append(q)
        try:
            while True:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                yield event
                if event.stage in TERMINAL_STATUSES:
                    break
        except TimeoutError:
            pass  # client will reconnect with Last-Event-ID
        finally:
            async with self._lock:
                subs = self._subscribers.get(run_id, [])
                if q in subs:
                    subs.remove(q)


# Module-level singleton
event_bus = RunEventBus()
