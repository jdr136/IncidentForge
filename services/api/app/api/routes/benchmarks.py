"""Benchmark aggregate endpoints."""
from __future__ import annotations

import os
import sys

from fastapi import APIRouter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", ".."))
from app.persistence.run_repository import RunRepository
from contracts.types import RunStatus

router = APIRouter(tags=["benchmarks"])

# Statuses that represent eligible, completed evaluation runs
_ELIGIBLE = frozenset({RunStatus.COMPLETED})

# Statuses classified as environment / infrastructure failures (not agent failures)
_ENV_FAILURE = frozenset({RunStatus.UNSUPPORTED, RunStatus.INVALID_SCENARIO})

# Statuses classified as agent failures
_AGENT_FAILURE = frozenset({RunStatus.FAILED, RunStatus.TIMED_OUT})


@router.get("/benchmarks/summary")
async def benchmark_summary() -> dict:
    """
    Aggregate benchmark statistics across all recorded runs.
    Separates eligible verified runs from excluded/partial runs.
    Per spec §7.3: never impute zero for NOT_EVALUATED; never mix
    agent failures with environment failures in accuracy denominators.
    """
    all_runs = await RunRepository().list_recent(limit=1000)

    total = len(all_runs)
    eligible = [r for r in all_runs if r.status in _ELIGIBLE]
    agent_failures = [r for r in all_runs if r.status in _AGENT_FAILURE]
    env_failures = [r for r in all_runs if r.status in _ENV_FAILURE]
    interrupted = [r for r in all_runs if r.status == RunStatus.INTERRUPTED]
    cancelled = [r for r in all_runs if r.status == RunStatus.CANCELLED]

    # Group eligible runs by scenario
    by_scenario: dict[str, int] = {}
    for r in eligible:
        by_scenario[r.scenario_id] = by_scenario.get(r.scenario_id, 0) + 1

    return {
        "benchmark_version": "1.0",
        "runs": {
            "total": total,
            "eligible_completed": len(eligible),
            "agent_failures": len(agent_failures),
            "environment_failures": len(env_failures),
            "interrupted": len(interrupted),
            "cancelled": cancelled,
        },
        "by_scenario": by_scenario,
        "note": (
            "Scoring statistics require the evaluator (IF-007). "
            "This endpoint returns run counts only until evaluations are persisted."
        ),
    }
