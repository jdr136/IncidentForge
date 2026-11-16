"""
Run state machine — enforces valid transitions.
Import from contracts.types for RunStatus and ALLOWED_TRANSITIONS.
"""
from __future__ import annotations

import os
import sys

# Allow importing contracts from the project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))

from contracts.types import ALLOWED_TRANSITIONS, TERMINAL_STATUSES, RunStatus


class InvalidTransitionError(Exception):
    def __init__(self, current: RunStatus, target: RunStatus) -> None:
        super().__init__(f"Cannot transition from {current} to {target}")
        self.current = current
        self.target = target


def transition(current: RunStatus, target: RunStatus) -> RunStatus:
    """
    Validate and return the new status.
    Raises InvalidTransitionError if the transition is not permitted.
    """
    if current in TERMINAL_STATUSES:
        raise InvalidTransitionError(current, target)
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidTransitionError(current, target)
    return target


def is_terminal(status: RunStatus) -> bool:
    return status in TERMINAL_STATUSES
