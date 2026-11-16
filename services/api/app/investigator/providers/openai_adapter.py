"""
OpenAI ProviderAdapter — implements the ProviderAdapter Protocol.

Drives the investigation loop using OpenAI's tool-calling API.
The final diagnosis is obtained via a mandatory `submit_diagnosis` tool call.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..', '..'))
from app.investigator.prompt import INVESTIGATOR_SYSTEM_PROMPT, SUBMIT_DIAGNOSIS_DESCRIPTION
from contracts.types import (
    ApprovedRemediation,
    Diagnosis,
    EvidenceItem,
    IncidentSummary,
    InvestigationResult,
    RunContext,
    ToolCallRecord,
    ToolDefinition,
)

logger = logging.getLogger(__name__)

SUBMIT_DIAGNOSIS_TOOL_NAME = "submit_diagnosis"


class OpenAIProviderAdapter:
    """
    Implements the ProviderAdapter Protocol using the OpenAI API.

    Key design:
    - submit_diagnosis is a special tool the model must call when done.
    - When budget is 1 call remaining, force tool_choice=submit_diagnosis.
    - All tool outputs are presented back as tool message content.
    - Provider-specific request/response format is hidden from domain code.
    """

    def __init__(self, model: str, api_key: str | None = None) -> None:
        self._model = model
        self._client = AsyncOpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))

    async def investigate(
        self,
        incident: IncidentSummary,
        tools: list[ToolDefinition],
        tool_gateway,  # ToolGateway Protocol
        context: RunContext,
        max_tool_calls: int = 12,
        seed: int | None = None,
        temperature: float = 0.0,
    ) -> InvestigationResult:
        start = datetime.now(UTC)
        openai_tools = _build_openai_tools(tools, max_tool_calls)
        tool_call_records: list[ToolCallRecord] = []
        tool_calls_used = 0

        messages: list[ChatCompletionMessageParam] = [
            {
                "role": "system",
                "content": INVESTIGATOR_SYSTEM_PROMPT.format(
                    incident_title=incident.title,
                    incident_objective=incident.objective,
                    remaining_calls=max_tool_calls,
                ),
            }
        ]

        diagnosis: Diagnosis | None = None

        while tool_calls_used < max_tool_calls:
            remaining = max_tool_calls - tool_calls_used

            # Force submit_diagnosis when budget is about to run out
            if remaining <= 1:
                tool_choice: Any = {
                    "type": "function",
                    "function": {"name": SUBMIT_DIAGNOSIS_TOOL_NAME},
                }
            else:
                tool_choice = "auto"

            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=openai_tools,
                tool_choice=tool_choice,
                temperature=temperature,
                seed=seed,
            )

            choice = response.choices[0]
            msg = choice.message
            messages.append(msg.model_dump(exclude_none=True))

            if not msg.tool_calls:
                # Model returned text without calling a tool (shouldn't happen with tool_choice)
                # Try to extract a diagnosis from the text as a fallback
                logger.warning("Model returned text without tool call; attempting JSON extraction")
                diagnosis = _extract_diagnosis_from_text(msg.content or "")
                break

            tool_response_messages: list[ChatCompletionMessageParam] = []

            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                if fn_name == SUBMIT_DIAGNOSIS_TOOL_NAME:
                    # Final answer
                    diagnosis = _parse_diagnosis(fn_args)
                    # Acknowledge the tool call so the loop can terminate cleanly
                    tool_response_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"status": "diagnosis_received"}),
                    })
                    messages.extend(tool_response_messages)
                    break

                # Regular tool call
                tool_calls_used += 1
                call_start = datetime.now(UTC)
                evidence: EvidenceItem | None = None
                error_msg: str | None = None

                try:
                    evidence = await tool_gateway.call(context, fn_name, fn_args)
                    tool_result = evidence.payload
                except Exception as exc:
                    error_msg = str(exc)
                    tool_result = {"error": error_msg, "tool": fn_name}

                duration_ms = int(
                    (datetime.now(UTC) - call_start).total_seconds() * 1000
                )
                tool_call_records.append(ToolCallRecord(
                    sequence=len(tool_call_records) + 1,
                    tool_name=fn_name,
                    arguments=fn_args,
                    evidence_id=evidence.evidence_id if evidence else None,
                    error=error_msg,
                    duration_ms=duration_ms,
                ))

                tool_response_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({
                        **tool_result,
                        "_evidence_id": evidence.evidence_id if evidence else None,
                        "_note": "Cite this evidence_id in your diagnosis if relevant.",
                    }),
                })
            else:
                messages.extend(tool_response_messages)
                continue

            # submit_diagnosis was called — exit loop
            if diagnosis is not None:
                break
            messages.extend(tool_response_messages)

        # If we exhausted budget without a diagnosis, produce UNKNOWN
        if diagnosis is None:
            diagnosis = Diagnosis(
                root_cause_id="UNKNOWN",
                summary="Investigation budget exhausted without reaching a conclusion.",
                confidence=0.0,
                evidence_ids=[r.evidence_id for r in tool_call_records if r.evidence_id],
            )

        total_ms = int((datetime.now(UTC) - start).total_seconds() * 1000)

        return InvestigationResult(
            run_id=context.run_id,
            tool_calls=tool_call_records,
            diagnosis=diagnosis,
            total_duration_ms=total_ms,
            provider="openai",
            model=self._model,
        )


def _build_openai_tools(tools: list[ToolDefinition], max_calls: int) -> list[dict]:
    """Convert ToolDefinitions to OpenAI function format, plus submit_diagnosis."""
    openai_tools = []
    for t in tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters_schema,
            },
        })
    # Always add the submit_diagnosis tool
    openai_tools.append({
        "type": "function",
        "function": {
            "name": SUBMIT_DIAGNOSIS_TOOL_NAME,
            "description": SUBMIT_DIAGNOSIS_DESCRIPTION,
            "parameters": {
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
                        "description": "List of evidence IDs (from tool responses) supporting this diagnosis.",
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
                            "action_type": {"type": "string",
                                            "description": "e.g. k8s.set_readiness_path"},
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
        },
    })
    return openai_tools


def _parse_diagnosis(args: dict) -> Diagnosis:
    """Parse the submit_diagnosis arguments into a Diagnosis object."""
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
    """Fallback: try to extract JSON from a text response."""
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
