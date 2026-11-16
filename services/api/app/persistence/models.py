"""Persistence layer Pydantic models (database row representations)."""
from __future__ import annotations

import os
import sys
from datetime import datetime

from pydantic import BaseModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
from contracts.types import RunStatus


class RunRecord(BaseModel):
    id: str
    scenario_id: str
    scenario_version: int
    provider: str
    model: str
    seed: int | None
    temperature: float
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    error_code: str | None = None
    environment_versions: dict[str, str] = {}

    model_config = {"from_attributes": True}
