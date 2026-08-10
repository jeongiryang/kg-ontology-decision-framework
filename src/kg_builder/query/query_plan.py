"""Structured contract produced by a future natural-language planning model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .schema_catalog import SchemaCatalog


class QueryPlanError(ValueError):
    """Raised when a QueryPlan is incomplete or violates the ontology contract."""


SUPPORTED_FILTERS = frozenset(
    {
        "academic_year",
        "department_code",
        "grade_year",
        "semester",
        "completion_type",
        "course_code",
        "course_name",
        "major_type",
        "admission_type",
    }
)
REQUIRED_SCOPE_FILTERS = frozenset({"academic_year", "department_code"})
VOCABULARY_FILTERS = {
    "semester": "semester",
    "completion_type": "completion_type",
    "major_type": "major_type",
    "admission_type": "admission_type",
}


@dataclass(frozen=True, slots=True)
class QueryPlan:
    question: str
    filters: dict[str, Any]
    requested_fields: tuple[str, ...]
    evidence_required: bool
    intent: str | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], catalog: SchemaCatalog) -> "QueryPlan":
        if not isinstance(payload, Mapping):
            raise QueryPlanError("QueryPlan must be a JSON object")
        allowed = {"question", "filters", "requested_fields", "evidence_required", "intent"}
        unknown = set(payload) - allowed
        if unknown:
            raise QueryPlanError(f"unknown QueryPlan fields: {sorted(unknown)}")

        question = payload.get("question")
        if not isinstance(question, str) or not question.strip():
            raise QueryPlanError("question must be a non-empty string")
        filters = payload.get("filters")
        if not isinstance(filters, Mapping):
            raise QueryPlanError("filters must be a JSON object")
        filters = dict(filters)
        unsupported = set(filters) - SUPPORTED_FILTERS
        if unsupported:
            raise QueryPlanError(f"unsupported filters: {sorted(unsupported)}")
        missing_scope = REQUIRED_SCOPE_FILTERS - set(filters)
        if missing_scope:
            raise QueryPlanError(f"missing required scope filters: {sorted(missing_scope)}")

        year = filters.get("academic_year")
        if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 9999:
            raise QueryPlanError("academic_year must be a four-digit integer")
        grade = filters.get("grade_year")
        if grade is not None and (
            isinstance(grade, bool) or not isinstance(grade, int) or not 1 <= grade <= 6
        ):
            raise QueryPlanError("grade_year must be an integer from 1 to 6")
        for name in ("department_code", "course_code", "course_name"):
            if name in filters:
                value = filters[name]
                if not isinstance(value, str) or not value.strip():
                    raise QueryPlanError(f"{name} must be a non-empty string")
                filters[name] = value.strip()
        for name, vocabulary in VOCABULARY_FILTERS.items():
            if name in filters and filters[name] not in catalog.controlled_vocabularies[vocabulary]:
                raise QueryPlanError(f"{name} is not in controlled vocabulary {vocabulary}")

        fields = payload.get("requested_fields")
        if not isinstance(fields, list) or not fields:
            raise QueryPlanError("requested_fields must be a non-empty array")
        if any(not isinstance(field, str) or not field.strip() for field in fields):
            raise QueryPlanError("requested_fields entries must be non-empty strings")
        normalized_fields = tuple(field.strip() for field in fields)
        if len(set(normalized_fields)) != len(normalized_fields):
            raise QueryPlanError("requested_fields must not contain duplicates")
        undeclared = set(normalized_fields) - catalog.all_node_properties
        if undeclared:
            raise QueryPlanError(
                f"requested fields are absent from ontology_spec.json: {sorted(undeclared)}"
            )

        evidence_required = payload.get("evidence_required")
        if not isinstance(evidence_required, bool):
            raise QueryPlanError("evidence_required must be boolean")
        intent = payload.get("intent")
        if intent is not None and (not isinstance(intent, str) or not intent.strip()):
            raise QueryPlanError("intent, when present, is logging metadata only and must be text")
        return cls(
            question=question.strip(),
            filters=filters,
            requested_fields=normalized_fields,
            evidence_required=evidence_required,
            intent=intent.strip() if isinstance(intent, str) else None,
        )
