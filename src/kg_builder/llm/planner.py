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
from .models import (
    GraduationQuestionClass,
    PlanningOutcome,
    PlanningStatus,
    UnsupportedReason,
)
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
COURSE_REQUEST_FIELDS = frozenset(
    {"course_code", "name_ko", "grade_year", "semester", "credits", "completion_type"}
)
_GRADUATION_CONTEXT = re.compile(
    r"(?:졸업|영어\s*대체|대학영어|공인\s*시험|TOEIC|토익)", re.IGNORECASE
)
_PERSONAL_RECORD = re.compile(
    r"(?:지금까지|현재까지).{0,12}(?:들|수강|이수)|"
    r"(?:수강|이수)\s*(?:내역|과목)|성적표|내\s*성적|취득\s*학점|"
    r"(?:들었|수강했|이수했)(?:는데|지만|고)"
)
_HOLISTIC_JUDGMENT = re.compile(
    r"(?:졸업(?:할\s*수|\s*가능)|남은\s*(?:과목|학점|요건)|"
    r"(?:뭘|무엇을)\s*해야\s*졸업|졸업(?:하려면|하기\s*위해))"
)
_SINGLE_CONDITION = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:점|학점).{0,24}(?:충족|가능|되|면제)|"
    r"(?:토익|TOEIC|점수|학점).{0,24}\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_GENERAL_RULE_CRITERION = re.compile(
    r"(?:기준|최소|최대|요건|규정|점수|학점|필수\s*과목|면제)"
)
_SUBJECT_FIELD_QUESTION_ALIASES: Mapping[str, re.Pattern[str]] = {
    "TOEIC": re.compile(r"(?:TOEIC|토익)", re.IGNORECASE),
    "TOEIC_SPEAKING": re.compile(r"(?:TOEIC\s*SPEAKING|토익\s*스피킹)", re.IGNORECASE),
    "TOEFL_IBT": re.compile(r"(?:TOEFL|토플)", re.IGNORECASE),
    "TEPS": re.compile(r"(?:TEPS|텝스)", re.IGNORECASE),
    "NEW_TEPS": re.compile(r"(?:NEW\s*TEPS|뉴\s*텝스)", re.IGNORECASE),
    "OPIC": re.compile(r"(?:OPIC|오픽)", re.IGNORECASE),
    "GTELP": re.compile(r"(?:G-?TELP|지텔프)", re.IGNORECASE),
    "FLEX": re.compile(r"FLEX", re.IGNORECASE),
}
_REQUESTED_FIELD_PATTERNS: Mapping[str, re.Pattern[str]] = {
    "grade_year": re.compile(r"(?:몇|어느)\s*학년"),
    "semester": re.compile(r"(?:몇|어느)\s*학기"),
    "course_code": re.compile(
        r"(?:과목\s*코드|학수번호).*(?:뭐|무엇|알려|인가|어떤)"
    ),
}

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
                "minItems": 0,
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
    review_required_rules: dict[str, dict[str, Any]] = {}
    nodes_by_id = {node["id"]: node for node in data["nodes"]}
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
        if "Rule" in labels:
            item = {
                "rule_id": props["rule_id"],
                "rule_type": props.get("rule_type"),
                "semantic_hint_without_values": re.sub(
                    r"\d+(?:[.~～-]\d+)?",
                    "<number>",
                    props.get("description_ko", ""),
                ),
            }
            if props.get("status") == "VERIFIED":
                rule_ids.append(item)
            elif props.get("status") == "REVIEW_REQUIRED":
                review_required_rules[node["id"]] = item
    group_to_rule: dict[str, str] = {}
    condition_fields_by_rule: dict[str, set[str]] = {
        rule_id: set() for rule_id in review_required_rules
    }
    for relationship in data["relationships"]:
        if (
            relationship["type"] == "HAS_CONDITION_GROUP"
            and relationship["from_id"] in review_required_rules
        ):
            group_to_rule[relationship["to_id"]] = relationship["from_id"]
    for relationship in data["relationships"]:
        if relationship["type"] != "HAS_CONDITION":
            continue
        rule_id = group_to_rule.get(relationship["from_id"])
        condition = nodes_by_id.get(relationship["to_id"])
        if rule_id is None or condition is None:
            continue
        subject_field = condition.get("properties", {}).get("subject_field")
        if isinstance(subject_field, str) and subject_field:
            condition_fields_by_rule[rule_id].add(subject_field)
    review_items = []
    for rule_id, item in review_required_rules.items():
        review_item = dict(item)
        review_item["condition_fields"] = sorted(condition_fields_by_rule[rule_id])
        review_items.append(review_item)
    default_scope: dict[str, Any] = {}
    if len(academic_years) == 1:
        default_scope["academic_year"] = next(iter(academic_years))
    if len(departments) == 1:
        default_scope["department_id"] = departments[0]["department_id"]
    return {
        "academic_years": sorted(academic_years),
        "departments": sorted(departments, key=lambda item: item["department_id"]),
        "verified_rule_identifiers": sorted(rule_ids, key=lambda item: item["rule_id"]),
        "review_required_rule_identifiers": sorted(
            review_items, key=lambda item: item["rule_id"]
        ),
        "supported_filters": sorted(FILTER_BINDINGS),
        "controlled_vocabularies": {
            name: sorted(values) for name, values in catalog.controlled_vocabularies.items()
        },
        "supported_requested_fields": sorted(
            LLM_REQUESTED_FIELDS.intersection(catalog.all_node_properties)
        ),
        "default_scope": default_scope,
    }


def classify_graduation_question(question: str) -> GraduationQuestionClass:
    """Classify required data scope without inferring any academic rule value."""

    if not _GRADUATION_CONTEXT.search(question):
        return GraduationQuestionClass.OTHER
    if _PERSONAL_RECORD.search(question) and _HOLISTIC_JUDGMENT.search(question):
        return GraduationQuestionClass.FULL_PERSONAL_HISTORY
    if _SINGLE_CONDITION.search(question):
        return GraduationQuestionClass.SINGLE_CONDITION_COMPARISON
    if _GENERAL_RULE_CRITERION.search(question):
        return GraduationQuestionClass.GENERAL_RULE
    return GraduationQuestionClass.GENERAL_RULE


def _matches_review_required_rule(
    question: str, context: Mapping[str, Any]
) -> bool:
    """Detect a known unresolved rule family without reading or returning its values."""

    items = context.get("review_required_rule_identifiers")
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, Mapping):
            continue
        fields = item.get("condition_fields")
        if not isinstance(fields, list):
            continue
        for field in fields:
            if not isinstance(field, str):
                continue
            family = field.split(".", 1)[0].upper()
            matcher = _SUBJECT_FIELD_QUESTION_ALIASES.get(family)
            if matcher is not None and matcher.search(question):
                return True
    return False


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
        question = question.strip()
        question_classification = classify_graduation_question(question)
        if question_classification is GraduationQuestionClass.FULL_PERSONAL_HISTORY:
            return PlanningOutcome(
                status=PlanningStatus.UNSUPPORTED,
                unsupported_reason=UnsupportedReason.PERSONAL_HISTORY,
            )
        if (
            question_classification is GraduationQuestionClass.GENERAL_RULE
            and _matches_review_required_rule(question, self.context)
        ):
            return PlanningOutcome(status=PlanningStatus.UNRESOLVED)
        previous_error: str | None = None
        for attempt in range(2):
            generation = self.client.generate_json(
                system_prompt=PLANNER_SYSTEM_PROMPT,
                user_prompt=planner_prompt(
                    question,
                    self.context,
                    question_classification=question_classification.value,
                    previous_error=previous_error,
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
                if isinstance(requested_fields, list):
                    requested_fields = list(requested_fields)
                    for field, pattern in _REQUESTED_FIELD_PATTERNS.items():
                        if pattern.search(question):
                            if field not in requested_fields:
                                requested_fields.append(field)
                            if isinstance(filters, dict):
                                filters.pop(field, None)
                if isinstance(filters, dict):
                    filters = dict(filters)
                    default_scope = self.context.get("default_scope")
                    if isinstance(default_scope, Mapping):
                        filters.setdefault("academic_year", default_scope.get("academic_year"))
                        is_course_query = (
                            selection_mode
                            in {
                                SelectionMode.SINGLE_COURSE.value,
                                SelectionMode.COURSE_LIST.value,
                            }
                            or bool(set(filters).intersection({"name_ko", "course_code"}))
                            or (
                                isinstance(requested_fields, list)
                                and bool(set(requested_fields).intersection(COURSE_REQUEST_FIELDS))
                            )
                        )
                        if is_course_query:
                            filters.setdefault(
                                "department_id", default_scope.get("department_id")
                            )
                    filters = {key: value for key, value in filters.items() if value is not None}
                if self._outside_supported_scope(filters):
                    return PlanningOutcome(status=PlanningStatus.OUT_OF_SCOPE)
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
                    if fully_scoped_course and isinstance(requested_fields, list):
                        status = PlanningStatus.READY
                    else:
                        unsupported_reason = None
                        if status is PlanningStatus.UNSUPPORTED:
                            unsupported_reason = (
                                UnsupportedReason.SINGLE_CONDITION_COMPARISON
                                if question_classification
                                is GraduationQuestionClass.SINGLE_CONDITION_COMPARISON
                                else UnsupportedReason.GENERAL_FEATURE
                            )
                        return PlanningOutcome(
                            status=status,
                            message=self._safe_status_message(status, selection_mode, filters),
                            unsupported_reason=unsupported_reason,
                        )
                # Every executable query in this project is evidence-grounded.  A
                # small local model can correctly identify a fully scoped course
                # request while leaving this flag false (especially after first
                # returning CLARIFICATION_REQUIRED).  Once the deterministic
                # scope checks above establish that the plan is READY, enforce the
                # application contract here instead of retrying the same invalid
                # model payload.  This changes no fact value or answer content.
                evidence_required = True
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
                    "question": question,
                    "intent": payload.get("intent"),
                    "filters": filters,
                    "requested_fields": requested_fields,
                    "evidence_required": evidence_required,
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

    def _outside_supported_scope(self, filters: object) -> bool:
        if not isinstance(filters, Mapping):
            return False
        years = set(self.context.get("academic_years") or ())
        if years and "academic_year" in filters and filters["academic_year"] not in years:
            return True
        departments = {
            item.get("department_id")
            for item in self.context.get("departments", ())
            if isinstance(item, Mapping)
        }
        return bool(departments) and (
            "department_id" in filters
            and filters["department_id"] not in departments
        )

    @staticmethod
    def _safe_status_message(
        status: PlanningStatus, selection_mode: object, filters: object
    ) -> str | None:
        if status is not PlanningStatus.CLARIFICATION_REQUIRED:
            return None
        if selection_mode == SelectionMode.SINGLE_COURSE.value and isinstance(filters, Mapping):
            if not {"name_ko", "course_code"}.intersection(filters):
                return "과목명 또는 학수번호를 입력해 주세요."
        return "질문에서 확인하려는 과목이나 기준을 조금 더 구체적으로 입력해 주세요."
