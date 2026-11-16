"""
Shared pytest fixtures for IncidentForge tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure project root and services/api are on the path for all tests
_PROJECT_ROOT = Path(__file__).resolve().parent
_API_ROOT = _PROJECT_ROOT / "services" / "api"
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_API_ROOT))


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """
    Provide a temporary SQLite database for each test.
    Patches the DB_PATH in persistence.database so tests
    don't share state.
    """
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    # Also patch the module-level constant if already imported
    try:
        import app.persistence.database as db_mod
        monkeypatch.setattr(db_mod, "DB_PATH", Path(db_file))
    except ImportError:
        pass
    yield db_file
