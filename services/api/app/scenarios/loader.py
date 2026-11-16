"""
Scenario loader — reads YAML files, validates against JSON Schema,
and returns split public/private models.

The loader enforces:
1. Schema validation (jsonschema against scenario.v1.1.json)
2. Unknown schema_version rejection
3. No sensitive fields returned from public accessors
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import jsonschema
import yaml

from app.scenarios.models import (
    CheckSpecModel,
    ScenarioFault,
    ScenarioFull,
    ScenarioPrivate,
    ScenarioPrivateExpected,
    ScenarioPublicMeta,
    ScenarioSetup,
)

logger = logging.getLogger(__name__)

# Location of the JSON Schema relative to the project root.
# The loader resolves this from the contracts/ directory.
# loader.py lives at: <project_root>/services/api/app/scenarios/loader.py
# parents[4] = <project_root>
_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "contracts" / "schemas" / "scenario.v1.1.json"
_SUPPORTED_VERSIONS = {"1.1"}


class ScenarioLoadError(Exception):
    """Raised when a scenario file cannot be loaded or fails validation."""


def load_scenario(path: Path) -> ScenarioFull:
    """
    Load and validate one scenario YAML file.

    Raises ScenarioLoadError for:
    - File not found
    - Invalid YAML
    - JSON Schema validation failure
    - Unsupported schema_version
    """
    if not path.exists():
        raise ScenarioLoadError(f"Scenario file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ScenarioLoadError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ScenarioLoadError(f"Expected a YAML mapping in {path}")

    # Validate against JSON Schema
    try:
        schema = json.loads(_SCHEMA_PATH.read_text())
        jsonschema.validate(raw, schema)
    except jsonschema.ValidationError as exc:
        raise ScenarioLoadError(
            f"Schema validation failed for {path}: {exc.message}"
        ) from exc

    # Reject unsupported versions (defensive — schema enum also covers this)
    version = raw.get("schema_version", "")
    if version not in _SUPPORTED_VERSIONS:
        raise ScenarioLoadError(
            f"Unsupported schema_version '{version}' in {path}. "
            f"Supported: {_SUPPORTED_VERSIONS}"
        )

    return _parse(raw)


def _parse(raw: dict) -> ScenarioFull:
    """Convert raw YAML dict into typed ScenarioFull."""
    public = ScenarioPublicMeta(
        id=raw["id"],
        version=raw["version"],
        title=raw["title"],
        environment=raw["environment"],
        difficulty=raw["difficulty"],
        summary=raw["public"]["summary"],
        target=raw["public"]["target"],
    )

    setup = ScenarioSetup(
        fixture=raw["setup"]["fixture"],
        baseline_checks=[
            CheckSpecModel(**c) for c in raw["setup"]["baseline_checks"]
        ],
    )

    fault = ScenarioFault(
        action=raw["fault"]["action"],
        ref=raw["fault"]["ref"],
        parameters=raw["fault"].get("parameters", {}),
        manifestation_checks=[
            CheckSpecModel(**c) for c in raw["fault"]["manifestation_checks"]
        ],
    )

    expected = raw["private_expected"]
    private_expected = ScenarioPrivateExpected(
        allowed_root_cause_ids=expected["allowed_root_cause_ids"],
        required_evidence_types=expected["required_evidence_types"],
        allowed_remediations=expected["allowed_remediations"],
        post_fix_checks=[
            CheckSpecModel(**c) for c in expected["post_fix_checks"]
        ],
    )

    private = ScenarioPrivate(
        id=raw["id"],
        version=raw["version"],
        setup=setup,
        fault=fault,
        private_expected=private_expected,
    )

    return ScenarioFull(
        schema_version=raw["schema_version"],
        public=public,
        private=private,
    )


def discover_scenarios(scenarios_root: Path) -> list[Path]:
    """
    Walk a scenarios/ directory and return all scenario.yaml paths found.
    Expected layout: scenarios/<id>/v<n>/scenario.yaml
    """
    return sorted(scenarios_root.rglob("scenario.yaml"))
