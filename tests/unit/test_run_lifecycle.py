"""Tests for the run lifecycle engine (IF-003)."""
from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

# Ensure paths are set up (also handled by conftest.py and PYTHONPATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "api"))

from app.domain.event_bus import RunEventBus
from app.domain.state_machine import InvalidTransitionError, transition
from app.persistence.event_repository import EventRepository
from app.persistence.models import RunRecord
from app.persistence.run_repository import RunRepository

from contracts.types import (
    TERMINAL_STATUSES,
    RunEvent,
    RunEventType,
    RunStatus,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_run(run_id: str = "run_test", status: RunStatus = RunStatus.QUEUED) -> RunRecord:
    now = datetime.now(UTC)
    return RunRecord(
        id=run_id,
        scenario_id="test-scenario",
        scenario_version=1,
        provider="test-provider",
        model="test-model",
        seed=None,
        temperature=0.0,
        status=status,
        created_at=now,
        updated_at=now,
    )


def _make_event(
    run_id: str = "run_test",
    seq: int = 0,
    stage: RunStatus = RunStatus.QUEUED,
    event_type: RunEventType = RunEventType.STAGE_CHANGED,
) -> RunEvent:
    return RunEvent(
        id=0,
        run_id=run_id,
        seq=seq,
        at=datetime.now(UTC),
        type=event_type,
        stage=stage,
        message="test event",
        payload={},
    )


# ---------------------------------------------------------------------------
# RunRepository tests
# ---------------------------------------------------------------------------

async def test_run_repo_create_and_get(tmp_db):
    repo = RunRepository()
    run = _make_run("run_001")
    created = await repo.create(run)
    assert created.id == "run_001"

    retrieved = await repo.get("run_001")
    assert retrieved is not None
    assert retrieved.id == "run_001"
    assert retrieved.status == RunStatus.QUEUED
    assert retrieved.scenario_id == "test-scenario"
    assert retrieved.provider == "test-provider"


async def test_run_repo_get_nonexistent(tmp_db):
    repo = RunRepository()
    result = await repo.get("does-not-exist")
    assert result is None


async def test_run_repo_get_active_none_when_empty(tmp_db):
    repo = RunRepository()
    result = await repo.get_active()
    assert result is None


async def test_run_repo_get_active_returns_queued_run(tmp_db):
    repo = RunRepository()
    await repo.create(_make_run("run_002", RunStatus.QUEUED))

    active = await repo.get_active()
    assert active is not None
    assert active.id == "run_002"


async def test_run_repo_get_active_ignores_terminal_runs(tmp_db):
    repo = RunRepository()
    # Insert a COMPLETED run directly (create doesn't enforce state machine)
    await repo.create(_make_run("run_003", RunStatus.COMPLETED))

    active = await repo.get_active()
    assert active is None


async def test_run_repo_get_active_ignores_all_terminal_statuses(tmp_db):
    repo = RunRepository()
    for i, status in enumerate(TERMINAL_STATUSES):
        await repo.create(_make_run(f"run_term_{i}", status))

    active = await repo.get_active()
    assert active is None


async def test_run_repo_update_status(tmp_db):
    repo = RunRepository()
    await repo.create(_make_run("run_004"))

    await repo.update_status("run_004", RunStatus.PREPARING)

    updated = await repo.get("run_004")
    assert updated is not None
    assert updated.status == RunStatus.PREPARING


async def test_run_repo_update_status_with_error_code(tmp_db):
    repo = RunRepository()
    await repo.create(_make_run("run_005"))

    await repo.update_status("run_005", RunStatus.FAILED, error_code="SOME_ERROR")

    updated = await repo.get("run_005")
    assert updated.status == RunStatus.FAILED
    assert updated.error_code == "SOME_ERROR"


async def test_run_repo_mark_all_interrupted(tmp_db):
    repo = RunRepository()

    # Non-terminal runs
    await repo.create(_make_run("run_q", RunStatus.QUEUED))
    await repo.create(_make_run("run_p", RunStatus.PREPARING))
    await repo.create(_make_run("run_i", RunStatus.INVESTIGATING))

    # Terminal runs (should be unaffected)
    await repo.create(_make_run("run_c", RunStatus.COMPLETED))
    await repo.create(_make_run("run_f", RunStatus.FAILED))

    interrupted_ids = await repo.mark_all_interrupted()

    assert set(interrupted_ids) == {"run_q", "run_p", "run_i"}

    # Verify DB state
    assert (await repo.get("run_q")).status == RunStatus.INTERRUPTED
    assert (await repo.get("run_p")).status == RunStatus.INTERRUPTED
    assert (await repo.get("run_i")).status == RunStatus.INTERRUPTED
    assert (await repo.get("run_c")).status == RunStatus.COMPLETED  # unchanged
    assert (await repo.get("run_f")).status == RunStatus.FAILED     # unchanged


async def test_run_repo_mark_all_interrupted_empty(tmp_db):
    repo = RunRepository()
    result = await repo.mark_all_interrupted()
    assert result == []


# ---------------------------------------------------------------------------
# EventRepository tests
# ---------------------------------------------------------------------------

async def test_event_repo_append_seq_starts_at_1(tmp_db):
    run_repo = RunRepository()
    event_repo = EventRepository()

    await run_repo.create(_make_run("run_e01"))

    appended = await event_repo.append(_make_event("run_e01"))

    assert appended.seq == 1
    assert appended.id > 0
    assert appended.run_id == "run_e01"


async def test_event_repo_append_seq_increments(tmp_db):
    run_repo = RunRepository()
    event_repo = EventRepository()

    await run_repo.create(_make_run("run_e02"))

    e1 = await event_repo.append(_make_event("run_e02"))
    e2 = await event_repo.append(_make_event("run_e02"))
    e3 = await event_repo.append(_make_event("run_e02"))

    assert e1.seq == 1
    assert e2.seq == 2
    assert e3.seq == 3


async def test_event_repo_seq_independent_per_run(tmp_db):
    run_repo = RunRepository()
    event_repo = EventRepository()

    await run_repo.create(_make_run("run_ea"))
    await run_repo.create(_make_run("run_eb"))

    ea1 = await event_repo.append(_make_event("run_ea"))
    eb1 = await event_repo.append(_make_event("run_eb"))
    ea2 = await event_repo.append(_make_event("run_ea"))

    assert ea1.seq == 1
    assert eb1.seq == 1  # independent counter
    assert ea2.seq == 2


async def test_event_repo_list_since_zero_returns_all(tmp_db):
    run_repo = RunRepository()
    event_repo = EventRepository()

    await run_repo.create(_make_run("run_e03"))
    for _ in range(3):
        await event_repo.append(_make_event("run_e03"))

    events = await event_repo.list_since("run_e03", since_seq=0)
    assert len(events) == 3
    assert [e.seq for e in events] == [1, 2, 3]


async def test_event_repo_list_since_filters_correctly(tmp_db):
    run_repo = RunRepository()
    event_repo = EventRepository()

    await run_repo.create(_make_run("run_e04"))
    for _ in range(5):
        await event_repo.append(_make_event("run_e04"))

    # since_seq=2 → returns events with seq > 2 (i.e., 3, 4, 5)
    events = await event_repo.list_since("run_e04", since_seq=2)
    assert len(events) == 3
    assert events[0].seq == 3
    assert events[2].seq == 5


async def test_event_repo_list_since_empty_when_all_seen(tmp_db):
    run_repo = RunRepository()
    event_repo = EventRepository()

    await run_repo.create(_make_run("run_e05"))
    for _ in range(3):
        await event_repo.append(_make_event("run_e05"))

    events = await event_repo.list_since("run_e05", since_seq=3)
    assert events == []


async def test_event_repo_preserves_payload(tmp_db):
    run_repo = RunRepository()
    event_repo = EventRepository()

    await run_repo.create(_make_run("run_e06"))
    evt = RunEvent(
        id=0, run_id="run_e06", seq=0,
        at=datetime.now(UTC),
        type=RunEventType.TOOL_OBSERVATION,
        stage=RunStatus.INVESTIGATING,
        message="tool call",
        payload={"tool": "k8s.list_pods", "count": 3},
    )
    appended = await event_repo.append(evt)

    events = await event_repo.list_since("run_e06", since_seq=0)
    assert len(events) == 1
    assert events[0].payload == {"tool": "k8s.list_pods", "count": 3}
    assert appended.payload == {"tool": "k8s.list_pods", "count": 3}


# ---------------------------------------------------------------------------
# RunEventBus tests
# ---------------------------------------------------------------------------

async def test_event_bus_publish_delivers_to_subscriber():
    bus = RunEventBus()
    received: list[RunEvent] = []

    async def collect():
        async for event in bus.subscribe("run_bus_1"):
            received.append(event)
            # Terminal stage causes the loop to stop

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)  # yield to let subscriber register

    # Publish a terminal event (stops the subscriber loop)
    event = _make_event("run_bus_1", seq=1, stage=RunStatus.CANCELLED)
    await bus.publish(event)

    await asyncio.wait_for(task, timeout=2.0)

    assert len(received) == 1
    assert received[0].stage == RunStatus.CANCELLED


async def test_event_bus_subscribe_stops_on_terminal_stage():
    bus = RunEventBus()
    received: list[RunEvent] = []

    async def collect():
        async for event in bus.subscribe("run_bus_2"):
            received.append(event)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)

    e1 = _make_event("run_bus_2", seq=1, stage=RunStatus.PREPARING)
    e2 = _make_event("run_bus_2", seq=2, stage=RunStatus.COMPLETED)  # terminal

    await bus.publish(e1)
    await asyncio.sleep(0)  # let subscriber process e1
    await bus.publish(e2)

    await asyncio.wait_for(task, timeout=2.0)

    assert len(received) == 2
    assert received[0].stage == RunStatus.PREPARING
    assert received[1].stage == RunStatus.COMPLETED


async def test_event_bus_no_cross_run_delivery():
    """Events for run A must not reach subscribers of run B."""
    bus = RunEventBus()
    run_b_received: list[RunEvent] = []

    async def collect_b():
        async for event in bus.subscribe("run_b"):
            run_b_received.append(event)

    task = asyncio.create_task(collect_b())
    await asyncio.sleep(0)

    # Publish to run_a only
    event_a = _make_event("run_a", seq=1, stage=RunStatus.CANCELLED)
    await bus.publish(event_a)
    await asyncio.sleep(0)

    # Now terminate run_b
    event_b = _make_event("run_b", seq=1, stage=RunStatus.COMPLETED)
    await bus.publish(event_b)

    await asyncio.wait_for(task, timeout=2.0)

    assert len(run_b_received) == 1
    assert run_b_received[0].stage == RunStatus.COMPLETED


async def test_event_bus_multiple_subscribers_same_run():
    """All subscribers receive the event."""
    bus = RunEventBus()
    r1: list[RunEvent] = []
    r2: list[RunEvent] = []

    async def collect1():
        async for e in bus.subscribe("run_multi"):
            r1.append(e)

    async def collect2():
        async for e in bus.subscribe("run_multi"):
            r2.append(e)

    t1 = asyncio.create_task(collect1())
    t2 = asyncio.create_task(collect2())
    await asyncio.sleep(0)

    event = _make_event("run_multi", seq=1, stage=RunStatus.FAILED)  # terminal
    await bus.publish(event)

    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=2.0)

    assert len(r1) == 1
    assert len(r2) == 1


# ---------------------------------------------------------------------------
# State machine tests
# ---------------------------------------------------------------------------

def test_state_machine_full_happy_path():
    """QUEUED → ... → COMPLETED traversal via every intermediate state."""
    status = RunStatus.QUEUED
    path = [
        RunStatus.PREPARING,
        RunStatus.BASELINE_READY,
        RunStatus.INJECTING,
        RunStatus.FAULT_CONFIRMED,
        RunStatus.INVESTIGATING,
        RunStatus.DIAGNOSED,
        RunStatus.AWAITING_VERIFICATION,
        RunStatus.VERIFYING,
        RunStatus.COMPLETED,
    ]
    for next_status in path:
        status = transition(status, next_status)
    assert status == RunStatus.COMPLETED


def test_state_machine_invalid_skip():
    with pytest.raises(InvalidTransitionError):
        transition(RunStatus.QUEUED, RunStatus.COMPLETED)


def test_state_machine_terminal_no_outbound():
    with pytest.raises(InvalidTransitionError):
        transition(RunStatus.COMPLETED, RunStatus.QUEUED)


def test_state_machine_cancel_from_active():
    result = transition(RunStatus.PREPARING, RunStatus.CANCELLED)
    assert result == RunStatus.CANCELLED


# ---------------------------------------------------------------------------
# API endpoint tests (via httpx AsyncClient)
# ---------------------------------------------------------------------------

class _MockRegistry:
    """Minimal scenario registry mock for route tests."""

    def get_public(self, scenario_id: str, version: int):  # noqa: ANN001
        if scenario_id == "test-scenario" and version == 1:
            return object()  # just needs to not raise
        from app.scenarios.registry import ScenarioNotFound
        raise ScenarioNotFound(scenario_id, version)


@pytest.fixture
async def api_client(tmp_db):
    """
    Async httpx client wired to the FastAPI app with:
    - a temporary SQLite database (via tmp_db)
    - the scenario registry mocked to avoid filesystem loading
    """
    import app.persistence.database as db_mod

    # Ensure tables exist in the temp DB
    await db_mod.init_db()

    from app.api.dependencies import get_scenario_registry
    from main import app  # services/api/main.py via PYTHONPATH

    app.dependency_overrides[get_scenario_registry] = lambda: _MockRegistry()

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        # Cancel any background orchestrator / verification tasks
        for task in asyncio.all_tasks():
            name = task.get_name()
            if name.startswith(("orchestrate-", "verify-")):
                task.cancel()
        # Allow cancelled tasks to terminate
        await asyncio.sleep(0)


_START_PAYLOAD = {
    "scenario_id": "test-scenario",
    "scenario_version": 1,
    "provider": "test-provider",
    "model": "test-model",
}


async def test_post_runs_returns_202(api_client):
    r = await api_client.post("/api/v1/runs", json=_START_PAYLOAD)
    assert r.status_code == 202
    body = r.json()
    assert body["run_id"].startswith("run_")
    assert body["status"] == "QUEUED"
    assert body["scenario_id"] == "test-scenario"
    assert "/api/v1/runs/" in body["events_url"]


async def test_post_runs_creates_run_in_db(api_client):
    r = await api_client.post("/api/v1/runs", json=_START_PAYLOAD)
    run_id = r.json()["run_id"]

    r2 = await api_client.get(f"/api/v1/runs/{run_id}")
    assert r2.status_code == 200
    assert r2.json()["run_id"] == run_id


async def test_post_runs_409_lab_busy(api_client):
    # First run is accepted
    r1 = await api_client.post("/api/v1/runs", json=_START_PAYLOAD)
    assert r1.status_code == 202

    # Second run is blocked while the first is active
    r2 = await api_client.post("/api/v1/runs", json=_START_PAYLOAD)
    assert r2.status_code == 409
    detail = r2.json()["detail"]
    assert detail["code"] == "LAB_BUSY"


async def test_post_runs_404_unknown_scenario(api_client):
    r = await api_client.post("/api/v1/runs", json={
        **_START_PAYLOAD,
        "scenario_id": "no-such-scenario",
    })
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "SCENARIO_NOT_FOUND"


async def test_get_run_200(api_client):
    r_post = await api_client.post("/api/v1/runs", json=_START_PAYLOAD)
    run_id = r_post.json()["run_id"]

    r_get = await api_client.get(f"/api/v1/runs/{run_id}")
    assert r_get.status_code == 200
    body = r_get.json()
    assert body["run_id"] == run_id
    # Status may have advanced from QUEUED by the time we poll —
    # the background orchestrator task starts immediately.
    # Assert the run exists and is in a valid (non-error) state.
    assert body["status"] in (
        "QUEUED", "PREPARING", "BASELINE_READY", "INJECTING",
        "FAULT_CONFIRMED", "INVESTIGATING", "DIAGNOSED",
        "AWAITING_VERIFICATION", "VERIFYING",
    ), f"Unexpected status: {body['status']}"


async def test_get_run_404(api_client):
    r = await api_client.get("/api/v1/runs/nonexistent_run_id")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "RUN_NOT_FOUND"


async def test_verify_run_409_wrong_state(api_client, tmp_db):
    """verify endpoint returns 409 when run is not in AWAITING_VERIFICATION."""
    # Create a run directly in QUEUED state
    repo = RunRepository()
    await repo.create(_make_run("run_verify_test", RunStatus.QUEUED))

    r = await api_client.post("/api/v1/runs/run_verify_test/verify")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "INVALID_STATE"


async def test_verify_run_404_unknown(api_client):
    r = await api_client.post("/api/v1/runs/no-such-run/verify")
    assert r.status_code == 404


async def test_cancel_run_200(api_client):
    r_post = await api_client.post("/api/v1/runs", json=_START_PAYLOAD)
    run_id = r_post.json()["run_id"]

    r_cancel = await api_client.post(f"/api/v1/runs/{run_id}/cancel")
    assert r_cancel.status_code == 200
    assert r_cancel.json()["status"] == "CANCELLED"


async def test_cancel_run_idempotent(api_client):
    """Cancel on an already-terminal run returns 200 with idempotent message."""
    # Insert the run already in CANCELLED state; no orchestrator is started.
    repo = RunRepository()
    await repo.create(_make_run("run_cancel_idem", RunStatus.CANCELLED))

    r1 = await api_client.post("/api/v1/runs/run_cancel_idem/cancel")
    r2 = await api_client.post("/api/v1/runs/run_cancel_idem/cancel")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json()["message"] == "Run already in terminal state"


async def test_cancel_run_unblocks_lab(api_client, tmp_db):
    """With a CANCELLED run in DB, POST /runs starts a new run (lab is free)."""
    # Seed DB with a run already in terminal state; no orchestrator to race with.
    repo = RunRepository()
    await repo.create(_make_run("run_prev_cancelled", RunStatus.CANCELLED))

    r = await api_client.post("/api/v1/runs", json=_START_PAYLOAD)
    assert r.status_code == 202


async def test_cancel_run_404(api_client):
    r = await api_client.post("/api/v1/runs/no-such-run/cancel")
    assert r.status_code == 404


async def test_sse_events_200_for_terminal_run(api_client, tmp_db):
    """SSE endpoint closes immediately for a run already in terminal state."""
    repo = RunRepository()
    await repo.create(_make_run("run_sse_test", RunStatus.COMPLETED))

    r = await api_client.get("/api/v1/runs/run_sse_test/events")
    assert r.status_code == 200


async def test_sse_events_404_unknown_run(api_client):
    r = await api_client.get("/api/v1/runs/no-such-run/events")
    assert r.status_code == 404


async def test_list_runs_returns_empty(api_client):
    r = await api_client.get("/api/v1/runs")
    assert r.status_code == 200
    assert r.json() == []
