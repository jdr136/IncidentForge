"""EvidenceStore — persists and retrieves evidence items."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

from app.persistence.database import get_db

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..'))
from contracts.types import EvidenceItem

MAX_PAYLOAD_BYTES = 16 * 1024  # 16 KiB limit per spec §9

class EvidenceRepository:
    """Implements the EvidenceStore Protocol backed by SQLite."""

    async def save(self, item: EvidenceItem) -> None:
        payload_str = json.dumps(item.payload)
        db = await get_db()
        try:
            await db.execute(
                """INSERT OR IGNORE INTO evidence
                   (evidence_id, run_id, tool_name, captured_at, payload_json, sha256, truncated)
                   VALUES (?,?,?,?,?,?,?)""",
                (item.evidence_id, item.run_id, item.tool_name,
                 item.captured_at.isoformat(), payload_str,
                 item.sha256, int(item.truncated)),
            )
            await db.commit()
        finally:
            await db.close()

    async def list_by_run(self, run_id: str) -> list[EvidenceItem]:
        db = await get_db()
        try:
            async with db.execute(
                "SELECT * FROM evidence WHERE run_id=? ORDER BY captured_at",
                (run_id,),
            ) as cur:
                rows = await cur.fetchall()
        finally:
            await db.close()
        return [_row_to_item(dict(r)) for r in rows]

    async def validate_ids(self, run_id: str, claimed_ids: list[str]) -> list[str]:
        """Return claimed_ids that do NOT exist for this run."""
        if not claimed_ids:
            return []
        db = await get_db()
        try:
            placeholders = ",".join("?" * len(claimed_ids))
            async with db.execute(
                f"SELECT evidence_id FROM evidence WHERE run_id=? AND evidence_id IN ({placeholders})",
                [run_id] + list(claimed_ids),
            ) as cur:
                rows = await cur.fetchall()
        finally:
            await db.close()
        found = {r["evidence_id"] for r in rows}
        return [eid for eid in claimed_ids if eid not in found]

    async def next_seq(self, run_id: str) -> int:
        db = await get_db()
        try:
            async with db.execute(
                "SELECT COUNT(*) FROM evidence WHERE run_id=?", (run_id,)
            ) as cur:
                row = await cur.fetchone()
        finally:
            await db.close()
        return row[0] + 1  # 1-based


def _row_to_item(row: dict) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=row["evidence_id"],
        run_id=row["run_id"],
        tool_name=row["tool_name"],
        captured_at=datetime.fromisoformat(row["captured_at"]),
        payload=json.loads(row["payload_json"]),
        sha256=row["sha256"],
        truncated=bool(row["truncated"]),
    )
