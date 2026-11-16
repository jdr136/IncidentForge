"""Run lifecycle endpoints."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response  # noqa: F401
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from ulid import ULID

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", ".."))
from app.api.dependencies import (
    get_kubernetes_adapter,
    get_provider_adapter,
    get_scenario_registry,
    get_tool_gateway,
)
from app.domain.event_bus import event_bus
from app.domain.orchestrator import run_scenario, run_verification
from app.persistence.event_repository import EventRepository
from app.persistence.models import RunRecord
from app.persistence.run_repository import RunRepository
from app.scenarios.registry import ScenarioNotFound, ScenarioRegistry
from contracts.types import TERMINAL_STATUSES, RunEvent, RunEventType, RunStatus

logger = logging.getLogger(__name__)
router = APIRouter(tags=["runs"])

# In-process lock: prevents two simultaneous POST /runs in the same process.
# DB state is the durable lock; this prevents the TOCTOU race within one process.
_lab_lock = asyncio.Lock()


class StartRunRequest(BaseModel):
    scenario_id: str
    scenario_version: int = 1
    provider: str
    model: str
    seed: int | None = None
    temperature: float = 0.0


def _run_to_response(run: RunRecord) -> dict:
    return {
        "run_id": run.id,
        "scenario_id": run.scenario_id,
        "scenario_version": run.scenario_version,
        "provider": run.provider,
        "model": run.model,
        "status": run.status.value,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "error_code": run.error_code,
        "events_url": f"/api/v1/runs/{run.id}/events",
    }


@router.post("/runs", status_code=202)
async def start_run(
    request: StartRunRequest,
    registry: ScenarioRegistry = Depends(get_scenario_registry),
) -> dict:
    """
    Start a new lab run. Returns 202 Accepted.
    Returns 409 LAB_BUSY if another run is active.
    Returns 404 if the scenario is not found.
    """
    # Validate scenario exists
    try:
        registry.get_public(request.scenario_id, request.scenario_version)
    except ScenarioNotFound:
        raise HTTPException(
            status_code=404,
            detail={"code": "SCENARIO_NOT_FOUND",
                    "message": f"Scenario '{request.scenario_id}' v{request.scenario_version} not found"},
        )

    run_repo = RunRepository()
    event_repo = EventRepository()

    async with _lab_lock:
        # Check for active run (DB-backed lock)
        active = await run_repo.get_active()
        if active is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "LAB_BUSY",
                        "message": f"Run {active.id} is currently active (status: {active.status.value})"},
            )

        # Create the run record
        now = datetime.now(UTC)
        run_id = f"run_{ULID()}"
        run = RunRecord(
            id=run_id,
            scenario_id=request.scenario_id,
            scenario_version=request.scenario_version,
            provider=request.provider,
            model=request.model,
            seed=request.seed,
            temperature=request.temperature,
            status=RunStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )
        await run_repo.create(run)

        # Emit run_created event
        created_event = await event_repo.append(RunEvent(
            id=0, run_id=run_id, seq=0,
            at=now, type=RunEventType.RUN_CREATED,
            stage=RunStatus.QUEUED,
            message=f"Run created for scenario '{request.scenario_id}'",
            payload={"scenario_id": request.scenario_id,
                     "provider": request.provider,
                     "model": request.model},
        ))
        await event_bus.publish(created_event)

    # Start background orchestration task (non-blocking)
    asyncio.create_task(
        run_scenario(
            run_id, request.scenario_id, run_repo, event_repo,
            scenario_adapter=get_kubernetes_adapter(),
            tool_gateway=get_tool_gateway(),
            provider_adapter=get_provider_adapter(),
        ),
        name=f"orchestrate-{run_id}",
    )

    return _run_to_response(run) | {"events_url": f"/api/v1/runs/{run_id}/events"}


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    run = await RunRepository().get(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "RUN_NOT_FOUND", "message": f"Run {run_id} not found"},
        )
    return _run_to_response(run)


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request) -> EventSourceResponse:
    """
    Server-Sent Events stream for a run.

    On connect: replays all historical events (seq > Last-Event-ID header),
    then streams live events until the run reaches a terminal state.
    """
    run_repo = RunRepository()
    event_repo = EventRepository()

    run = await run_repo.get(run_id)
    if run is None:
        raise HTTPException(status_code=404,
                            detail={"code": "RUN_NOT_FOUND",
                                    "message": f"Run {run_id} not found"})

    last_event_id_header = request.headers.get("last-event-id", "0")
    try:
        since_seq = int(last_event_id_header)
    except (ValueError, TypeError):
        since_seq = 0

    async def event_generator():
        # 1. Replay historical events
        historical = await event_repo.list_since(run_id, since_seq)
        for event in historical:
            yield {"id": str(event.seq), "data": event.model_dump_json()}

        # 2. If run is already terminal, we're done
        current = await run_repo.get(run_id)
        if current and current.status in TERMINAL_STATUSES:
            return

        # 3. Stream live events
        async for event in event_bus.subscribe(run_id):
            if await request.is_disconnected():
                break
            yield {"id": str(event.seq), "data": event.model_dump_json()}

    return EventSourceResponse(event_generator())


@router.post("/runs/{run_id}/verify", status_code=202)
async def verify_run(run_id: str) -> dict:
    """
    Approve local fix verification. Only valid when status=AWAITING_VERIFICATION.
    """
    run_repo = RunRepository()
    event_repo = EventRepository()
    run = await run_repo.get(run_id)
    if run is None:
        raise HTTPException(status_code=404,
                            detail={"code": "RUN_NOT_FOUND",
                                    "message": f"Run {run_id} not found"})
    if run.status != RunStatus.AWAITING_VERIFICATION:
        raise HTTPException(
            status_code=409,
            detail={"code": "INVALID_STATE",
                    "message": f"Run must be in AWAITING_VERIFICATION state (current: {run.status.value})"},
        )
    # Retrieve the diagnosis from the diagnosed event for real verification
    from app.persistence.event_repository import EventRepository as _ER
    all_events = await _ER().list_since(run_id, 0)
    diagnosis_dict: dict | None = None
    for ev in reversed(all_events):
        if ev.type.value == "diagnosis_completed" and ev.payload.get("diagnosis"):
            diagnosis_dict = ev.payload["diagnosis"]
            break
    asyncio.create_task(
        run_verification(
            run_id, run_repo, event_repo,
            scenario_adapter=get_kubernetes_adapter(),
            diagnosis_dict=diagnosis_dict,
        ),
        name=f"verify-{run_id}",
    )
    return {"run_id": run_id, "status": "VERIFYING", "message": "Verification started"}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict:
    """Cancel an active run. Idempotent."""
    run_repo = RunRepository()
    event_repo = EventRepository()
    run = await run_repo.get(run_id)
    if run is None:
        raise HTTPException(status_code=404,
                            detail={"code": "RUN_NOT_FOUND",
                                    "message": f"Run {run_id} not found"})
    if run.status in TERMINAL_STATUSES:
        return {"run_id": run_id, "status": run.status.value,
                "message": "Run already in terminal state"}
    await run_repo.update_status(run_id, RunStatus.CANCELLED)
    cancelled_event = await event_repo.append(RunEvent(
        id=0, run_id=run_id, seq=0,
        at=datetime.now(UTC),
        type=RunEventType.STAGE_CHANGED,
        stage=RunStatus.CANCELLED,
        message="Run cancelled",
        payload={},
    ))
    await event_bus.publish(cancelled_event)
    return {"run_id": run_id, "status": "CANCELLED"}


@router.get("/runs")
async def list_runs() -> list[dict]:
    """List recent runs (for benchmark history page)."""
    runs = await RunRepository().list_recent(limit=50)
    return [_run_to_response(r) for r in runs]


@router.get("/runs/{run_id}/export")
async def export_run(run_id: str) -> dict:
    """
    Download a portable JSON run record.
    Does not include secrets, private answer keys, or raw credentials.
    """
    run = await RunRepository().get(run_id)
    if run is None:
        raise HTTPException(status_code=404,
                            detail={"code": "RUN_NOT_FOUND", "message": f"Run {run_id} not found"})
    event_repo = EventRepository()
    events = await event_repo.list_since(run_id, 0)
    # Filter out any payload fields that could contain private data
    safe_events = []
    for ev in events:
        payload = {k: v for k, v in ev.payload.items()
                   if k not in {"private_expected", "setup", "fault", "fixture"}}
        safe_events.append({
            "seq": ev.seq, "at": ev.at.isoformat(),
            "type": ev.type.value, "stage": ev.stage.value,
            "message": ev.message, "payload": payload,
        })
    return {
        "export_version": "1.0",
        "run_id": run.id,
        "scenario_id": run.scenario_id,
        "scenario_version": run.scenario_version,
        "provider": run.provider,
        "model": run.model,
        "status": run.status.value,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "error_code": run.error_code,
        "events": safe_events,
        "_note": "Private scenario data, credentials and raw evidence excluded from this export.",
    }
