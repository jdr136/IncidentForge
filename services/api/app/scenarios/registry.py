"""
Scenario Registry — implements the ScenarioRegistry Protocol.

The registry is the gatekeeper for scenario data:
  - Public methods return ScenarioPublicMeta only
  - Private methods return ScenarioPrivate and are for trusted callers only
  - Private methods MUST NOT be called from public API route handlers

The registry is loaded at application startup and held in-process memory.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.scenarios.loader import ScenarioLoadError, discover_scenarios, load_scenario
from app.scenarios.models import ScenarioFull, ScenarioPrivate, ScenarioPublicMeta

logger = logging.getLogger(__name__)

# Key: (scenario_id, version)
_ScenarioKey = tuple[str, int]


class ScenarioNotFound(Exception):
    def __init__(self, scenario_id: str, version: int) -> None:
        super().__init__(f"Scenario '{scenario_id}' v{version} not found")
        self.scenario_id = scenario_id
        self.version = version


class ScenarioRegistry:
    """
    In-memory registry of all loaded scenarios.

    Usage:
        registry = ScenarioRegistry.load(Path("scenarios/"))
        metas = registry.list_public()          # safe for API
        private = registry.get_private(...)     # trusted callers ONLY

    Implements the ScenarioRegistry Protocol defined in contracts/interfaces.py.
    """

    def __init__(self, scenarios: dict[_ScenarioKey, ScenarioFull]) -> None:
        self._scenarios = scenarios

    @classmethod
    def load(cls, scenarios_root: Path) -> ScenarioRegistry:
        """
        Discover and load all scenario YAML files under scenarios_root.
        Logs a warning for each file that fails to load; does not abort.
        Returns a registry with all successfully loaded scenarios.
        """
        loaded: dict[_ScenarioKey, ScenarioFull] = {}
        paths = discover_scenarios(scenarios_root)

        if not paths:
            logger.warning("No scenario files found under %s", scenarios_root)

        for path in paths:
            try:
                scenario = load_scenario(path)
                key: _ScenarioKey = (scenario.public.id, scenario.public.version)
                if key in loaded:
                    logger.warning(
                        "Duplicate scenario %s v%d — skipping %s",
                        key[0], key[1], path,
                    )
                    continue
                loaded[key] = scenario
                logger.info(
                    "Loaded scenario %s v%d (%s)",
                    scenario.public.id, scenario.public.version, scenario.public.title,
                )
            except ScenarioLoadError as exc:
                logger.warning("Failed to load scenario at %s: %s", path, exc)

        return cls(loaded)

    # ── Public API ────────────────────────────────────────────────────────────

    def list_public(self) -> list[ScenarioPublicMeta]:
        """
        Return public metadata for all loaded scenarios.
        Safe to serialize and return from GET /scenarios.
        """
        return [s.public for s in self._scenarios.values()]

    def get_public(self, scenario_id: str, version: int) -> ScenarioPublicMeta:
        """
        Return public metadata for one scenario.
        Raises ScenarioNotFound if the scenario is not loaded.
        """
        key = (scenario_id, version)
        scenario = self._scenarios.get(key)
        if scenario is None:
            raise ScenarioNotFound(scenario_id, version)
        return scenario.public

    # ── Private API (trusted callers only) ───────────────────────────────────

    def get_private(self, scenario_id: str, version: int) -> ScenarioPrivate:
        """
        Return the full private scenario data.

        !! TRUSTED CALLERS ONLY !!
        Must NOT be called from any public API route handler.
        Must NOT return this data in any HTTP response.
        Only the ScenarioAdapter and Evaluator may call this method.
        """
        key = (scenario_id, version)
        scenario = self._scenarios.get(key)
        if scenario is None:
            raise ScenarioNotFound(scenario_id, version)
        return scenario.private

    def __len__(self) -> int:
        return len(self._scenarios)
