"""
Scenario models — Pydantic representations of the scenario YAML schema.

PUBLIC fields may be serialized into API responses.
PRIVATE fields must NEVER appear in any HTTP response, prompt, or log.

The split is enforced by using separate model classes:
  ScenarioPublicMeta  — safe to return from GET /scenarios
  ScenarioPrivate     — only used internally by ScenarioAdapter and Evaluator
  ScenarioFull        — the complete loaded scenario (internal only)
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Shared sub-types (also defined here to avoid import loops)
# ---------------------------------------------------------------------------

class CheckSpecModel(BaseModel):
    type: str
    ref: str
    parameters: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# PUBLIC — safe for API responses
# ---------------------------------------------------------------------------

class ScenarioPublicMeta(BaseModel):
    """
    Everything a UI or investigator may see.
    No fixture paths, fault details, or expected answers.
    """
    id: str
    version: int
    title: str
    environment: str        # "kubernetes" | "aws"
    difficulty: str         # "beginner" | "intermediate" | "advanced"
    summary: str            # from public.summary
    target: str             # from public.target

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# PRIVATE — trusted code only (ScenarioAdapter, Evaluator)
# ---------------------------------------------------------------------------

class ScenarioSetup(BaseModel):
    fixture: str
    baseline_checks: list[CheckSpecModel]

    model_config = {"frozen": True}


class ScenarioFault(BaseModel):
    action: str
    ref: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    manifestation_checks: list[CheckSpecModel]

    model_config = {"frozen": True}


class ScenarioPrivateExpected(BaseModel):
    allowed_root_cause_ids: list[str]
    required_evidence_types: list[str]
    allowed_remediations: list[str]
    post_fix_checks: list[CheckSpecModel]

    model_config = {"frozen": True}


class ScenarioPrivate(BaseModel):
    """
    The complete private side of a scenario.
    Only accessible via ScenarioRegistry.get_private() — a trusted method
    that must never be called from a public API route handler.
    """
    id: str
    version: int
    setup: ScenarioSetup
    fault: ScenarioFault
    private_expected: ScenarioPrivateExpected

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# FULL (internal representation after loading)
# ---------------------------------------------------------------------------

class ScenarioFull(BaseModel):
    """
    Complete parsed scenario — only lives inside the registry.
    Never serialized to JSON for external consumers.
    """
    schema_version: str
    public: ScenarioPublicMeta
    private: ScenarioPrivate

    model_config = {"frozen": True}
