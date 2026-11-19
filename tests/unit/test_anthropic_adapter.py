"""
Tests for the Anthropic Claude provider adapter.
All Anthropic API calls are mocked — no network requests.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'services', 'api'))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")
os.environ.setdefault("DB_PATH", "/tmp/test_anthropic_adapter.db")

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.investigator.providers.anthropic_adapter import (
    AnthropicProviderAdapter,
    _build_anthropic_tools,
    _parse_diagnosis,
)

from contracts.types import (
    EvidenceItem,
    IncidentSummary,
    RunContext,
    ToolDefinition,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

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


def _mock_tool_use_block(tool_id: str, name: str, input_data: dict) -> MagicMock:
    """Build a mock Anthropic tool_use content block."""
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = name
    block.input = input_data
    return block


def _mock_text_block(text: str) -> MagicMock:
    """Build a mock Anthropic text content block."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _mock_response(stop_reason: str, content: list) -> MagicMock:
    """Build a mock Anthropic messages response."""
    response = MagicMock()
    response.stop_reason = stop_reason
    response.content = content
    return response


# ── Tool building tests ───────────────────────────────────────────────────────

class TestBuildAnthropicTools:
    def test_build_anthropic_tools_includes_submit_diagnosis(self):
        tools = _build_anthropic_tools([make_tool()])
        names = [t["name"] for t in tools]
        assert "submit_diagnosis" in names

    def test_build_anthropic_tools_uses_input_schema_not_parameters(self):
        tools = _build_anthropic_tools([make_tool()])
        for t in tools:
            assert "input_schema" in t
            assert "parameters" not in t

    def test_submit_diagnosis_has_required_fields(self):
        tools = _build_anthropic_tools([])
        sd = next(t for t in tools if t["name"] == "submit_diagnosis")
        required = sd["input_schema"]["required"]
        assert "root_cause_id" in required
        assert "evidence_ids" in required
        assert "confidence" in required
        assert "summary" in required

    def test_regular_tool_included(self):
        tools = _build_anthropic_tools([make_tool()])
        names = [t["name"] for t in tools]
        assert "k8s.list_pods" in names


# ── Diagnosis parsing tests ───────────────────────────────────────────────────

class TestParseDiagnosis:
    def test_parse_diagnosis_full(self):
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
            "alternatives": [
                {"root_cause_id": "OOM", "why_less_likely": "No OOM events"},
            ],
            "limitations": "Could not access node logs.",
        }
        d = _parse_diagnosis(args)
        assert d.root_cause_id == "K8S_READINESS_PATH_MISMATCH"
        assert d.confidence == 0.9
        assert d.evidence_ids == ["ev_abc_0001"]
        assert d.remediation is not None
        assert d.remediation.action_type == "k8s.set_readiness_path"
        assert d.remediation.target_ref == "api"
        assert d.remediation.parameters == {"path": "/healthz"}
        assert len(d.alternatives) == 1
        assert d.limitations == "Could not access node logs."

    def test_parse_diagnosis_unknown(self):
        args = {
            "root_cause_id": "UNKNOWN",
            "summary": "Not enough evidence.",
            "confidence": 0.0,
            "evidence_ids": [],
        }
        d = _parse_diagnosis(args)
        assert d.root_cause_id == "UNKNOWN"
        assert d.remediation is None
        assert d.confidence == 0.0


# ── Full investigation loop tests (mocked API) ────────────────────────────────

class TestAnthropicAdapterLoop:
    @pytest.fixture
    def adapter(self):
        return AnthropicProviderAdapter(model="claude-3-5-sonnet-20241022", api_key="test-key")

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
        """Model calls one tool then submit_diagnosis → correct InvestigationResult."""
        list_pods_resp = _mock_response("tool_use", [
            _mock_tool_use_block("toolu_01", "k8s.list_pods", {}),
        ])
        submit_resp = _mock_response("tool_use", [
            _mock_tool_use_block("toolu_02", "submit_diagnosis", {
                "root_cause_id": "K8S_READINESS_PATH_MISMATCH",
                "summary": "Probe wrong",
                "confidence": 0.9,
                "evidence_ids": ["ev_01testadap_0001"],
            }),
        ])

        with patch.object(adapter._client.messages, "create", new=AsyncMock(
            side_effect=[list_pods_resp, submit_resp]
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

    def test_budget_exhaustion_forces_submit(self, adapter, mock_tool_gateway):
        """max_tool_calls=1 → forced submit_diagnosis via tool_choice."""
        submit_resp = _mock_response("tool_use", [
            _mock_tool_use_block("toolu_01", "submit_diagnosis", {
                "root_cause_id": "UNKNOWN",
                "summary": "Budget exhausted.",
                "confidence": 0.0,
                "evidence_ids": [],
            }),
        ])

        with patch.object(adapter._client.messages, "create", new=AsyncMock(
            return_value=submit_resp
        )) as mock_create:
            result = asyncio.run(
                adapter.investigate(
                    incident=make_incident(),
                    tools=[make_tool()],
                    tool_gateway=mock_tool_gateway,
                    context=make_context(),
                    max_tool_calls=1,
                )
            )

        # Forced tool_choice should have been used
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["tool_choice"] == {
            "type": "tool",
            "name": "submit_diagnosis",
        }
        assert result.diagnosis.root_cause_id == "UNKNOWN"

    def test_tool_error_captured_not_raised(self, adapter, mock_tool_gateway):
        """ToolPolicyDenied is captured in record, not raised to the caller."""
        from app.investigator.gateway import ToolPolicyDenied
        mock_tool_gateway.call = AsyncMock(side_effect=ToolPolicyDenied("Not allowed"))

        list_pods_resp = _mock_response("tool_use", [
            _mock_tool_use_block("toolu_01", "k8s.list_pods", {}),
        ])
        submit_resp = _mock_response("tool_use", [
            _mock_tool_use_block("toolu_02", "submit_diagnosis", {
                "root_cause_id": "UNKNOWN",
                "summary": "Error occurred.",
                "confidence": 0.0,
                "evidence_ids": [],
            }),
        ])

        with patch.object(adapter._client.messages, "create", new=AsyncMock(
            side_effect=[list_pods_resp, submit_resp]
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

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].error is not None
        assert result.tool_calls[0].evidence_id is None

    def test_seed_ignored_silently(self, adapter, mock_tool_gateway):
        """seed param accepted but NOT passed to Anthropic (Anthropic doesn't support it)."""
        submit_resp = _mock_response("tool_use", [
            _mock_tool_use_block("toolu_01", "submit_diagnosis", {
                "root_cause_id": "UNKNOWN",
                "summary": "Done.",
                "confidence": 0.0,
                "evidence_ids": [],
            }),
        ])

        with patch.object(adapter._client.messages, "create", new=AsyncMock(
            return_value=submit_resp
        )) as mock_create:
            asyncio.run(
                adapter.investigate(
                    incident=make_incident(),
                    tools=[make_tool()],
                    tool_gateway=mock_tool_gateway,
                    context=make_context(),
                    max_tool_calls=1,
                    seed=42,
                )
            )

        call_kwargs = mock_create.call_args.kwargs
        assert "seed" not in call_kwargs

    def test_provider_and_model_in_result(self, adapter, mock_tool_gateway):
        """result.provider == 'anthropic', result.model == the configured model."""
        submit_resp = _mock_response("tool_use", [
            _mock_tool_use_block("toolu_01", "submit_diagnosis", {
                "root_cause_id": "UNKNOWN",
                "summary": ".",
                "confidence": 0.0,
                "evidence_ids": [],
            }),
        ])

        with patch.object(adapter._client.messages, "create", new=AsyncMock(
            return_value=submit_resp
        )):
            result = asyncio.run(
                adapter.investigate(
                    incident=make_incident(),
                    tools=[],
                    tool_gateway=mock_tool_gateway,
                    context=make_context(),
                    max_tool_calls=1,
                )
            )

        assert result.provider == "anthropic"
        assert result.model == "claude-3-5-sonnet-20241022"
        assert result.total_duration_ms >= 0

    def test_end_turn_without_tool_produces_unknown(self, adapter, mock_tool_gateway):
        """stop_reason='end_turn' with text response → UNKNOWN diagnosis."""
        end_turn_resp = _mock_response("end_turn", [
            _mock_text_block("{}"),
        ])

        with patch.object(adapter._client.messages, "create", new=AsyncMock(
            return_value=end_turn_resp
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

        assert result.diagnosis.root_cause_id == "UNKNOWN"
