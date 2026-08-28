"""Strict schemas for the bounded GraphRAG tools.

The planner selects a tool name.  Inputs are then derived from already validated
question/context/profile objects; free-form model arguments are never executed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import ToolName


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: ToolName
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]


_EMPTY_OBJECT = {"type": "object", "properties": {}, "additionalProperties": False}
_STATUS_OUTPUT = {
    "type": "object",
    "properties": {"status": {"type": "string"}},
    "required": ["status"],
    "additionalProperties": False,
}

TOOL_SPECS: Mapping[ToolName, ToolSpec] = {
    ToolName.READ_USER_PROFILE: ToolSpec(
        ToolName.READ_USER_PROFILE,
        _EMPTY_OBJECT,
        _STATUS_OUTPUT,
    ),
    ToolName.RESOLVE_COURSE: ToolSpec(
        ToolName.RESOLVE_COURSE,
        {
            "type": "object",
            "properties": {
                "course_codes": {
                    "type": "array",
                    "items": {"type": "string", "pattern": "^[A-Z0-9_-]{2,32}$"},
                    "maxItems": 20,
                    "uniqueItems": True,
                }
            },
            "required": ["course_codes"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"resolved_count": {"type": "integer", "minimum": 0}},
            "required": ["resolved_count"],
            "additionalProperties": False,
        },
    ),
    ToolName.QUERY_CURRICULUM: ToolSpec(
        ToolName.QUERY_CURRICULUM,
        {
            "type": "object",
            "properties": {
                "question": {"type": "string", "minLength": 1, "maxLength": 2000}
            },
            "required": ["question"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "fact_count": {"type": "integer", "minimum": 0},
                "evidence_count": {"type": "integer", "minimum": 0},
            },
            "required": ["status", "fact_count", "evidence_count"],
            "additionalProperties": False,
        },
    ),
    ToolName.CALCULATE_REMAINING_CREDITS: ToolSpec(
        ToolName.CALCULATE_REMAINING_CREDITS,
        {
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["total", "general", "major", "free_elective"],
                    },
                    "maxItems": 4,
                    "uniqueItems": True,
                }
            },
            "required": ["categories"],
            "additionalProperties": False,
        },
        _STATUS_OUTPUT,
    ),
    ToolName.ASK_CLARIFICATION: ToolSpec(
        ToolName.ASK_CLARIFICATION,
        {
            "type": "object",
            "properties": {
                "missing_fields": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 80},
                    "maxItems": 8,
                    "uniqueItems": True,
                }
            },
            "required": ["missing_fields"],
            "additionalProperties": False,
        },
        _STATUS_OUTPUT,
    ),
    ToolName.GROUNDED_NARRATIVE: ToolSpec(
        ToolName.GROUNDED_NARRATIVE,
        {
            "type": "object",
            "properties": {
                "claim_count": {"type": "integer", "minimum": 0},
                "evidence_count": {"type": "integer", "minimum": 0},
            },
            "required": ["claim_count", "evidence_count"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"grounding_preserved": {"type": "boolean"}},
            "required": ["grounding_preserved"],
            "additionalProperties": False,
        },
    ),
}


def validate_tool_input(name: ToolName, value: Mapping[str, Any]) -> None:
    """Small runtime validator for inputs constructed by the orchestrator."""

    if not isinstance(value, Mapping):
        raise ValueError("tool input must be an object")
    if name is ToolName.READ_USER_PROFILE:
        if value:
            raise ValueError("read_user_profile accepts no arguments")
        return
    if name is ToolName.RESOLVE_COURSE:
        _string_list(value, "course_codes", 20, 32)
        _only(value, {"course_codes"})
        return
    if name is ToolName.QUERY_CURRICULUM:
        _only(value, {"question"})
        question = value.get("question")
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            raise ValueError("query_curriculum question is invalid")
        return
    if name is ToolName.CALCULATE_REMAINING_CREDITS:
        values = _string_list(value, "categories", 4, 40)
        if not set(values).issubset({"total", "general", "major", "free_elective"}):
            raise ValueError("credit category is invalid")
        _only(value, {"categories"})
        return
    if name is ToolName.ASK_CLARIFICATION:
        _string_list(value, "missing_fields", 8, 80)
        _only(value, {"missing_fields"})
        return
    if name is ToolName.GROUNDED_NARRATIVE:
        _only(value, {"claim_count", "evidence_count"})
        if any(
            isinstance(value.get(field), bool)
            or not isinstance(value.get(field), int)
            or value[field] < 0
            for field in ("claim_count", "evidence_count")
        ):
            raise ValueError("grounded narrative counts are invalid")
        return
    raise ValueError("unknown agent tool")


def _only(value: Mapping[str, Any], allowed: set[str]) -> None:
    if set(value) != allowed:
        raise ValueError("tool input fields do not match its schema")


def _string_list(
    value: Mapping[str, Any], name: str, maximum_items: int, maximum_chars: int
) -> tuple[str, ...]:
    raw = value.get(name)
    if not isinstance(raw, (list, tuple)) or len(raw) > maximum_items:
        raise ValueError(f"{name} is invalid")
    if any(not isinstance(item, str) or not item or len(item) > maximum_chars for item in raw):
        raise ValueError(f"{name} is invalid")
    if len(set(raw)) != len(raw):
        raise ValueError(f"{name} must be unique")
    return tuple(raw)
