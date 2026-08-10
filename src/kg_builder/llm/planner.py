"""Natural-language question to validated QueryPlan using a local model."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from kg_builder.query.query_plan import (
    FILTER_BINDINGS,
    QueryPlan,
    QueryPlanError,
    SelectionMode,
)
from kg_builder.query.schema_catalog import DEFAULT_SPEC_PATH, ROOT, SchemaCatalog

from .client import LLMResponseError, StructuredLLMClient
from .models import PlanningOutcome, PlanningStatus
from .prompts import PLANNER_SYSTEM_PROMPT, planner_prompt


DEFAULT_VERIFIED_DATA = ROOT / "data/verified/2026/2026_curriculum_kg_data.json"
LLM_REQUESTED_FIELDS = frozenset(
    {
        "course_code",
        "name_ko",
        "grade_year",
        "semester",
        "credits",
        "lecture_hours",
        "practice_hours",
        "completion_type",
        "rule_type",
        "operator",
        "value",
        "unit",
        "description_ko",
    }
)
SELECTION_MODES = frozenset(item.value for item in SelectionMode)

def planner_response_schema(catalog: SchemaCatalog) -> dict[str, Any]:
    requested_fields = sorted(LLM_REQUESTED_FIELDS.intersection(catalog.all_node_properties))
    filter_properties: dict[str, Any] = {
        "academic_year": {"type": "integer", "minimum": 1900, "maximum": 9999},
        "department_id": {"type": "string"},
        "grade_year": {"type": "integer", "minimum": 1, "maximum": 6},
        "semester": {"type": "string", "enum": sorted(catalog.controlled_vocabularies["semester"])},
        "completion_type": {"type": "string", "enum": sorted(catalog.controlled_vocabularies["completion_type"])},
        "credits": {"type": "number", "minimum": 0},
        "course_code": {"type": "string"},
        "name_ko": {"type": "string"},
        "rule_id": {"type": "string"},
        "rule_ids": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "uniqueItems": True,
        },
        "major_type": {"type": "string", "enum": sorted(catalog.controlled_vocabularies["major_type"])},
        "admission_type": {"type": "string", "enum": sorted(catalog.controlled_vocabularies["admission_type"])},
    }
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": [item.value for item in PlanningStatus]},
            "intent": {"type": ["string", "null"]},
            "filters": {
                "type": "object",
                "properties": filter_properties,
                "additionalProperties": False,
            },
            "requested_fields": {
                "type": "array",
                "items": {"type": "string", "enum": requested_fields},
                "minItems": 1,
                "uniqueItems": True,
            },
            "evidence_required": {"type": "boolean"},
            "message": {"type": ["string", "null"]},
            "selection_mode": {"type": "string", "enum": sorted(SELECTION_MODES)},
        },
        "required": [
            "status",
            "intent",
            "filters",
            "requested_fields",
            "evidence_required",
            "message",
            "selection_mode",
        ],
        "additionalProperties": False,
    }


def build_planner_context(
    catalog: SchemaCatalog,
    data_path: Path = DEFAULT_VERIFIED_DATA,
) -> dict[str, Any]:
    data = json.loads(data_path.read_text(encoding="utf-8"))
    departments = []
    rule_ids = []
    academic_years: set[int] = set()
    for node in data["nodes"]:
        labels = set(node["labels"])
        props = node["properties"]
        if "CurriculumVersion" in labels:
            academic_years.add(props["academic_year"])
        if "Department" in labels:
            departments.append(
                {"department_id": props["department_id"], "name_ko": props["name_ko"]}
            )
        if "Rule" in labels and props.get("status") == "VERIFIED":
            rule_ids.append(
                {
                    "rule_id": props["rule_id"],
                    "rule_type": props.get("rule_type"),
                    "semantic_hint_without_values": re.sub(
                        r"\d+(?:[.~～-]\d+)?",
                        "<number>",
                        props.get("description_ko", ""),
                    ),
                }
            )
    return {
        "academic_years": sorted(academic_years),
        "departments": sorted(departments, key=lambda item: item["department_id"]),
        "verified_rule_identifiers": sorted(rule_ids, key=lambda item: item["rule_id"]),
        "supported_filters": sorted(FILTER_BINDINGS),
        "controlled_vocabularies": {
            name: sorted(values) for name, values in catalog.controlled_vocabularies.items()
        },
        "supported_requested_fields": sorted(
            LLM_REQUESTED_FIELDS.intersection(catalog.all_node_properties)
        ),
    }


class LocalQueryPlanner:
    def __init__(
        self,
        client: StructuredLLMClient,
        *,
        catalog: SchemaCatalog | None = None,
        planner_context: Mapping[str, Any] | None = None,
    ):
        self.client = client
        self.catalog = catalog or SchemaCatalog.from_spec(DEFAULT_SPEC_PATH)
        self.context = dict(planner_context or build_planner_context(self.catalog))

    def plan(self, question: str) -> PlanningOutcome:
        if not isinstance(question, str) or not question.strip():
            raise QueryPlanError("question must be a non-empty string")
        previous_error: str | None = None
        for attempt in range(2):
            generation = self.client.generate_json(
                system_prompt=PLANNER_SYSTEM_PROMPT,
                user_prompt=planner_prompt(
                    question.strip(), self.context, previous_error=previous_error
                ),
                response_schema=planner_response_schema(self.catalog),
            )
            payload = generation.payload
            try:
                status = PlanningStatus(payload.get("status"))
                message = payload.get("message")
                if message is not None and not isinstance(message, str):
                    raise LLMResponseError(
                        "LLM_PLAN_MESSAGE_INVALID", "planner message must be text"
                    )
                selection_mode = payload.get("selection_mode")
                filters = payload.get("filters")
                requested_fields = payload.get("requested_fields")
                if status is not PlanningStatus.READY:
                    # Do not silently turn the model's ambiguity decision into READY.
                    # For a fully scoped SINGLE_COURSE request, ask the model once to
                    # delegate candidate cardinality to the stable Course identity
                    # validation performed after the database query.  A second
                    # CLARIFICATION_REQUIRED remains a safe stop.
                    fully_scoped_course = (
                        status is PlanningStatus.CLARIFICATION_REQUIRED
                        and selection_mode == SelectionMode.SINGLE_COURSE.value
                        and isinstance(filters, dict)
                        and {"academic_year", "department_id"}.issubset(filters)
                        and bool({"name_ko", "course_code"}.intersection(filters))
                    )
                    if fully_scoped_course and attempt == 0:
                        previous_error = (
                            "SINGLE_COURSE is fully scoped. Return READY so the database "
                            "result validator can count stable course identities; do not "
                            "guess whether duplicate course names exist."
                        )
                        continue
                    return PlanningOutcome(status=status, message=message)
                rule_ids = filters.get("rule_ids") if isinstance(filters, dict) else None
                if selection_mode == "SINGLE_RULE" and (
                    not isinstance(filters, dict)
                    or not (
                        isinstance(filters.get("rule_id"), str)
                        or (isinstance(rule_ids, list) and len(rule_ids) == 1)
                    )
                ):
                    raise QueryPlanError("SINGLE_RULE requires one rule_id or one rule_ids entry")
                if selection_mode == "MULTIPLE_RULES" and (
                    not isinstance(rule_ids, list) or len(rule_ids) < 2
                ):
                    raise QueryPlanError("MULTIPLE_RULES requires at least two rule_ids entries")
                if selection_mode == "SINGLE_COURSE" and (
                    not isinstance(filters, dict)
                    or not {"academic_year", "department_id"}.issubset(filters)
                    or not ({"name_ko", "course_code"}.intersection(filters))
                ):
                    raise QueryPlanError(
                        "SINGLE_COURSE requires year, department, and course name or code"
                    )
                if selection_mode == "COURSE_LIST" and (
                    not isinstance(filters, dict)
                    or not {"academic_year", "department_id"}.issubset(filters)
                ):
                    raise QueryPlanError("COURSE_LIST requires year and department")
                # A numeric Rule value is not a grounded semantic Claim without
                # its operator, unit, type, and verified description.  Small
                # models sometimes request only ``value``; expand the structural
                # result contract here without adding any answer value or
                # question-specific branch.
                if isinstance(requested_fields, list) and "value" in requested_fields:
                    requested_fields = list(
                        dict.fromkeys(
                            requested_fields
                            + ["rule_type", "operator", "unit", "description_ko"]
                        )
                    )
                plan_payload = {
                    "question": question.strip(),
                    "intent": payload.get("intent"),
                    "filters": filters,
                    "requested_fields": requested_fields,
                    "evidence_required": payload.get("evidence_required"),
                    "selection_mode": selection_mode,
                }
                return PlanningOutcome(
                    status=status,
                    plan=QueryPlan.from_dict(plan_payload, self.catalog),
                    message=None,
                )
            except (ValueError, QueryPlanError, LLMResponseError) as exc:
                previous_error = str(exc)
                if attempt == 1:
                    raise LLMResponseError(
                        "LLM_PLAN_CONTRACT_INVALID", "planner failed the QueryPlan contract"
                    ) from exc
        raise AssertionError("unreachable planner retry state")
