"""
Tests for the OpenAI provider adapter.
All OpenAI API calls are mocked — no network requests.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'services', 'api'))
os.environ.setdefault("OPENAI_API_KEY", "test-key-not-real")
os.environ.setdefault("DB_PATH", "/tmp/test_openai_adapter.db")

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.investigator.providers.openai_adapter import (
    OpenAIProviderAdapter,
    _build_openai_tools,
    _parse_diagnosis,
)

from contracts.types import (
    EvidenceItem,
    IncidentSummary,
    RunContext,
    ToolDefinition,
)


def make_incident() -> IncidentSummary:
    return IncidentSummary(
        scenario_id="k8s-readiness-path",
        title="API pods never become ready",
        objective="Diagnose why the API deployment is not ready",
        environment="kubernetes",
    )


def make_context() -> RunContext:
    return RunContext.for_run(
        run_id="run_01TESTADAP",
        scenario_id="k8s-readiness-path",
        scenario_version=1,
    )


def make_tool() -> ToolDefinition:
    return ToolDefinition(
        name="k8s.list_pods",
        description="List pods",
        parameters_schema={"type": "object", "properties": {}},
    )


def _mock_tool_call_message(tool_name: str, arguments: dict, call_id: str = "call_1"):
    """Build a mock OpenAI message with a tool_call."""
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = tool_name
    tc.function.arguments = json.dumps(arguments)
    msg = MagicMock()
    msg.tool_calls = [tc]
    msg.content = None
    msg.model_dump.return_value = {"role": "assistant", "tool_calls": []}
    return msg


def _mock_completion(message):
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


# ── Tool building tests ──────────────────────────────────────────────────────

class TestBuildOpenAITools:
    def test_includes_submit_diagnosis(self):
        tools = _build_openai_tools([make_tool()], max_calls=12)
        names = [t["function"]["name"] for t in tools]
        assert "submit_diagnosis" in names

    def test_all_tools_included(self):
        tools = _build_openai_tools([make_tool()], max_calls=12)
        names = [t["function"]["name"] for t in tools]
        assert "k8s.list_pods" in names

    def test_submit_diagnosis_has_required_params(self):
        tools = _build_openai_tools([], max_calls=12)
        sd = next(t for t in tools if t["function"]["name"] == "submit_diagnosis")
        required = sd["function"]["parameters"]["required"]
        assert "root_cause_id" in required
        assert "evidence_ids" in required
        assert "confidence" in required


# ── Diagnosis parsing tests ──────────────────────────────────────────────────

class TestParseDiagnosis:
    def test_parses_full_diagnosis(self):
        args = {
            "root_cause_id": "K8S_READINESS_PATH_MISMATCH",
            "summary": "The readiness probe path is wrong.",
            "confidence": 0.9,
            "evidence_ids": ["ev_abc_0001"],
            "remediation": {
                "action_type": "k8s.set_readiness_path",
                "target_ref": "api",
                "parameters": {"path": "/healthz"},
            },
        }
        d = _parse_diagnosis(args)
        assert d.root_cause_id == "K8S_READINESS_PATH_MISMATCH"
        assert d.confidence == 0.9
        assert d.remediation is not None
        assert d.remediation.action_type == "k8s.set_readiness_path"

    def test_parses_unknown_diagnosis(self):
        args = {
            "root_cause_id": "UNKNOWN",
            "summary": "Not enough evidence.",
            "confidence": 0.0,
            "evidence_ids": [],
        }
        d = _parse_diagnosis(args)
        assert d.root_cause_id == "UNKNOWN"
        assert d.remediation is None


# ── Full investigation loop tests (mocked API) ───────────────────────────────

class TestOpenAIAdapterLoop:
    @pytest.fixture
    def adapter(self):
        return OpenAIProviderAdapter(model="gpt-4o", api_key="test-key")

    @pytest.fixture
    def mock_tool_gateway(self):
        gw = MagicMock()
        evidence = EvidenceItem(
            evidence_id="ev_01testadap_0001",
            run_id="run_01TESTADAP",
            tool_name="k8s.list_pods",
            captured_at=datetime.now(UTC),
            payload={"pods": [], "count": 0},
            sha256="hash",
            truncated=False,
        )
        gw.call = AsyncMock(return_value=evidence)
        gw.available_tools = MagicMock(return_value=[make_tool()])
        return gw

    def test_happy_path_calls_submit_diagnosis(self, adapter, mock_tool_gateway):
        """Model calls one tool then calls submit_diagnosis."""
        list_pods_msg = _mock_tool_call_message("k8s.list_pods", {}, "call_1")
        submit_msg = _mock_tool_call_message(
            "submit_diagnosis",
            {
                "root_cause_id": "K8S_READINESS_PATH_MISMATCH",
                "summary": "Readiness probe wrong.",
                "confidence": 0.9,
                "evidence_ids": ["ev_01testadap_0001"],
            },
            "call_2",
        )

        with patch.object(adapter._client.chat.completions, "create", new=AsyncMock(
            side_effect=[_mock_completion(list_pods_msg), _mock_completion(submit_msg)]
        )):
            result = asyncio.run(
                adapter.investigate(
                    incident=make_incident(),
                    tools=[make_tool()],
                    tool_gateway=mock_tool_gateway,
                    context=make_context(),
                    max_tool_calls=12,
                )
            )

        assert result.diagnosis.root_cause_id == "K8S_READINESS_PATH_MISMATCH"
        assert result.diagnosis.confidence == 0.9
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "k8s.list_pods"

    def test_budget_exhaustion_forces_unknown(self, adapter, mock_tool_gateway):
        """When budget = 1 remaining, force submit_diagnosis."""
        submit_msg = _mock_tool_call_message(
            "submit_diagnosis",
            {
                "root_cause_id": "UNKNOWN",
                "summary": "Budget exhausted.",
                "confidence": 0.0,
                "evidence_ids": [],
            },
            "call_1",
        )
        with patch.object(adapter._client.chat.completions, "create", new=AsyncMock(
            return_value=_mock_completion(submit_msg)
        )):
            result = asyncio.run(
                adapter.investigate(
                    incident=make_incident(),
                    tools=[make_tool()],
                    tool_gateway=mock_tool_gateway,
                    context=make_context(),
                    max_tool_calls=1,  # only 1 call = forced submit
                )
            )
        assert result.diagnosis.root_cause_id == "UNKNOWN"

    def test_tool_error_becomes_evidence(self, adapter, mock_tool_gateway):
        """A ToolPolicyDenied error is captured as an error payload, not raised."""
        from app.investigator.gateway import ToolPolicyDenied
        mock_tool_gateway.call = AsyncMock(side_effect=ToolPolicyDenied("Not allowed"))
        list_pods_msg = _mock_tool_call_message("k8s.list_pods", {}, "call_1")
        submit_msg = _mock_tool_call_message(
            "submit_diagnosis",
            {"root_cause_id": "UNKNOWN", "summary": "Error.", "confidence": 0.0, "evidence_ids": []},
            "call_2",
        )
        with patch.object(adapter._client.chat.completions, "create", new=AsyncMock(
            side_effect=[_mock_completion(list_pods_msg), _mock_completion(submit_msg)]
        )):
            result = asyncio.run(
                adapter.investigate(
                    incident=make_incident(),
                    tools=[make_tool()],
                    tool_gateway=mock_tool_gateway,
                    context=make_context(),
                    max_tool_calls=12,
                )
            )
        assert result.tool_calls[0].error is not None
        assert result.tool_calls[0].evidence_id is None

    def test_investigation_result_has_timing(self, adapter, mock_tool_gateway):
        submit_msg = _mock_tool_call_message(
            "submit_diagnosis",
            {"root_cause_id": "UNKNOWN", "summary": ".", "confidence": 0.0, "evidence_ids": []},
            "call_1",
        )
        with patch.object(adapter._client.chat.completions, "create", new=AsyncMock(
            return_value=_mock_completion(submit_msg)
        )):
            result = asyncio.run(
                adapter.investigate(
                    incident=make_incident(), tools=[], tool_gateway=mock_tool_gateway,
                    context=make_context(), max_tool_calls=1,
                )
            )
        assert result.total_duration_ms >= 0
        assert result.provider == "openai"
        assert result.model == "gpt-4o"
