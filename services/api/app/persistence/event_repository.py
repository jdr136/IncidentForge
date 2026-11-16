"""EventRepository — append-only event log for SSE replay."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import aiosqlite

from app.persistence.database import get_db

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
from contracts.types import RunEvent, RunEventType, RunStatus


class EventRepository:
    async def append(self, event: RunEvent) -> RunEvent:
        """Persist event; assigns id (DB autoincrement) and seq."""
        db = await get_db()
        try:
            seq = await self._next_seq(db, event.run_id)
            await db.execute(
                """INSERT INTO events (run_id, seq, at, type, stage, message, payload_json)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    event.run_id, seq,
                    event.at.isoformat(),
                    event.type.value,
                    event.stage.value,
                    event.message,
                    json.dumps(event.payload),
                ),
            )
            await db.commit()
            # Retrieve the autoincrement id
            async with db.execute(
                "SELECT id FROM events WHERE run_id=? AND seq=?", (event.run_id, seq)
            ) as cur:
                row = await cur.fetchone()
        finally:
            await db.close()
        return RunEvent(
            id=row["id"],
            run_id=event.run_id,
            seq=seq,
            at=event.at,
            type=event.type,
            stage=event.stage,
            message=event.message,
            payload=event.payload,
        )

    async def list_since(self, run_id: str, since_seq: int = 0) -> list[RunEvent]:
        """Return all events for run_id with seq > since_seq, ordered by seq."""
        db = await get_db()
        try:
            async with db.execute(
                "SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq",
                (run_id, since_seq),
            ) as cur:
                rows = await cur.fetchall()
        finally:
            await db.close()
        return [_row_to_event(dict(r)) for r in rows]

    async def _next_seq(self, db: aiosqlite.Connection, run_id: str) -> int:
        async with db.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id=?", (run_id,)
        ) as cur:
            row = await cur.fetchone()
        return row[0]


def _row_to_event(row: dict) -> RunEvent:
    return RunEvent(
        id=row["id"],
        run_id=row["run_id"],
        seq=row["seq"],
        at=datetime.fromisoformat(row["at"]),
        type=RunEventType(row["type"]),
        stage=RunStatus(row["stage"]),
        message=row["message"],
        payload=json.loads(row.get("payload_json") or "{}"),
    )
