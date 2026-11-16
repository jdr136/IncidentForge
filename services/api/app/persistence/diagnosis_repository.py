"""Persist and retrieve diagnosis records."""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

from app.persistence.database import get_db

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..'))
from contracts.types import Diagnosis


class DiagnosisRepository:
    async def save(self, run_id: str, diagnosis: Diagnosis) -> None:
        db = await get_db()
        try:
            await db.execute(
                """INSERT OR REPLACE INTO diagnoses
                   (run_id, parsed_json, schema_valid, validation_errors, created_at)
                   VALUES (?,?,?,?,?)""",
                (run_id, diagnosis.model_dump_json(),
                 1, json.dumps([]),
                 datetime.now(UTC).isoformat()),
            )
            await db.commit()
        finally:
            await db.close()

    async def get(self, run_id: str) -> Diagnosis | None:
        db = await get_db()
        try:
            async with db.execute(
                "SELECT parsed_json FROM diagnoses WHERE run_id=?", (run_id,)
            ) as cur:
                row = await cur.fetchone()
        finally:
            await db.close()
        if row is None:
            return None
        return Diagnosis.model_validate_json(row["parsed_json"])
