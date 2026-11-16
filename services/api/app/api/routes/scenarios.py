"""Scenario catalogue endpoints — public metadata only."""
from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_scenario_registry
from app.scenarios.models import ScenarioPublicMeta
from app.scenarios.registry import ScenarioNotFound, ScenarioRegistry

router = APIRouter(tags=["scenarios"])


@router.get("/scenarios", response_model=list[ScenarioPublicMeta])
async def list_scenarios(
    registry: ScenarioRegistry = Depends(get_scenario_registry),
) -> list[ScenarioPublicMeta]:
    """Return public scenario catalogue. All private fields excluded."""
    return registry.list_public()


@router.get("/scenarios/{scenario_id}", response_model=ScenarioPublicMeta)
async def get_scenario(
    scenario_id: str,
    version: int = 1,
    registry: ScenarioRegistry = Depends(get_scenario_registry),
) -> ScenarioPublicMeta:
    """Return public metadata for one scenario."""
    try:
        return registry.get_public(scenario_id, version)
    except ScenarioNotFound:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' v{version} not found")
