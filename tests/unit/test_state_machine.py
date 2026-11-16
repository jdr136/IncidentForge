"""Tests for the run state machine."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services", "api"))

import pytest
from app.domain.state_machine import InvalidTransitionError, is_terminal, transition

from contracts.types import RunStatus


def test_valid_transition():
    result = transition(RunStatus.QUEUED, RunStatus.PREPARING)
    assert result == RunStatus.PREPARING


def test_valid_full_happy_path():
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


def test_cannot_skip_state():
    with pytest.raises(InvalidTransitionError):
        transition(RunStatus.QUEUED, RunStatus.INVESTIGATING)


def test_cannot_transition_from_terminal():
    with pytest.raises(InvalidTransitionError):
        transition(RunStatus.COMPLETED, RunStatus.QUEUED)


def test_any_active_state_can_reach_failed():
    for status in [
        RunStatus.PREPARING, RunStatus.BASELINE_READY,
        RunStatus.INJECTING, RunStatus.INVESTIGATING,
    ]:
        result = transition(status, RunStatus.FAILED)
        assert result == RunStatus.FAILED


def test_is_terminal():
    assert is_terminal(RunStatus.COMPLETED)
    assert is_terminal(RunStatus.FAILED)
    assert is_terminal(RunStatus.CANCELLED)
    assert not is_terminal(RunStatus.INVESTIGATING)
    assert not is_terminal(RunStatus.QUEUED)
