"""RunRepository — CRUD for the runs table."""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

from app.persistence.database import get_db
from app.persistence.models import RunRecord

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
from contracts.types import TERMINAL_STATUSES, RunStatus


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RunRepository:
    async def create(self, record: RunRecord) -> RunRecord:
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO runs (id, scenario_id, scenario_version, provider, model,
                   seed, temperature, status, created_at, updated_at, error_code, environment_versions)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.id, record.scenario_id, record.scenario_version,
                    record.provider, record.model, record.seed, record.temperature,
                    record.status.value, record.created_at.isoformat(),
                    record.updated_at.isoformat(), record.error_code,
                    json.dumps(record.environment_versions),
                ),
            )
            await db.commit()
        finally:
            await db.close()
        return record

    async def get(self, run_id: str) -> RunRecord | None:
        db = await get_db()
        try:
            async with db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)) as cur:
                row = await cur.fetchone()
        finally:
            await db.close()
        if row is None:
            return None
        return _row_to_record(dict(row))

    async def update_status(
        self, run_id: str, status: RunStatus, error_code: str | None = None
    ) -> None:
        db = await get_db()
        try:
            await db.execute(
                "UPDATE runs SET status=?, updated_at=?, error_code=? WHERE id=?",
                (status.value, _now(), error_code, run_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def get_active(self) -> RunRecord | None:
        """Return the run holding the lab lock (non-terminal status), or None."""
        terminal_placeholders = ",".join("?" * len(TERMINAL_STATUSES))
        terminal_values = [s.value for s in TERMINAL_STATUSES]
        db = await get_db()
        try:
            async with db.execute(
                f"SELECT * FROM runs WHERE status NOT IN ({terminal_placeholders}) LIMIT 1",
                terminal_values,
            ) as cur:
                row = await cur.fetchone()
        finally:
            await db.close()
        if row is None:
            return None
        return _row_to_record(dict(row))

    async def mark_all_interrupted(self) -> list[str]:
        """Startup recovery: mark any non-terminal runs as INTERRUPTED."""
        terminal_placeholders = ",".join("?" * len(TERMINAL_STATUSES))
        terminal_values = [s.value for s in TERMINAL_STATUSES]
        db = await get_db()
        try:
            async with db.execute(
                f"SELECT id FROM runs WHERE status NOT IN ({terminal_placeholders})",
                terminal_values,
            ) as cur:
                rows = await cur.fetchall()
            run_ids = [row["id"] for row in rows]
            if run_ids:
                await db.execute(
                    f"UPDATE runs SET status=?, updated_at=? WHERE status NOT IN ({terminal_placeholders})",
                    [RunStatus.INTERRUPTED.value, _now()] + terminal_values,
                )
                await db.commit()
        finally:
            await db.close()
        return run_ids


    async def list_recent(self, limit: int = 50, offset: int = 0) -> list[RunRecord]:
        """Return recent runs ordered by created_at descending."""
        db = await get_db()
        try:
            async with db.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ) as cur:
                rows = await cur.fetchall()
        finally:
            await db.close()
        return [_row_to_record(dict(r)) for r in rows]

    async def count(self) -> int:
        """Return total run count."""
        db = await get_db()
        try:
            async with db.execute("SELECT COUNT(*) FROM runs") as cur:
                row = await cur.fetchone()
        finally:
            await db.close()
        return row[0]


def _row_to_record(row: dict) -> RunRecord:
    return RunRecord(
        id=row["id"],
        scenario_id=row["scenario_id"],
        scenario_version=row["scenario_version"],
        provider=row["provider"],
        model=row["model"],
        seed=row["seed"],
        temperature=row["temperature"],
        status=RunStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        error_code=row.get("error_code"),
        environment_versions=json.loads(row.get("environment_versions") or "{}"),
    )
