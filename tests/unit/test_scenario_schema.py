"""Validate the three MVP scenario YAMLs against the JSON Schema contract."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pathlib import Path

import jsonschema
import pytest
import yaml

SCHEMA_PATH = Path("contracts/schemas/scenario.v1.1.json")
SCENARIOS_DIR = Path("scenarios")


@pytest.fixture(scope="module")
def scenario_schema():
    return json.loads(SCHEMA_PATH.read_text())


def scenario_files():
    return list(SCENARIOS_DIR.rglob("scenario.yaml"))


@pytest.mark.parametrize("path", scenario_files(), ids=lambda p: p.parent.parent.name)
def test_scenario_validates_against_schema(path, scenario_schema):
    data = yaml.safe_load(path.read_text())
    jsonschema.validate(data, scenario_schema)


def test_private_fields_present(scenario_schema):
    """Every scenario has private_expected — the loader must guard access to it."""
    for path in scenario_files():
        data = yaml.safe_load(path.read_text())
        assert "private_expected" in data
        assert "allowed_root_cause_ids" in data["private_expected"]
        assert len(data["private_expected"]["allowed_root_cause_ids"]) >= 1


def test_no_scenario_missing_public_fields():
    for path in scenario_files():
        data = yaml.safe_load(path.read_text())
        assert "summary" in data["public"]
        assert "target" in data["public"]
