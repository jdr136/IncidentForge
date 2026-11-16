"""Persist and retrieve evaluation records."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

from app.persistence.database import get_db

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..'))
from contracts.types import ComponentScore, EvaluationResult


class EvaluationRepository:
    async def save(self, result: EvaluationResult) -> None:
        db = await get_db()
        try:
            await db.execute(
                """INSERT OR REPLACE INTO evaluations
                   (run_id, rubric_version, component_scores_json, verification_status, completed_at)
                   VALUES (?,?,?,?,?)""",
                (
                    result.run_id,
                    result.rubric_version,
                    json.dumps([s.model_dump() for s in result.component_scores]),
                    result.verification_status,
                    result.completed_at.isoformat() if result.completed_at else None,
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def get(self, run_id: str) -> EvaluationResult | None:
        db = await get_db()
        try:
            async with db.execute(
                "SELECT * FROM evaluations WHERE run_id=?", (run_id,)
            ) as cur:
                row = await cur.fetchone()
        finally:
            await db.close()
        if row is None:
            return None
        row = dict(row)
        scores = [ComponentScore(**s) for s in json.loads(row["component_scores_json"] or "[]")]
        return EvaluationResult(
            run_id=row["run_id"],
            rubric_version=row["rubric_version"],
            component_scores=scores,
            verification_status=row.get("verification_status"),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row.get("completed_at") else None,
            diagnosis_valid=True,
        )
