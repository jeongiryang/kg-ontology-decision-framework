"""Structured contract produced by a future natural-language planning model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .fact_families import (
    BASE_FILTER_BINDINGS,
    EXTENDED_ONLY_FILTERS,
    FilterBinding,
    SelectionMode,
    family_for_mode,
    resolve_filter_bindings,
)
from .schema_catalog import SchemaCatalog


class QueryPlanError(ValueError):
    """Raised when a QueryPlan is incomplete or violates the ontology contract."""


MAX_QUESTION_LENGTH = 2_000

# ``SelectionMode``/``FilterBinding``/``FILTER_BINDINGS`` are declared in
# ``fact_families`` so that fact-family declarations and the plan contract cannot
# drift apart.  They stay importable from here for the existing call sites.
__all__ = [
    "FILTER_BINDINGS",
    "FilterBinding",
    "MAX_QUESTION_LENGTH",
    "QueryPlan",
    "QueryPlanError",
    "SelectionMode",
    "SUPPORTED_FILTERS",
    "resolve_filter_bindings",
    "validate_filter_policy",
]

FILTER_BINDINGS = BASE_FILTER_BINDINGS
SUPPORTED_FILTERS = frozenset(FILTER_BINDINGS)
REQUIRED_SCOPE_FILTERS = frozenset({"academic_year"})
DEPARTMENT_SCOPED_FILTERS = frozenset(
    {
        "grade_year",
        "semester",
        "completion_type",
        "credits",
        "course_code",
        "course_codes",
        "name_ko",
    }
)
DEPARTMENT_SCOPED_FIELDS = frozenset(
    {
        "course_code",
        "name_ko",
        "grade_year",
        "semester",
        "credits",
        "lecture_hours",
        "practice_hours",
        "completion_type",
        "offering_id",
    }
)
VOCABULARY_FILTERS = {
    "semester": "semester",
    "completion_type": "completion_type",
    "major_type": "major_type",
    "admission_type": "admission_type",
    "recommended_semester": "semester",
    "entry_type": "roadmap_entry_type",
    "goal_scope": "goal_scope",
}
# 자유 문자열 필터. 통제어휘가 아니므로 값 자체를 검사하지 않고, 일치하는 사실이
# 없으면 NOT_FOUND 로 끝난다. 없는 값을 지어내지 않는다는 성질은 그대로다.
FREE_TEXT_FILTERS = frozenset({"credit_category"})
BOOLEAN_FILTERS = frozenset({"source_was_blank", "is_total"})


def validate_filter_policy(catalog: SchemaCatalog) -> None:
    """Check every declared binding, including per-family overrides, against the catalog."""

    bindings = dict(FILTER_BINDINGS)
    for mode in SelectionMode:
        family = family_for_mode(mode)
        if family is not None:
            bindings.update(
                {
                    f"{mode.value}.{name}": binding
                    for name, binding in family.filter_overrides.items()
                }
            )
            for field, alias in family.field_owners.items():
                if alias != family.fact_alias:
                    continue
                if field not in catalog.properties_for_labels({family.fact_label}):
                    raise QueryPlanError(
                        f"fact family {family.fact_label} declares an undeclared field {field}"
                    )
    for name, binding in bindings.items():
        definition = catalog.nodes.get(binding.label)
        if definition is None or binding.property_name not in catalog.properties_for_labels(
            {binding.label}
        ):
            raise QueryPlanError(
                f"filter policy {name} refers to an undeclared ontology label/property"
            )


@dataclass(frozen=True, slots=True)
class QueryPlan:
    question: str
    filters: dict[str, Any]
    requested_fields: tuple[str, ...]
    evidence_required: bool
    intent: str | None = None
    selection_mode: SelectionMode | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], catalog: SchemaCatalog) -> "QueryPlan":
        if not isinstance(payload, Mapping):
            raise QueryPlanError("QueryPlan must be a JSON object")
        validate_filter_policy(catalog)
        allowed = {
            "question",
            "filters",
            "requested_fields",
            "evidence_required",
            "intent",
            "selection_mode",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise QueryPlanError(f"unknown QueryPlan fields: {sorted(unknown)}")

        question = payload.get("question")
        if not isinstance(question, str) or not question.strip():
            raise QueryPlanError("question must be a non-empty string")
        if len(question) > MAX_QUESTION_LENGTH:
            raise QueryPlanError(f"question must not exceed {MAX_QUESTION_LENGTH} characters")
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
        credits = filters.get("credits")
        if credits is not None and (
            isinstance(credits, bool) or not isinstance(credits, (int, float)) or credits < 0
        ):
            raise QueryPlanError("credits must be a non-negative number")
        for name in BOOLEAN_FILTERS.intersection(filters):
            if not isinstance(filters[name], bool):
                raise QueryPlanError(f"{name} must be a boolean")
        for name in FREE_TEXT_FILTERS.intersection(filters):
            value = filters[name]
            if not isinstance(value, str) or not value.strip():
                raise QueryPlanError(f"{name} must be a non-empty string")
            filters[name] = value.strip()
        for name in ("department_id", "course_code", "name_ko", "rule_id", "area_id"):
            if name in filters:
                value = filters[name]
                if not isinstance(value, str) or not value.strip():
                    raise QueryPlanError(f"{name} must be a non-empty string")
                filters[name] = value.strip()
        if "course_codes" in filters:
            course_codes = filters["course_codes"]
            if (
                not isinstance(course_codes, list)
                or not course_codes
                or any(
                    not isinstance(value, str) or not value.strip()
                    for value in course_codes
                )
                or len(set(course_codes)) != len(course_codes)
            ):
                raise QueryPlanError(
                    "course_codes must be a non-empty array of unique strings"
                )
            filters["course_codes"] = [value.strip() for value in course_codes]
        # A stable catalog identifier takes precedence over a display name.  Keeping
        # both would make an otherwise exact lookup fail when the display name is stale.
        if "course_code" in filters and "name_ko" in filters:
            filters.pop("name_ko")
        if "rule_ids" in filters:
            rule_ids = filters["rule_ids"]
            if (
                not isinstance(rule_ids, list)
                or not rule_ids
                or any(not isinstance(value, str) or not value.strip() for value in rule_ids)
                or len(set(rule_ids)) != len(rule_ids)
            ):
                raise QueryPlanError("rule_ids must be a non-empty array of unique strings")
            filters["rule_ids"] = [value.strip() for value in rule_ids]
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
        department_scope_required = bool(
            set(filters).intersection(DEPARTMENT_SCOPED_FILTERS)
            or set(normalized_fields).intersection(DEPARTMENT_SCOPED_FIELDS)
        )
        stable_course_identity = bool(
            {"course_code", "course_codes", "name_ko"}.intersection(filters)
        )
        if (
            department_scope_required
            and "department_id" not in filters
            and not stable_course_identity
        ):
            raise QueryPlanError(
                "department_id is required for course and CourseOffering queries"
            )
        if "area_id" in filters and set(normalized_fields).issubset(
            {"value", "operator", "unit"}
        ):
            raise QueryPlanError(
                "area_id can match multiple rules; use an exact rule_id for one threshold"
            )

        evidence_required = payload.get("evidence_required")
        if not isinstance(evidence_required, bool):
            raise QueryPlanError("evidence_required must be boolean")
        if not evidence_required:
            raise QueryPlanError("dynamic answer plans must require VERIFIED Evidence")
        intent = payload.get("intent")
        if intent is not None and (not isinstance(intent, str) or not intent.strip()):
            raise QueryPlanError("intent, when present, is logging metadata only and must be text")
        selection_mode_value = payload.get("selection_mode")
        try:
            selection_mode = (
                SelectionMode(selection_mode_value)
                if selection_mode_value is not None
                else None
            )
        except ValueError as exc:
            raise QueryPlanError("selection_mode is not supported") from exc
        if selection_mode is SelectionMode.SINGLE_COURSE and not (
            {"course_code", "name_ko"}.intersection(filters)
        ):
            raise QueryPlanError(
                "SINGLE_COURSE is only for one course identified by course_code or "
                "name_ko. A credit or count question without a named course asks for "
                "a completion requirement: use SINGLE_RULE or MULTIPLE_RULES with "
                "rule_ids and the value/operator/unit/description_ko fields."
            )

        # 확장 fact family 의 계약. family 는 fact label 하나에 고정되므로 필터와
        # 요청 필드가 그 label(또는 선언된 이웃)에 실제로 존재하는지 여기서 닫는다.
        family = family_for_mode(selection_mode)
        if family is None:
            stray = EXTENDED_ONLY_FILTERS.intersection(filters)
            if stray:
                raise QueryPlanError(
                    f"filters require an extended fact family: {sorted(stray)}"
                )
        else:
            unsupported_filters = set(filters) - family.allowed_filters
            if unsupported_filters:
                raise QueryPlanError(
                    f"{selection_mode.value} does not support filters: "
                    f"{sorted(unsupported_filters)}"
                )
            missing_required = family.required_filters - set(filters)
            if missing_required:
                raise QueryPlanError(
                    f"{selection_mode.value} requires filters: {sorted(missing_required)}"
                )
            unsupported_fields = set(normalized_fields) - set(family.field_owners)
            if unsupported_fields:
                raise QueryPlanError(
                    f"{selection_mode.value} does not expose fields: "
                    f"{sorted(unsupported_fields)}"
                )
        return cls(
            question=question.strip(),
            filters=filters,
            requested_fields=normalized_fields,
            evidence_required=evidence_required,
            intent=intent.strip() if isinstance(intent, str) else None,
            selection_mode=selection_mode,
        )
