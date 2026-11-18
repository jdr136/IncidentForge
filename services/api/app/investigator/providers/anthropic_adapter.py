"""
Anthropic Claude ProviderAdapter — implements the ProviderAdapter Protocol.

Drives the investigation loop using Anthropic's Messages API with tool use.
The final diagnosis is obtained via a mandatory `submit_diagnosis` tool call,
mirroring the OpenAI adapter's behaviour.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

from anthropic import AsyncAnthropic

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..', '..'))
from app.investigator.prompt import INVESTIGATOR_SYSTEM_PROMPT, SUBMIT_DIAGNOSIS_DESCRIPTION
from contracts.types import (
    ApprovedRemediation,
    Diagnosis,
    IncidentSummary,
    InvestigationResult,
    RunContext,
    ToolCallRecord,
    ToolDefinition,
)

logger = logging.getLogger(__name__)

SUBMIT_DIAGNOSIS_TOOL_NAME = "submit_diagnosis"
MAX_TOKENS = 4096


class AnthropicProviderAdapter:
    """
    Implements the ProviderAdapter Protocol using Anthropic's Claude API.

    Structurally identical to OpenAIProviderAdapter but adapted for
    Anthropic's Messages API format. The investigation loop, budget
    enforcement, and diagnosis parsing are equivalent.

    Note: Anthropic does not support a `seed` parameter — it is accepted
    for interface compatibility but ignored.
    """

    def __init__(self, model: str, api_key: str | None = None) -> None:
        self._model = model
        self._client = AsyncAnthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )

    async def investigate(
        self,
        incident: IncidentSummary,
        tools: list[ToolDefinition],
        tool_gateway,
        context: RunContext,
        max_tool_calls: int = 12,
        seed: int | None = None,  # not supported by Anthropic; accepted for interface compat
        temperature: float = 0.0,
    ) -> InvestigationResult:
        start = datetime.now(timezone.utc)
        anthropic_tools = _build_anthropic_tools(tools)
        tool_call_records: list[ToolCallRecord] = []
        tool_calls_used = 0
        messages: list[dict[str, Any]] = []
        diagnosis: Diagnosis | None = None

        system_prompt = INVESTIGATOR_SYSTEM_PROMPT.format(
            incident_title=incident.title,
            incident_objective=incident.objective,
            remaining_calls=max_tool_calls,
        )

        while tool_calls_used < max_tool_calls:
            remaining = max_tool_calls - tool_calls_used

            # Force submit_diagnosis when budget is about to run out
            if remaining <= 1:
                tool_choice: dict[str, Any] = {
                    "type": "tool",
                    "name": SUBMIT_DIAGNOSIS_TOOL_NAME,
                }
            else:
                tool_choice = {"type": "auto"}

            response = await self._client.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                messages=messages,
                tools=anthropic_tools,
                tool_choice=tool_choice,
                temperature=temperature,
            )

            # Append assistant turn to message history
            messages.append({"role": "assistant", "content": response.content})

            # If no tool use, the model gave a text response — try to extract
            if response.stop_reason == "end_turn":
                text = " ".join(
                    block.text for block in response.content
                    if hasattr(block, "text")
                )
                diagnosis = _extract_diagnosis_from_text(text)
                break

            # Process content blocks
            tool_results: list[dict[str, Any]] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                if block.name == SUBMIT_DIAGNOSIS_TOOL_NAME:
                    diagnosis = _parse_diagnosis(block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps({"status": "diagnosis_received"}),
                    })
                    break

                # Regular tool call
                tool_calls_used += 1
                call_start = datetime.now(timezone.utc)
                evidence = None
                error_msg: str | None = None

                try:
                    evidence = await tool_gateway.call(context, block.name, block.input)
                    tool_result_content = json.dumps({
                        **evidence.payload,
                        "_evidence_id": evidence.evidence_id,
                        "_note": "Cite this evidence_id in your diagnosis if relevant.",
                    })
                except Exception as exc:
                    error_msg = str(exc)
                    tool_result_content = json.dumps({"error": error_msg, "tool": block.name})

                duration_ms = int(
                    (datetime.now(timezone.utc) - call_start).total_seconds() * 1000
                )
                tool_call_records.append(ToolCallRecord(
                    sequence=len(tool_call_records) + 1,
                    tool_name=block.name,
                    arguments=dict(block.input),
                    evidence_id=evidence.evidence_id if evidence else None,
                    error=error_msg,
                    duration_ms=duration_ms,
                ))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": tool_result_content,
                })

            if diagnosis is not None:
                break

            if tool_results:
                messages.append({"role": "user", "content": tool_results})

        # Budget exhausted without a diagnosis
        if diagnosis is None:
            diagnosis = Diagnosis(
                root_cause_id="UNKNOWN",
                summary="Investigation budget exhausted without reaching a conclusion.",
                confidence=0.0,
                evidence_ids=[r.evidence_id for r in tool_call_records if r.evidence_id],
            )

        total_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        return InvestigationResult(
            run_id=context.run_id,
            tool_calls=tool_call_records,
            diagnosis=diagnosis,
            total_duration_ms=total_ms,
            provider="anthropic",
            model=self._model,
        )


def _build_anthropic_tools(tools: list[ToolDefinition]) -> list[dict]:
    """Convert ToolDefinitions to Anthropic tool format, plus submit_diagnosis."""
    anthropic_tools = []
    for t in tools:
        anthropic_tools.append({
            "name": t.name,
            "description": t.description,
            "input_schema": t.parameters_schema,
        })
    # Always add submit_diagnosis
    anthropic_tools.append({
        "name": SUBMIT_DIAGNOSIS_TOOL_NAME,
        "description": SUBMIT_DIAGNOSIS_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "required": ["root_cause_id", "summary", "confidence", "evidence_ids"],
            "properties": {
                "root_cause_id": {
                    "type": "string",
                    "description": (
                        "The identified root cause ID, e.g. K8S_READINESS_PATH_MISMATCH. "
                        "Use UNKNOWN if evidence is insufficient."
                    ),
                },
                "summary": {
                    "type": "string",
                    "description": "Human-readable explanation of the root cause.",
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "Your confidence in this diagnosis (0.0 to 1.0).",
                },
                "evidence_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of evidence IDs from tool responses supporting this diagnosis.",
                },
                "alternatives": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "root_cause_id": {"type": "string"},
                            "why_less_likely": {"type": "string"},
                        },
                    },
                    "description": "Alternative hypotheses considered and ruled out.",
                },
                "remediation": {
                    "type": "object",
                    "description": "Proposed fix (omit if no safe fix is known).",
                    "properties": {
                        "action_type": {"type": "string"},
                        "target_ref": {"type": "string"},
                        "parameters": {"type": "object"},
                    },
                },
                "limitations": {
                    "type": "string",
                    "description": "What evidence was unavailable or unclear.",
                },
            },
        },
    })
    return anthropic_tools


def _parse_diagnosis(args: dict) -> Diagnosis:
    """Parse the submit_diagnosis tool input into a Diagnosis object."""
    remediation = None
    if args.get("remediation"):
        r = args["remediation"]
        remediation = ApprovedRemediation(
            action_type=r.get("action_type", ""),
            target_ref=r.get("target_ref", ""),
            parameters=r.get("parameters", {}),
        )
    return Diagnosis(
        root_cause_id=args.get("root_cause_id", "UNKNOWN"),
        summary=args.get("summary", "No summary provided."),
        confidence=float(args.get("confidence", 0.0)),
        evidence_ids=args.get("evidence_ids", []),
        alternatives=args.get("alternatives", []),
        remediation=remediation,
        limitations=args.get("limitations"),
    )


def _extract_diagnosis_from_text(text: str) -> Diagnosis:
    """Fallback: try to extract JSON from a text-only response."""
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
            return _parse_diagnosis(data)
    except Exception:
        pass
    return Diagnosis(
        root_cause_id="UNKNOWN",
        summary="Could not parse diagnosis from model response.",
        confidence=0.0,
        evidence_ids=[],
        limitations="Model returned text instead of a structured tool call.",
    )
