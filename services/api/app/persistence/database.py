"""SQLite database initialisation and connection management."""
from __future__ import annotations

import os
from pathlib import Path

import aiosqlite

DB_PATH = Path(os.environ.get("DB_PATH", "data/incidentforge.db"))

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    scenario_version INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    seed INTEGER,
    temperature REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'QUEUED',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error_code TEXT,
    environment_versions TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    at TEXT NOT NULL,
    type TEXT NOT NULL,
    stage TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (run_id, seq),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    truncated INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS diagnoses (
    run_id TEXT PRIMARY KEY,
    parsed_json TEXT NOT NULL,
    schema_valid INTEGER NOT NULL DEFAULT 0,
    validation_errors TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS evaluations (
    run_id TEXT PRIMARY KEY,
    rubric_version TEXT NOT NULL DEFAULT '1.0',
    component_scores_json TEXT NOT NULL DEFAULT '[]',
    verification_status TEXT,
    completed_at TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
"""


async def get_db() -> aiosqlite.Connection:
    """Return an open database connection. Caller must close it."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.executescript(CREATE_TABLES)
    return db


async def init_db() -> None:
    """Create tables if they do not exist. Called at startup."""
    db = await get_db()
    await db.close()
