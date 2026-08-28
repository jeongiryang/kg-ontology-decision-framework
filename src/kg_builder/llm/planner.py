"""Natural-language question to validated QueryPlan using a local model."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from kg_builder.query.clarification import (
    FILTER_IMPLIED_MODE,
    MAX_ROUNDS as MAX_CLARIFICATION_ROUNDS,
    REQUESTED_FIELDS,
    Choice,
    ClarificationChoices,
)
from kg_builder.query.course_names import CourseIdentity, CourseNameResolver
from kg_builder.query.fact_index import (
    FactIndex,
    leading_candidates,
    vocabulary_labels,
)

from kg_builder.query.fact_families import (
    allowed_fields_for_mode,
    allowed_filters_for_mode,
    family_for_mode,
)
from kg_builder.query.query_plan import (
    DEPARTMENT_SCOPED_FIELDS,
    VOCABULARY_FILTERS,
    DEPARTMENT_SCOPED_FILTERS,
    FILTER_BINDINGS,
    QueryPlan,
    QueryPlanError,
    SelectionMode,
)
from kg_builder.query.schema_catalog import DEFAULT_SPEC_PATH, ROOT, SchemaCatalog

from .client import LLMResponseError, StructuredLLMClient
from .models import (
    AttemptOutcome,
    GraduationQuestionClass,
    MissingScope,
    PlanningAttempt,
    PlanningOutcome,
    PlanningStatus,
    UnsupportedReason,
)
from .prompts import (
    DETAIL_SYSTEM_PROMPT,
    INTENT_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    detail_prompt,
    intent_prompt,
    planner_prompt,
)


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
        # 확장 fact family 필드. 어느 필드가 어느 family 것인지는 QueryPlan 이
        # selection_mode 로 닫으므로, 여기서는 선택 가능한 이름만 넓힌다.
        "credit_category",
        "allocated_credits",
        "is_total",
        "raw_label",
        "entry_type",
        "is_required",
        "is_extracurricular",
        "goal_order",
        "goal_scope",
        "field_order",
        "profile_order",
        "course_name_ko",
        "area_raw",
        "recommended_grade_year",
        "recommended_semester",
    }
)
SELECTION_MODES = frozenset(item.value for item in SelectionMode)
# Rule 설명 힌트의 상한. 어느 규칙인지 구분할 정도만 남기고 자른다.
RULE_HINT_MAX_CHARS = 45
# 한 질문에 함께 보여 줄 Rule 후보 수. 전부 실으면 계획 컨텍스트를 규칙 목록이
# 차지해, 규칙과 무관한 과목 질문까지 이수요건으로 오인된다.
RULE_CONTEXT_LIMIT = 10
# Rule 노드에서 계획이 요청할 수 있는 필드. 어떤 규칙이 이 중 무엇을 갖고 있는지는
# 적재 데이터에서 읽는다.
RULE_FIELDS = ("rule_type", "operator", "value", "unit", "description_ko")
# 계획 프롬프트에 싣지 않는 내부 색인. 모델이 고를 것이 아니라 계획을 다듬는 데 쓴다.
INTERNAL_CONTEXT_KEYS = (
    "rule_field_presence",
    "question_matchable_values",
    "course_codes_by_name",
    "course_identities",
    "rule_match_text",
)
# 질문 낱말이 규칙 설명과 겹치는지 볼 때 인정하는 최소 길이. 조사가 붙은 낱말은
# 앞에서부터 잘라 가며 맞춰 본다.
MIN_MATCH_PREFIX = 2
# 모드를 역산할 때 훑어보는 검색 후보 수.
MODE_CANDIDATE_LIMIT = 12
# 같은 fact label 안에서 어느 종류인지를 가르는 필터. 이 필터가 비면 종류가 다른 행이
# 한 결과에 섞이고, 종류마다 채워진 속성이 달라 결과 검증이 조회 전체를 막는다.
FAMILY_DISCRIMINATORS = frozenset({"aggregate_type", "alignment_type", "competency_type"})
# 계획 시도 상한. 계약 위반 문구를 되먹여 스스로 고칠 기회를 준다.
MAX_PLANNING_ATTEMPTS = 3
# 되묻지 않고 자동으로 채택할 단일 후보의 연쇄 상한. 고를 것이 하나뿐인 되묻기가
# 이어질 수 있으나, 매번 계획을 다시 세우므로 무한히 돌지 않도록 막는다.
MAX_AUTO_ADOPTED_CHOICES = 2
# 어떤 필터가 채워지면 그 부족 코드가 해소되는지. 필터로 메울 수 없는 코드는 넣지
# 않으며, 그런 코드는 되묻기로 남는다.
SCOPE_FILLING_FILTERS: dict[MissingScope, tuple[str, ...]] = {
    MissingScope.ACADEMIC_YEAR: ("academic_year",),
    MissingScope.DEPARTMENT: ("department_id",),
    MissingScope.COURSE_IDENTITY: ("name_ko", "course_code"),
    MissingScope.RULE_TOPIC: ("rule_id", "rule_ids"),
}
# 과목을 가리키기만 하는 필드. 이것만 요청하면 답변에 쓸 값이 남지 않는다.
COURSE_IDENTITY_FIELDS = frozenset({"course_code", "name_ko"})
# 한 과목을 답할 때 필요한 필드. 과목명은 답변의 주어를 만드는 데 반드시 있어야
# 하고, 나머지 서술 필드는 적재된 개설 정보 304건 전부에 값이 있어 안전하다.
COURSE_DETAIL_FIELDS = (
    "name_ko",
    "grade_year",
    "semester",
    "credits",
    "completion_type",
)
# 어떤 과목을 묻는지 계획이 실제로 담고 있는지 확인할 때 보는 필터.
COURSE_IDENTIFYING_FILTERS = ("name_ko", "course_code", "course_name_ko")
# 고른 규칙 개수가 곧 조회 종류를 정하는 모드들.
# 한 과목 또는 과목 목록을 조회하는 모드. 질문이 적재된 과목을 지목했을 때 이 모드를
# 필드만 보고 다른 family 로 옮기지 않는다.
COURSE_SELECTION_MODES = frozenset(
    {SelectionMode.SINGLE_COURSE.value, SelectionMode.COURSE_LIST.value}
)
RULE_SELECTION_MODES = frozenset(
    {SelectionMode.SINGLE_RULE.value, SelectionMode.MULTIPLE_RULES.value}
)
_WORD = re.compile(r"[가-힣A-Za-z]{2,}")
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
    "TOEIC": re.compile(r"(?:TOEIC(?!\s*SPEAKING)|토익(?!\s*스피킹))", re.IGNORECASE),
    "TOEIC_SPEAKING": re.compile(r"(?:TOEIC\s*SPEAKING|토익\s*스피킹)", re.IGNORECASE),
    "TOEFL_IBT": re.compile(r"(?:TOEFL|토플)", re.IGNORECASE),
    "TEPS": re.compile(r"(?:(?<!NEW\s)TEPS|텝스)", re.IGNORECASE),
    "NEW_TEPS": re.compile(r"(?:NEW\s*TEPS|뉴\s*텝스)", re.IGNORECASE),
    "OPIC": re.compile(r"(?:OPIC|오픽)", re.IGNORECASE),
    "GTELP": re.compile(r"(?:G-?TELP|지텔프)", re.IGNORECASE),
    "FLEX": re.compile(r"FLEX", re.IGNORECASE),
}
_SUBJECT_FIELD_RULE_LABELS: Mapping[str, str] = {
    "TOEIC": "TOEIC 기준",
    "TOEIC_SPEAKING": "TOEIC Speaking 기준",
    "TOEFL_IBT": "TOEFL 기준",
    "TEPS": "TEPS 기준",
    "NEW_TEPS": "New TEPS 기준",
    "OPIC": "OPIc 기준",
    "GTELP": "G-TELP",
    "FLEX": "FLEX 기준",
}
_REQUESTED_FIELD_PATTERNS: Mapping[str, re.Pattern[str]] = {
    "grade_year": re.compile(r"(?:몇|어느)\s*학년"),
    "semester": re.compile(r"(?:몇|어느)\s*학기"),
    "course_code": re.compile(
        r"(?:과목\s*코드|학수번호).*(?:뭐|무엇|알려|인가|어떤)"
    ),
}
_COURSE_FIELD_HINTS: Mapping[str, re.Pattern[str]] = {
    "course_code": re.compile(r"(?:과목\s*코드|학수번호)"),
    "grade_year": re.compile(r"(?:학년|권장\s*시기)"),
    "semester": re.compile(r"(?:학기|개설\s*시기)"),
    "credits": re.compile(r"(?:몇\s*학점|\d+\s*학점|학점은|학점이|0학점)"),
    "completion_type": re.compile(
        r"(?:이수\s*구분|전공\s*필수|필수\s*전공|전공\s*선택|교양\s*선택|필수\s*과목|"
        r"필수(?:가|인|인지|인가|이야|맞)|졸업)"
    ),
}
_COURSE_LIST_HINT = re.compile(
    r"(?:과목(?:은|이|을|들|\s*목록)|어떤\s*과목|정확히\s*어떤|중\s*어떤|"
    r"과목\s*중|지정된\s*과목|빠뜨|순서|둘\s*다|"
    r"(?:중|가운데).{0,12}(?:하나|아무거나))"
)
_GENERAL_RULE_HINT = re.compile(
    r"(?:이수요건|요건|기준|최소|적어도|최대|초과|면제|의무|대체|균형교양|졸업\s*학점|잔여\s*학점|"
    r"졸업까지|졸업하려|절반|반드시|가능|인정|충족|삭제|채워|들어야|이수해야|남은|"
    r"모자라|부족|얼마나\s*남)"
)
_COURSE_IDENTITY_COMPARISON = re.compile(
    r"(?:같은\s*과목|다른\s*과목|서로\s*다른|표기|이름.{0,12}다르|"
    r"둘\s*다.{0,12}(?:신청|수강|들어))"
)

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
        "course_codes": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "uniqueItems": True,
        },
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
        "area_id": {"type": "string"},
        # 확장 fact family 전용 필터
        "credit_category": {"type": "string"},
        "source_was_blank": {"type": "boolean"},
        "is_total": {"type": "boolean"},
        "recommended_grade_year": {"type": "integer", "minimum": 1, "maximum": 6},
        "recommended_semester": {
            "type": "string",
            "enum": sorted(catalog.controlled_vocabularies["semester"]),
        },
        "entry_type": {
            "type": "string",
            "enum": sorted(catalog.controlled_vocabularies["roadmap_entry_type"]),
        },
        "goal_scope": {
            "type": "string",
            "enum": sorted(catalog.controlled_vocabularies["goal_scope"]),
        },
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
            "missing_scope": {
                "type": "array",
                "items": {"type": "string", "enum": [item.value for item in MissingScope]},
                "uniqueItems": True,
            },
        },
        "required": [
            "status",
            "intent",
            "filters",
            "requested_fields",
            "evidence_required",
            "message",
            "selection_mode",
            "missing_scope",
        ],
        "additionalProperties": False,
    }



def intent_response_schema() -> dict[str, Any]:
    """Stage one: decide what the question asks, nothing else."""

    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": [item.value for item in PlanningStatus]},
            "intent": {"type": ["string", "null"]},
            "selection_mode": {"type": "string", "enum": sorted(SELECTION_MODES)},
            "message": {"type": ["string", "null"]},
            "missing_scope": {
                "type": "array",
                "items": {"type": "string", "enum": [item.value for item in MissingScope]},
                "uniqueItems": True,
            },
        },
        "required": ["status", "intent", "selection_mode", "message", "missing_scope"],
        "additionalProperties": False,
    }


def detail_response_schema(catalog: SchemaCatalog, selection_mode: str) -> dict[str, Any]:
    """Stage two: only the filters and fields the chosen mode is allowed to use.

    모드가 정해진 뒤에는 선택지를 그 모드의 것으로 좁힌다. 전체 목록을 계속 보여 주면
    작은 모델이 다른 모드의 필드를 섞어 계약 위반을 만든다.
    """

    full = planner_response_schema(catalog)
    filter_properties = full["properties"]["filters"]["properties"]
    allowed_filters = allowed_filters_for_mode(selection_mode)
    scoped_filters = {
        name: spec for name, spec in filter_properties.items() if name in allowed_filters
    }
    allowed_fields = allowed_fields_for_mode(selection_mode)
    field_enum = full["properties"]["requested_fields"]["items"]["enum"]
    if allowed_fields is not None:
        scoped = [name for name in field_enum if name in allowed_fields]
        field_enum = scoped or field_enum
    return {
        "type": "object",
        "properties": {
            "filters": {
                "type": "object",
                "properties": scoped_filters or filter_properties,
                "additionalProperties": False,
            },
            "requested_fields": {
                "type": "array",
                "items": {"type": "string", "enum": field_enum},
                "minItems": 1,
                "uniqueItems": True,
            },
        },
        "required": ["filters", "requested_fields"],
        "additionalProperties": False,
    }


def build_planner_context(
    catalog: SchemaCatalog,
    data_path: Path = DEFAULT_VERIFIED_DATA,
) -> dict[str, Any]:
    data = json.loads(data_path.read_text(encoding="utf-8"))
    departments = []
    rule_ids = []
    rule_field_presence: dict[str, tuple[str, ...]] = {}
    rule_match_text: dict[str, str] = {}
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
            # 설명을 통째로 실으면 계획 컨텍스트의 대부분을 Rule 목록이 차지한다.
            # 8K 창에서는 그만큼 질문에 쓸 자리가 줄고, 과목 질문까지 이수요건으로
            # 오인된다. 어느 규칙인지 고를 수 있을 만큼만 남긴다.
            hint = re.sub(
                r"\d+(?:[.~～-]\d+)?",
                "<number>",
                props.get("description_ko", ""),
            )
            if len(hint) > RULE_HINT_MAX_CHARS:
                hint = hint[:RULE_HINT_MAX_CHARS].rstrip() + "…"
            item = {
                "rule_id": props["rule_id"],
                "rule_type": props.get("rule_type"),
                "semantic_hint_without_values": hint,
            }
            if props.get("status") == "VERIFIED":
                rule_ids.append(item)
            elif props.get("status") == "REVIEW_REQUIRED":
                review_required_rules[node["id"]] = item
                continue
            else:
                continue
            # 어떤 규칙이 어떤 필드를 실제로 갖고 있는지. 수치가 아닌 규칙은 value·
            # unit·operator 가 비어 있고, 원문의 빈 값을 0 으로 바꾸지 않는 것이 이
            # 저장소의 계약이라 한 건만 비어도 결과 검증이 전체 조회를 막는다. 없는
            # 필드를 애초에 요청하지 않도록 여기서 적재 데이터에서 읽어 둔다.
            rule_field_presence[props["rule_id"]] = tuple(
                name for name in RULE_FIELDS if props.get(name) is not None
            )
            # 질문과 규칙을 대조할 때 쓰는 원문. 프롬프트에는 싣지 않는다.
            rule_match_text[props["rule_id"]] = " ".join(
                str(props.get(name) or "")
                for name in ("description_ko", "rule_type", "area_id")
            )
    # 질문에 원문 표기 그대로 등장할 수 있는 필터 값. 코드에 값을 적지 않고 bundle 에서
    # 읽으며, 어떤 필터에 쓸 수 있는지는 fact family 선언이 정한다.
    filterable_values = {
        "credit_category": sorted(
            {
                node["properties"]["credit_category"]
                for node in data["nodes"]
                if "CreditAllocation" in node["labels"]
                and isinstance(node["properties"].get("credit_category"), str)
                and node["properties"].get("status") == "VERIFIED"
            }
        )
    }
    # 질문에 원문 표기 그대로 나올 수 있으나 프롬프트에 싣기에는 수가 많은 값.
    # 계획 프롬프트에 넣지 않고, 질문 문자열에 그대로 있을 때만 필터로 채택한다.
    question_matchable_values = {
        "name_ko": sorted(
            {
                node["properties"]["name_ko"]
                for node in data["nodes"]
                if "Course" in node["labels"]
                and isinstance(node["properties"].get("name_ko"), str)
                and node["properties"]["name_ko"]
            }
        )
    }
    # 어느 과목명이 어느 학수번호인지. 계획 모델이 지어낸 학수번호로 다른 과목을 답하는
    # 것을 막는 대조용이며, 프롬프트에는 싣지 않는다(동명 과목이 있어 값은 목록이다).
    course_codes_by_name: dict[str, list[str]] = {}
    course_identities: list[dict[str, Any]] = []
    curriculum_scopes = {
        node["id"]: node["properties"].get("scope_type")
        for node in data["nodes"]
        if "CurriculumVersion" in node["labels"]
    }
    offering_scopes = {
        relationship["to_id"]: curriculum_scopes.get(relationship["from_id"])
        for relationship in data["relationships"]
        if relationship["type"] == "HAS_OFFERING"
    }
    scopes_by_course: dict[str, set[str]] = {}
    for relationship in data["relationships"]:
        if relationship["type"] != "OF_COURSE":
            continue
        scope = offering_scopes.get(relationship["from_id"])
        if isinstance(scope, str):
            scopes_by_course.setdefault(relationship["to_id"], set()).add(scope)
    for node in data["nodes"]:
        if "Course" not in node["labels"]:
            continue
        props = node["properties"]
        name = props.get("name_ko")
        code = props.get("course_code")
        if isinstance(name, str) and name and isinstance(code, str) and code:
            course_codes_by_name.setdefault(name, []).append(code)
            course_id = props.get("course_id")
            if isinstance(course_id, str) and course_id:
                course_identities.append(
                    {
                        "course_id": course_id,
                        "course_code": code,
                        "name_ko": name,
                        "scope_types": sorted(scopes_by_course.get(course_id, ())),
                    }
                )
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
        "question_matchable_values": question_matchable_values,
        "course_codes_by_name": course_codes_by_name,
        "course_identities": sorted(
            course_identities, key=lambda item: (item["course_code"], item["course_id"])
        ),
        "filterable_values": filterable_values,
        "departments": sorted(departments, key=lambda item: item["department_id"]),
        "verified_rule_identifiers": sorted(rule_ids, key=lambda item: item["rule_id"]),
        "rule_field_presence": rule_field_presence,
        "rule_match_text": rule_match_text,
        "review_required_rule_identifiers": sorted(
            review_items, key=lambda item: item["rule_id"]
        ),
        "supported_filters": sorted(FILTER_BINDINGS),
        # 필터로 실제 쓰이는 어휘만 싣는다. 나머지는 계획에 필요 없고 자리만 차지한다.
        "controlled_vocabularies": {
            name: sorted(catalog.controlled_vocabularies[name])
            for name in sorted(set(VOCABULARY_FILTERS.values()))
            if name in catalog.controlled_vocabularies
        },
        "supported_requested_fields": sorted(
            LLM_REQUESTED_FIELDS.intersection(catalog.all_node_properties)
        ),
        "default_scope": default_scope,
    }


def _relevant_rules(question: str, rules: list[Any], limit: int) -> list[Any]:
    """Keep the rule candidates whose wording overlaps the question.

    질문에 없는 규칙까지 모두 보여 주면 계획 모델이 규칙 목록에 끌려간다. 규칙 설명과
    질문이 공유하는 낱말 수로 추려, 관련 있는 것부터 남긴다. 어느 규칙이 답인지는
    고르지 않으며, 뒤의 계약 검증과 근거 검증은 그대로 적용된다.
    """

    if len(rules) <= limit:
        return rules
    words = {word for word in _WORD.findall(question)}
    scored: list[tuple[int, int, Any]] = []
    for index, rule in enumerate(rules):
        hint = str(rule.get("semantic_hint_without_values", "")) + str(
            rule.get("rule_type", "")
        )
        score = sum(1 for word in words if word in hint)
        scored.append((-score, index, rule))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [rule for _, _, rule in scored[:limit]]

@lru_cache(maxsize=1)
def _default_choices() -> ClarificationChoices:
    """Build the clarification choice source once per process from the loaded data."""

    bundle = json.loads(DEFAULT_VERIFIED_DATA.read_text(encoding="utf-8"))
    spec = json.loads(DEFAULT_SPEC_PATH.read_text(encoding="utf-8"))
    return ClarificationChoices(bundle, _default_fact_index(), spec)


@lru_cache(maxsize=1)
def _default_fact_index() -> FactIndex:
    """Build the retrieval index once per process from the loaded bundle."""

    bundle = json.loads(DEFAULT_VERIFIED_DATA.read_text(encoding="utf-8"))
    spec = json.loads(DEFAULT_SPEC_PATH.read_text(encoding="utf-8"))
    return FactIndex.from_bundle(bundle, vocabulary_labels(spec))


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
        hint = str(item.get("semantic_hint_without_values") or "")
        terms = {
            term.rstrip("의은는이가을를")
            for term in _WORD.findall(hint)
            if len(term.rstrip("의은는이가을를")) >= 4
        }
        if sum(term in question for term in terms) >= 2:
            return True
    return False


class LocalQueryPlanner:
    def __init__(
        self,
        client: StructuredLLMClient,
        *,
        catalog: SchemaCatalog | None = None,
        planner_context: Mapping[str, Any] | None = None,
        fact_index: FactIndex | None = None,
    ):
        self.client = client
        self.catalog = catalog or SchemaCatalog.from_spec(DEFAULT_SPEC_PATH)
        self.context = dict(planner_context or build_planner_context(self.catalog))
        # 색인은 계획 컨텍스트에 넣지 않고 따로 든다. 프롬프트로 나갈 값이 아니라
        # 계획을 다듬는 데만 쓰는 내부 자료이기 때문이다.
        self.fact_index = fact_index if fact_index is not None else _default_fact_index()
        self.choices = _default_choices() if fact_index is None else None
        identities = self.context.get("course_identities")
        self.course_resolver = CourseNameResolver(
            CourseIdentity(
                item["course_id"],
                item["course_code"],
                item["name_ko"],
                tuple(
                    scope
                    for scope in item.get("scope_types", ())
                    if isinstance(scope, str)
                ),
            )
            for item in identities or ()
            if isinstance(item, Mapping)
            and all(
                isinstance(item.get(key), str) and item.get(key)
                for key in ("course_id", "course_code", "name_ko")
            )
        )
        # 이번 요청에서 사용자가 고른 값. plan() 이 매 호출마다 다시 채운다.
        self._resolved: dict[str, Any] = {}

    @staticmethod

    def _missing_scope(payload: Mapping[str, Any]) -> tuple[MissingScope, ...]:
        raw = payload.get("missing_scope")
        if not isinstance(raw, list):
            return ()
        found: list[MissingScope] = []
        for item in raw:
            try:
                code = MissingScope(item)
            except (TypeError, ValueError):
                continue
            if code not in found:
                found.append(code)
        return tuple(found)

    def _adopt_question_values(
        self, question: str, filters: dict[str, Any], selection_mode: Any
    ) -> dict[str, Any]:
        """Adopt filter values the question already states verbatim.

        계획 모델이 질문에 나온 범주를 필터에 넣지 않으면, 그 범주가 빠진 채 다른
        범주까지 조회돼 묻지 않은 것을 답하게 된다. 질문 문자열에 데이터 값이 그대로
        들어 있으면 그것은 추정이 아니라 사용자가 말한 조건이므로 여기서 채운다.

        값 목록은 bundle 에서 읽고, 어떤 필터를 쓸 수 있는지는 모드 선언이 정한다.
        질문별 분기나 값 목록 하드코딩은 없다.
        """

        allowed = allowed_filters_for_mode(selection_mode)
        values_by_filter: dict[str, Any] = {}
        for key in ("filterable_values", "question_matchable_values"):
            source = self.context.get(key)
            if isinstance(source, Mapping):
                values_by_filter.update(source)
        if not values_by_filter:
            return filters
        adopted = dict(filters)
        for name, values in values_by_filter.items():
            if name in adopted or name not in allowed:
                continue
            if not isinstance(values, list):
                continue
            # 겹치는 표기가 있으면 가장 구체적인(긴) 값을 고른다.
            matched = sorted(
                (value for value in values if isinstance(value, str) and value and value in question),
                key=len,
                reverse=True,
            )
            if matched:
                adopted[name] = matched[0]
        return self._adopt_vocabulary_terms(question, adopted, allowed)

    def _adopt_vocabulary_terms(
        self, question: str, filters: dict[str, Any], allowed: frozenset[str]
    ) -> dict[str, Any]:
        """Adopt controlled-vocabulary filters the question names in Korean.

        통제어휘의 코드(``MAJOR_REQUIRED``)는 질문에 나올 리 없지만, 온톨로지가 그
        코드에 붙여 둔 한국어 표기(``전공필수``)는 그대로 나온다. 계획 모델이 이 값을
        필터에 넣지 않으면 이수구분으로 좁혀지지 않은 채 학과의 모든 과목이 조회돼
        묻지 않은 것까지 답하게 된다.

        표기는 명세에서 읽으며 코드에 적지 않는다. 질문에 그 표기가 그대로 있을 때만
        채우므로 추정이 아니라 사용자가 말한 조건을 옮기는 것이다.
        """

        adopted = dict(filters)
        for name, terms in self._vocabulary_terms().items():
            if name in adopted or name not in allowed:
                continue
            matched = sorted(
                (term for term in terms if term in question), key=len, reverse=True
            )
            if matched:
                adopted[name] = terms[matched[0]]
        return adopted

    @lru_cache(maxsize=1)
    def _vocabulary_terms(self) -> Mapping[str, Mapping[str, str]]:
        """Map each vocabulary filter's Korean wording back to its declared code."""

        spec = json.loads(DEFAULT_SPEC_PATH.read_text(encoding="utf-8"))
        vocabularies = spec.get("controlled_vocabularies")
        if not isinstance(vocabularies, Mapping):
            return {}
        terms: dict[str, dict[str, str]] = {}
        for name, vocabulary in VOCABULARY_FILTERS.items():
            declared = vocabularies.get(vocabulary)
            if not isinstance(declared, Mapping):
                continue
            for item in declared.get("values", ()):
                if not isinstance(item, Mapping):
                    continue
                value, label = item.get("value"), item.get("description_ko")
                if isinstance(value, str) and isinstance(label, str) and label:
                    terms.setdefault(name, {})[label] = value
        return terms

    def _complete_scope(
        self,
        filters: dict[str, Any],
        requested_fields: Any,
        family: Any,
    ) -> dict[str, Any]:
        """Close the query scope when the loaded data leaves exactly one choice.

        계획 모델은 질문에 학년도나 학과가 없으면 범위 필터를 자주 빠뜨린다. 적재된
        후보가 하나뿐이면 그것을 고르는 일은 추정이 아니라 확정이므로 여기서 채운다.
        후보가 둘 이상이면 채우지 않고, 계약 위반으로 되돌려 되묻게 한다.

        조회 범위만 바꾸며 답변 값은 바꾸지 않는다. 채운 범위로 조회한 결과도 종전과
        똑같이 VERIFIED 사실과 Evidence 검증을 거친다.
        """

        completed = dict(filters)
        years = self.context.get("academic_years")
        if (
            "academic_year" not in completed
            and isinstance(years, list)
            and len(years) == 1
        ):
            completed["academic_year"] = years[0]

        fields = set(requested_fields) if isinstance(requested_fields, list) else set()
        needs_department = (
            family is not None
            or bool(set(completed) & DEPARTMENT_SCOPED_FILTERS)
            or bool(fields & DEPARTMENT_SCOPED_FIELDS)
        )
        departments = self.context.get("departments")
        if (
            needs_department
            and "department_id" not in completed
            and isinstance(departments, list)
            and len(departments) == 1
            and isinstance(departments[0], Mapping)
        ):
            department_id = departments[0].get("department_id")
            if isinstance(department_id, str) and department_id:
                completed["department_id"] = department_id
        return completed

    def _context_for(self, question: str) -> dict[str, Any]:
        """Trim the shared context down to what this question can use."""

        context = {
            name: value
            for name, value in self.context.items()
            if name not in INTERNAL_CONTEXT_KEYS
        }
        rules = context.get("verified_rule_identifiers")
        if isinstance(rules, list):
            context["verified_rule_identifiers"] = _relevant_rules(
                question, rules, RULE_CONTEXT_LIMIT
            )
        return context

    def _request_detail(
        self, question: str, selection_mode: Any, previous_error: str | None
    ) -> Mapping[str, Any]:
        generation = self.client.generate_json(
            system_prompt=DETAIL_SYSTEM_PROMPT,
            user_prompt=detail_prompt(
                question,
                self.context,
                str(selection_mode),
                previous_error=previous_error,
            ),
            response_schema=detail_response_schema(self.catalog, str(selection_mode)),
        )
        payload = generation.payload
        return payload if isinstance(payload, Mapping) else {}

    def _verified_rule_ids(self) -> list[str]:
        """Every VERIFIED rule identifier the loaded bundle declares."""

        rules = self.context.get("verified_rule_identifiers")
        if not isinstance(rules, list):
            return []
        found: list[str] = []
        for rule in rules:
            if not isinstance(rule, Mapping):
                continue
            rule_id = rule.get("rule_id")
            if isinstance(rule_id, str) and rule_id and rule_id not in found:
                found.append(rule_id)
        return found

    @staticmethod
    def _scope_settled(
        codes: tuple[MissingScope, ...], plan_payload: Mapping[str, Any]
    ) -> bool:
        """Say whether the completed plan already carries what the model asked for.

        모델은 질문에 답이 있는데도 되묻는 일이 잦다. 계획을 다듬은 뒤 그 필터에 부족
        하다던 값이 실제로 들어 있으면, 되물을 것이 남아 있지 않다. 어떤 필터가 어떤
        코드를 채우는지만 보고 판단하며 질문 문자열을 다시 읽지 않는다.

        고른 모드가 아예 표현할 수 없는 코드는 이 계획의 차단 사유가 아니다. 과목 질의에
        붙은 RULE_TOPIC 처럼, 모델이 관성으로 남긴 코드까지 되묻기로 취급하면 답할 수
        있는 질문이 막힌다. QUESTION_INTENT 처럼 어떤 필터로도 메울 수 없는 코드가
        하나라도 있으면 되묻기를 그대로 둔다.
        """

        if not codes:
            return False
        filters = plan_payload.get("filters") or {}
        allowed = allowed_filters_for_mode(plan_payload.get("selection_mode"))
        for code in codes:
            names = SCOPE_FILLING_FILTERS.get(code)
            if not names:
                return False
            if any(name in filters for name in names):
                continue
            if any(name in allowed for name in names):
                return False
        return True

    def _closable_scope(self, codes: tuple[MissingScope, ...]) -> bool:
        """Say whether the loaded data already settles every missing scope code.

        모델이 학년도나 학과를 물어보겠다고 해도, 적재된 후보가 하나뿐이면 그것은
        되물을 것이 아니라 확정된 값이다. 후보가 둘 이상인 코드가 하나라도 있으면
        되묻기를 그대로 둔다.
        """

        if not codes:
            return False
        years = self.context.get("academic_years")
        departments = self.context.get("departments")
        for code in codes:
            if code is MissingScope.ACADEMIC_YEAR:
                if not (isinstance(years, list) and len(years) == 1):
                    return False
            elif code is MissingScope.DEPARTMENT:
                if not (isinstance(departments, list) and len(departments) == 1):
                    return False
            else:
                return False
        return True

    def _mode_for_fields(
        self, question: str, selection_mode: Any, requested_fields: Any
    ) -> Any:
        """Correct the mode when the requested fields belong to exactly one family.

        작은 모델은 물음의 종류를 자주 SINGLE_COURSE 로 몰아 놓고, 정작 요청 필드는
        옳은 fact family 의 것을 고른다. 어느 필드가 어느 family 소유인지는 온톨로지
        선언이 이미 정하고 있으므로, 요청 필드를 전부 담을 수 있는 모드가 하나뿐이면
        그 모드로 고친다.

        family 가 늘면서 필드만으로는 후보가 하나로 좁혀지지 않는 경우가 생겼다.
        ``name_ko`` 처럼 여러 family 가 함께 쓰는 필드가 그렇다. 그때는 질문 표기와
        적재된 사실을 대조해 얻은 후보 모드로 동점을 깬다. 분류를 더 잘하라고 요구하는
        대신 데이터와 맞춰 보는 것이며, 고친 계획도 같은 계약 검증을 다시 거친다.

        지금 모드로도 충분하면 손대지 않는다.

        **보정이 거꾸로 도는 경우가 있다.** 모델이 종류는 맞게 고르고 필드를 틀리게
        고르면, 필드를 믿는 이 보정이 맞는 종류를 뒤집는다. `자료구조는 몇 학년 몇
        학기에 개설되나?` 에 모델이 `SINGLE_COURSE` + `recommended_grade_year` 를 내면
        (권장 교양 과목 전용 필드다) 계획이 통째로 권장 과목 조회로 바뀌어, 묻지 않은
        사실을 답하게 된다. 3회 시도 모두 같은 값이 나와 재시도로도 벗어나지 못했다
        (2026-08-15 실측).

        그래서 신호가 둘 이상 일치하면 필드 하나에 지지 않는다. 질문이 적재된 과목을
        이름으로 지목했고 모델도 과목 조회라고 했다면, 그 둘이 일치하는 쪽을 따른다.
        과목명이 적재 데이터에 실제로 있는지 확인한 값이라 필드보다 근거가 세다. 틀린
        필드는 뒤에서 그 모드가 줄 수 있는 것만 남기고 채워 넣으므로 버려도 손해가 없다.
        """

        if not isinstance(requested_fields, list) or not requested_fields:
            return selection_mode
        if selection_mode in COURSE_SELECTION_MODES and self._names_a_course(question):
            return selection_mode
        wanted = {name for name in requested_fields if isinstance(name, str)}
        if not wanted:
            return selection_mode
        current = allowed_fields_for_mode(selection_mode)
        if current is not None and wanted <= set(current):
            return selection_mode
        candidates = [
            mode.value
            for mode in SelectionMode
            if (allowed := allowed_fields_for_mode(mode.value)) is not None
            and wanted <= set(allowed)
        ]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            allowed_by_question = [
                mode.value
                for mode in self._modes_from_question(question)
                if mode.value in candidates
            ]
            if allowed_by_question:
                return allowed_by_question[0]
        return selection_mode

    def _drop_absent_rule_facts(
        self,
        selection_mode: Any,
        filters: Any,
        requested_fields: Any,
    ) -> tuple[Any, dict[str, Any], Any]:
        """Ask only the rules that actually carry every requested field.

        수치가 아닌 이수요건은 value·unit·operator 가 비어 있다. 원문의 빈 값을 0 으로
        바꾸지 않는 것이 이 저장소의 데이터 계약이므로, 그런 규칙이 한 건이라도 섞이면
        결과 검증이 조회 전체를 막아 아무 답도 나가지 못한다.

        없는 사실을 만들어 채우는 대신, 그 필드를 갖지 않은 규칙을 조회 대상에서 뺀다.
        어떤 규칙이 무엇을 갖는지는 적재된 bundle 에서 읽으며 질문을 보지 않는다.
        남는 규칙이 없으면 손대지 않고 계약 검증에 맡긴다.
        """

        filters = dict(filters) if isinstance(filters, Mapping) else {}
        rule_ids = filters.get("rule_ids")
        if not isinstance(rule_ids, list) or not isinstance(requested_fields, list):
            return selection_mode, filters, requested_fields
        presence = self.context.get("rule_field_presence")
        if not isinstance(presence, Mapping):
            return selection_mode, filters, requested_fields
        needed = {
            name for name in requested_fields if name in RULE_FIELDS
        }
        if not needed:
            return selection_mode, filters, requested_fields
        kept = [
            rule_id
            for rule_id in rule_ids
            if isinstance(rule_id, str) and needed.issubset(set(presence.get(rule_id, ())))
        ]
        if not kept or len(kept) == len(rule_ids):
            return selection_mode, filters, requested_fields
        filters["rule_ids"] = kept
        # 하나만 남으면 여러 건 모드의 계약을 더는 만족하지 못한다. 같은 조회를
        # 단건 모드로 옮긴다.
        if len(kept) == 1 and selection_mode == SelectionMode.MULTIPLE_RULES.value:
            selection_mode = SelectionMode.SINGLE_RULE.value
        return selection_mode, filters, requested_fields

    def _redundant_clarification(
        self,
        question: str,
        payload: Mapping[str, Any],
        missing: tuple[MissingScope, ...],
    ) -> str | None:
        """Explain why this clarification did not need to be asked, if it did not.

        모델의 모호성 판단을 조용히 READY 로 바꾸지는 않는다. 다만 두 경우에는 되묻지
        않아도 되는 것이 확인되므로, 그 사실을 알려 주고 한 번 더 계획하게 한다.

        - 이미 계획 계약을 만족하는 응답이면, 남은 후보가 몇 개인지는 조회 뒤의 안정
          식별자 검증이 판정한다. 계획 단계에서 미리 짐작할 일이 아니다.
        - 적재된 데이터가 그 범위 코드의 후보를 하나만 남기면 그것은 확정된 값이다.

        두 번째 되묻기는 그대로 안전한 정지로 둔다.
        """

        try:
            self._normalise(question, payload)
        except (ValueError, QueryPlanError):
            pass
        else:
            return (
                "This plan already satisfies the QueryPlan contract. Return READY; the "
                "database result validator counts how many stable identities match. Do "
                "not guess whether the request is ambiguous."
            )
        if self._closable_scope(missing):
            return (
                "The loaded data leaves exactly one choice for "
                f"{', '.join(code.value for code in missing)}, so it is already settled. "
                "Do not ask the user for it. Return READY with the filters and fields "
                "the question needs."
            )
        return None

    @staticmethod
    def _record(
        index: int,
        outcome: AttemptOutcome,
        payload: Mapping[str, Any],
        *,
        contract_error: str | None = None,
    ) -> PlanningAttempt:
        filters = payload.get("filters")
        fields = payload.get("requested_fields")
        missing = payload.get("missing_scope")
        return PlanningAttempt(
            attempt=index + 1,
            outcome=outcome,
            status=payload.get("status") if isinstance(payload.get("status"), str) else None,
            selection_mode=(
                payload.get("selection_mode")
                if isinstance(payload.get("selection_mode"), str)
                else None
            ),
            filter_names=tuple(sorted(filters)) if isinstance(filters, Mapping) else (),
            requested_fields=(
                tuple(item for item in fields if isinstance(item, str))
                if isinstance(fields, list)
                else ()
            ),
            missing_scope=(
                tuple(item for item in missing if isinstance(item, str))
                if isinstance(missing, list)
                else ()
            ),
            contract_error=contract_error,
        )

    def _normalise(
        self, question: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Turn one model payload into a plan payload, or raise the contract reason.

        여기서 던지는 문구는 다음 시도의 프롬프트로 그대로 되먹여진다. 사용자 화면에는
        나가지 않는다.
        """

        selection_mode = payload.get("selection_mode")
        filters = payload.get("filters")
        requested_fields = payload.get("requested_fields")
        # 사용자가 되묻기에서 고른 값은 계약 검사보다 **먼저** 반영한다. 나중에
        # 반영하면 모델이 고른 모드로 계약을 먼저 검사해 버려, 사용자의 선택과
        # 맞지 않는다는 이유로 계획 전체가 버려진다(규칙 하나를 골랐는데 모델이
        # MULTIPLE_RULES 를 고집하는 경우가 그렇다).
        if self._resolved:
            filters = dict(filters) if isinstance(filters, Mapping) else {}
            for name, value in self._resolved.items():
                # 조회 종류와 요청 필드는 필터가 아니다. 필터 자리에 넣으면 뒤에서
                # 걸러지긴 하지만, 애초에 다른 것이므로 여기서 가른다.
                if name not in {"selection_mode", REQUESTED_FIELDS}:
                    filters[name] = value
            chosen = self._resolved.get("selection_mode")
            if not isinstance(chosen, str):
                for name in self._resolved:
                    implied = FILTER_IMPLIED_MODE.get(name)
                    if implied is not None:
                        chosen = implied.value
                        break
            if isinstance(chosen, str):
                selection_mode = chosen
        # 요청 필드로 모드를 고쳐 주는 보정은 사용자가 조회 종류를 직접 고르지 않았을
        # 때만 쓴다. 되묻기 payload 는 계획을 접은 껍데기라 필드가 앞 시도의 잔재일 수
        # 있고, 그 필드로 사용자의 선택을 뒤집으면 고른 것과 다른 사실을 조회하게 된다.
        if not self._settled_mode():
            selection_mode = self._mode_for_fields(
                question, selection_mode, requested_fields
            )
        rule_ids = filters.get("rule_ids") if isinstance(filters, Mapping) else None
        if selection_mode == SelectionMode.SINGLE_RULE.value and (
            not isinstance(filters, Mapping)
            or not (
                isinstance(filters.get("rule_id"), str)
                or (isinstance(rule_ids, list) and len(rule_ids) == 1)
            )
        ):
            raise QueryPlanError("SINGLE_RULE requires one rule_id or one rule_ids entry")
        if selection_mode == SelectionMode.MULTIPLE_RULES.value and (
            not isinstance(rule_ids, list) or len(rule_ids) < 2
        ):
            raise QueryPlanError("MULTIPLE_RULES requires at least two rule_ids entries")
        # A numeric Rule value is not a grounded semantic Claim without its
        # operator, unit, type, and verified description.  Small models sometimes
        # request only ``value``; expand the structural result contract here
        # without adding any answer value or question-specific branch.
        if isinstance(requested_fields, list) and "value" in requested_fields:
            requested_fields = list(
                dict.fromkeys(
                    requested_fields + ["rule_type", "operator", "unit", "description_ko"]
                )
            )
        # 과목 목록 Claim 은 모든 행의 이수구분을 요구한다. 계획이 이수구분으로 좁히지
        # 않았고 요청 필드에도 없으면 그 값이 비어 와 답변 단계에서 막힌다. 값을 정하는
        # 것이 아니라 이미 필요한 필드를 채우는 구조적 보강이다.
        # 한 과목을 물으면 그 과목의 개설 정보를 온전히 답한다. 계획 모델이 고른 한
        # 속성만 답하면 `자료구조는 뭐지?` 에 `3학점입니다` 로 끝나, 틀리지는 않지만
        # 물은 것에 못 미친다. 식별 필드만 남아 Claim 이 비는 경우도 함께 막는다.
        # 개설 정보의 서술 필드는 적재된 304건 전부 값이 있어 보강해도 안전하다.
        if (
            selection_mode == SelectionMode.SINGLE_COURSE.value
            and isinstance(requested_fields, list)
            and requested_fields
        ):
            picked = self._resolved.get(REQUESTED_FIELDS)
            if isinstance(picked, list) and picked:
                # 사용자가 무엇을 물을지 골랐으면 그것만 답한다. 개설 정보를 통째로
                # 답하면 고른 것이 무의미해진다. 과목명은 답의 주어라 함께 남긴다.
                requested_fields = list(dict.fromkeys(["name_ko", *picked]))
            elif self._question_names_course_aspect(question):
                # 질문이 이미 원하는 출력 필드를 말했으면 그 필드만 유지한다.
                # 학년·학기·학수번호를 검색 조건으로 되묻거나, 묻지 않은 개설
                # 정보를 통째로 덧붙이지 않는다.
                requested_fields = list(dict.fromkeys(requested_fields))
            else:
                requested_fields = list(
                    dict.fromkeys(list(requested_fields) + list(COURSE_DETAIL_FIELDS))
                )
        # 요청 필드가 아예 비어 있으면 손대지 않는다. 그건 계획이 무엇을 물었는지
        # 정하지 못했다는 뜻이고, 그 계획은 계약 위반으로 거부되는 편이 맞다.
        # 과목 목록은 과목명이 있어야 답이 된다. 이수구분과 같은 이유의 구조적 보강이며,
        # 계획 모델이 `credits` 만 요청해도 어느 과목의 학점인지 말할 수 있어야 한다.
        if (
            selection_mode == SelectionMode.COURSE_LIST.value
            and isinstance(requested_fields, list)
            and requested_fields
        ):
            requested_fields = list(
                dict.fromkeys(list(requested_fields) + ["name_ko", "completion_type"])
            )
        # 확장 family 도 같은 이유로 구조적 보강만 한다. 문장을 만들 수 없는 필드
        # 조합이 오면 답변 단계에서 안전 실패로 끝나므로, 질문별 분기 없이 family 가
        # 선언한 최소 필드를 채워 준다.
        family = family_for_mode(selection_mode)
        if family is not None and isinstance(requested_fields, list):
            requested_fields = self._fields_the_family_owns(family, requested_fields)
            requested_fields = list(
                dict.fromkeys(list(requested_fields) + list(family.mandatory_fields))
            )
        filters = dict(filters) if isinstance(filters, Mapping) else {}
        if family is not None:
            filters = {**family.default_filters, **filters}
        selection_mode, filters, requested_fields = self._drop_absent_rule_facts(
            selection_mode, filters, requested_fields
        )
        filters = self._adopt_question_values(question, filters, selection_mode)
        if family is not None:
            filters = self._adopt_family_discriminators(question, family, filters)
            requested_fields = self._fields_every_fact_has(
                family, filters, requested_fields
            )
        filters = self._complete_scope(filters, requested_fields, family)
        # 고른 모드가 쓸 수 없는 필터는 조회 경로에 붙일 자리가 없어 Cypher 생성에서
        # 반드시 실패한다(예: Rule 질의에 department_id). 그 필터는 그 모드에서
        # 애초에 무의미하므로 떨어뜨리고, 남은 조건으로 조회한다. 조회 범위만 넓어질
        # 뿐 없는 사실을 만들지 않으며, 결과는 그대로 근거 검증을 거친다.
        # 사용자가 선택지에서 고른 값은 계획 모델보다 우선한다. 모드까지 고른 경우
        # 그 모드로 확정하고, 나머지는 그 모드가 허용하는 필터만 남긴다.
        chosen_mode = self._resolved.get("selection_mode")
        if chosen_mode is None:
            # 모드를 직접 고르지 않았어도, 고른 값이 어떤 조회인지는 선택지 선언이
            # 알고 있다. 사용자가 과목 하나를 골랐으면 그건 과목 조회다. 지금 모드가
            # 그 필터를 우연히 허용하더라도(권장과목도 course_code 를 쓴다) 다른
            # 사실을 조회하게 되므로, 고른 값이 뜻하는 모드를 따른다.
            for name in self._resolved:
                implied = FILTER_IMPLIED_MODE.get(name)
                if implied is not None:
                    chosen_mode = implied.value
                    break
        if isinstance(chosen_mode, str) and chosen_mode != selection_mode:
            selection_mode = chosen_mode
            family = family_for_mode(selection_mode)
            # 모드를 바꿨으면 요청 필드도 그 모드가 줄 수 있는 것만 남긴다. 이전
            # 모드의 필드를 그대로 두면 조회문을 만들 수 없다.
            allowed_fields = allowed_fields_for_mode(selection_mode)
            if allowed_fields is not None and isinstance(requested_fields, list):
                requested_fields = [
                    name for name in requested_fields if name in allowed_fields
                ]
            if selection_mode == SelectionMode.SINGLE_COURSE.value:
                requested_fields = list(
                    dict.fromkeys(
                        list(requested_fields or []) + list(COURSE_DETAIL_FIELDS)
                    )
                )
            elif selection_mode in {
                SelectionMode.SINGLE_RULE.value,
                SelectionMode.MULTIPLE_RULES.value,
            } and not requested_fields:
                # 규칙 모드로 옮기면 이전 모드의 필드가 모두 걸러져 남는 것이 없을 수
                # 있다. 고른 규칙이 실제로 갖고 있는 필드만 요청한다. 없는 필드를
                # 요청하면 결과 검증이 조회 전체를 막는다.
                chosen_rules = filters.get("rule_ids") or (
                    [filters["rule_id"]] if filters.get("rule_id") else []
                )
                requested_fields = self._fields_every_rule_has(list(chosen_rules))
            if family is not None and isinstance(requested_fields, list):
                requested_fields = list(
                    dict.fromkeys(
                        list(requested_fields) + list(family.mandatory_fields)
                    )
                )
        # 고른 모드가 줄 수 없는 필드는 조회문을 만들 자리가 없어 생성 단계에서 반드시
        # 실패한다(과목 조회에 Rule 전용 description_ko 가 섞이는 경우). family 가 선언된
        # 확장 모드에서는 이미 걸러지지만 기본 네 모드에는 그 단계가 없었다. 어느 모드가
        # 어떤 필드를 줄 수 있는지는 온톨로지 선언이 정하므로 여기서 그대로 따른다.
        mode_fields = allowed_fields_for_mode(selection_mode)
        if mode_fields is not None and isinstance(requested_fields, list):
            requested_fields = [
                name for name in requested_fields if name in mode_fields
            ]
        # 되묻기 payload 는 모델이 계획을 접은 껍데기라 요청 필드가 없거나 앞 시도의
        # 잔재뿐이다(위에서 모두 걸러진다). 사용자가 규칙을 골라 조회가 정해졌는데도 그
        # 껍데기 때문에 계약이 깨지면, 다 고른 뒤에도 답을 받지 못한다. 무엇을 요청할지는
        # 고른 규칙이 실제로 가진 필드에서 나오므로 값을 지어내지 않는다.
        if (
            selection_mode in RULE_SELECTION_MODES
            and not requested_fields
            and self._resolved
        ):
            chosen_rules = filters.get("rule_ids") or (
                [filters["rule_id"]] if filters.get("rule_id") else []
            )
            if chosen_rules:
                requested_fields = self._fields_every_rule_has(list(chosen_rules))
        allowed = allowed_filters_for_mode(selection_mode)
        filters.update(
            {
                name: value
                for name, value in self._resolved.items()
                if name != "selection_mode" and name in allowed
            }
        )
        filters = {name: value for name, value in filters.items() if name in allowed}
        if selection_mode in {
            SelectionMode.SINGLE_COURSE.value,
            SelectionMode.COURSE_LIST.value,
        } and not {"academic_year", "department_id"}.issubset(filters):
            raise QueryPlanError(
                f"{selection_mode} requires academic_year and department_id"
            )
        # 과목 식별자 검사는 질문값 채택 뒤에 한다. 질문에 과목명이 그대로 있으면
        # 모델이 비워 두었어도 사용자가 말한 조건이므로 그때 채워진다.
        if selection_mode == SelectionMode.SINGLE_COURSE.value and not (
            {"name_ko", "course_code"}.intersection(filters)
        ):
            raise QueryPlanError(
                "SINGLE_COURSE requires the course name_ko or course_code from the question"
            )
        return {
            "question": question,
            "intent": payload.get("intent"),
            "filters": filters,
            "requested_fields": requested_fields,
            # 근거 요구는 계획 모델이 정할 값이 아니다. 이 저장소는 근거 없는 답을
            # 하지 않으므로 언제나 참이며, 계약도 참만 통과시킨다. 모델이 비워 두면
            # 계약 위반으로 계획 전체가 버려지는데, 그건 모델 실수의 대가를 사용자가
            # 치르는 것이다. 되묻기 응답에서 이 값이 자주 비어 있어 특히 그렇다.
            "evidence_required": True,
            "selection_mode": selection_mode,
        }

    def _named_courses(self, question: str) -> list[str]:
        """Every loaded course identity named exactly or by one safe spelling edit."""

        return [item.name_ko for item in self.course_resolver.find_mentions(question)]

    def _names_a_course(self, question: str) -> bool:
        """Say whether the question states a loaded course name verbatim."""

        return bool(self._named_courses(question))

    def _ignores_named_course(self, question: str, plan_payload: Mapping[str, Any]) -> bool:
        """Catch a plan that would answer about something else than the named course.

        질문이 적재된 과목을 이름으로 지목했는데 계획에 그 과목을 가리키는 필터가 하나도
        없으면, 조회 결과는 근거가 붙더라도 묻지 않은 것을 답하게 된다. 커버리지를
        넓히는 경로가 다른 질문에 답하는 통로가 되지 않도록 여기서 막는다.

        **필터가 있다는 것만으로는 부족하다.** 계획 모델이 적재에 없는 학수번호를 지어
        넣으면 이 검사를 통과하고도 조회 결과가 비거나 다른 과목을 가리킨다. 지목한
        과목이 실제로 그 값을 갖는지까지 적재 데이터와 대조한다.
        """

        named = self._named_courses(question)
        if not named:
            return False
        filters = plan_payload.get("filters") or {}
        present = [
            name
            for name in (*COURSE_IDENTIFYING_FILTERS, "course_codes")
            if name in filters
        ]
        if not present:
            return True
        codes = self._course_codes_for(named)
        for name in present:
            value = filters[name]
            if name == "course_code":
                # 적재된 학수번호를 모르면 판단하지 않고 통과시킨다. 결과 검증이 뒤에서
                # 다시 거른다.
                if codes and value not in codes:
                    return True
            elif name == "course_codes":
                if not isinstance(value, list) or codes - set(value):
                    return True
            elif isinstance(value, str) and value not in named:
                return True
        return False

    def _aspect_clarification(
        self, question: str, plan_payload: Mapping[str, Any]
    ) -> tuple[Choice, ...] | None:
        """Choices for which attribute of one course to answer, or ``None`` to answer.

        되묻지 않는 경우가 셋이다. 한 과목 조회가 아닐 때, 질문이 이미 무엇을 묻는지
        말했을 때(`몇 학년 몇 학기에 개설되나?`), 사용자가 이미 골랐을 때다.
        """

        if self.choices is None:
            return None
        if plan_payload.get("selection_mode") != SelectionMode.SINGLE_COURSE.value:
            return None
        if REQUESTED_FIELDS in self._resolved:
            return None
        # 질문 자체에서 요청 필드로 판별해 필터에서 분리한 항목도 이미 확정된
        # 조회 대상이다. 일부 항목(예: Course 노드의 식별 속성)은 CourseOffering
        # 속성 설명으로 만든 aspect label 집합에 없으므로, 그 집합만 보면 불필요한
        # 되묻기가 생긴다. 값은 만들지 않고 위의 일반 필드 패턴과 같은 판정만 공유한다.
        if self._question_names_course_aspect(question):
            return None
        if self.choices.names_an_aspect(question):
            return None
        offered = self.choices.for_missing(
            question, [MissingScope.COURSE_ASPECT.value], self._resolved
        )
        return offered or None

    def _question_names_course_aspect(self, question: str) -> bool:
        """Whether the question already identifies an output field generically."""

        if any(pattern.search(question) for pattern in _REQUESTED_FIELD_PATTERNS.values()):
            return True
        return self.choices is not None and self.choices.names_an_aspect(question)

    def _named_course_error(self, question: str) -> str:
        """The contract reason fed back to the planner when it lost the named course.

        종전 문구는 "과목 필터가 없다"고만 알려 줬다. 지어낸 학수번호를 넣은 경우에는
        틀린 진단이라 모델이 같은 값을 다시 냈다. 지목한 과목과 적재된 학수번호를 그대로
        되먹여 다음 시도가 스스로 고칠 수 있게 한다. 정답 값이 아니라 조회 대상이다.
        """

        named = self._named_courses(question)
        if not named:
            return "the question names a loaded course but the plan does not filter on it"
        codes = sorted(self._course_codes_for(named))
        wanted = f"name_ko must be one of {named}"
        if codes:
            wanted += f" or course_code one of {codes}"
        return (
            "the plan must filter on the course the question names: "
            f"{wanted}. do not invent a course_code."
        )

    def _course_codes_for(self, names: list[str]) -> set[str]:
        """The loaded course codes belonging to these course names."""

        mapping = self.context.get("course_codes_by_name")
        if not isinstance(mapping, Mapping):
            return set()
        found: set[str] = set()
        for name in names:
            codes = mapping.get(name)
            if isinstance(codes, list):
                found.update(code for code in codes if isinstance(code, str))
        return found

    def _rules_related_to(self, question: str) -> list[str]:
        """Keep the requirement rules whose verified wording overlaps the question.

        질문이 어느 이수요건을 가리키는지 확정하지 못했을 때, 확인된 요건을 전부 보여
        주면 묻지 않은 전공 요건까지 답에 섞인다. 질문의 낱말과 규칙 원문이 겹치는
        정도를 재어 가장 잘 겹치는 것만 남긴다.

        한국어는 낱말에 조사가 붙으므로 앞에서부터 잘라 가며 맞춰 보고, 겹친 길이가
        길수록 구체적인 것으로 본다. 어느 규칙이 정답인지 고르는 것이 아니라 조회
        대상을 추리는 것이며, 결과는 그대로 Evidence 검증을 거친다. 겹치는 것이 하나도
        없으면 빈 목록을 돌려주어 호출자가 전부 보여 주도록 둔다.
        """

        verified = set(self._verified_rule_ids())
        if not verified:
            return []
        candidates = self.fact_index.search(
            question, limit=RULE_CONTEXT_LIMIT, labels={"Rule"}
        )
        if not candidates:
            return []
        # 최상위 점수에 가까운 것만 남긴다. 꼬리까지 실으면 종전처럼 묻지 않은
        # 요건까지 답에 섞인다. 자르는 기준은 되묻기 선택지와 공유한다. 종전에는 이
        # 자르기가 여기에만 있어, 같은 결함이 선택지 화면에 그대로 남아 있었다.
        selected = [
            candidate.identifiers["rule_id"]
            for candidate in leading_candidates(candidates)
            if "rule_id" in candidate.identifiers
        ]
        return sorted({rule_id for rule_id in selected if rule_id in verified})

    @staticmethod
    def _fields_the_family_owns(family: Any, requested_fields: list[Any]) -> list[Any]:
        """Drop requested fields the chosen family cannot return.

        모드가 정해지면 어떤 필드를 돌려줄 수 있는지는 family 선언이 이미 정해 두었다.
        그런데 작은 모델은 모드를 맞게 고르고도 다른 family 의 필드를 함께 적는다
        (연계표를 물었는데 ``rule_type``·``profile_order`` 를 요청하는 식). 종전에는
        이때 계획 전체가 거부돼 답할 수 있는 질문이 거절로 끝났다.

        필터에 이미 같은 처리를 하고 있다. 그 모드에서 쓸 수 없는 필터는 떨어뜨리고
        남은 조건으로 조회한다. 필드도 같게 다룬다. 없는 필드를 지어내는 것이 아니라
        돌려줄 수 없는 요청을 걷어내는 것이며, 남은 필드로도 답이 되도록 family 가
        선언한 필수 필드를 이어서 채운다.

        요청 필드가 하나도 이 family 소유가 아니면 손대지 않는다. 그건 모드가 잘못
        정해졌다는 뜻이고, 그 계획은 거부돼 다음 시도로 넘어가는 편이 맞다.
        """

        owned = set(family.field_owners)
        kept = [name for name in requested_fields if name in owned]
        return kept if kept else requested_fields

    def _adopt_family_discriminators(
        self, question: str, family: Any, filters: dict[str, Any]
    ) -> dict[str, Any]:
        """Fill the filter that decides which shape of the family the question means.

        한 fact label 이 종류마다 다른 속성을 채우는 경우가 있다. 집계가 그렇다.
        최소전공학점제 행은 boolean 만, 전공능력별 집계 행은 과목 수와 학점만 채워져
        있다. 종류를 고정하지 않고 조회하면 한 결과 안에 빈 칸이 섞이고, 원문의 빈
        값을 0 으로 바꾸지 않는 계약 때문에 결과 검증이 조회 전체를 막는다.

        어떤 종류인지는 질문 표기와 적재된 사실을 대조해 얻는다. 이미 그 사실을 찾아 둔
        검색 결과에서 값을 그대로 옮길 뿐이며, 없는 값을 지어내지 않는다. 모델이 이미
        값을 넣었으면 손대지 않는다.
        """

        missing = [
            name
            for name in family.required_filters
            if name in FAMILY_DISCRIMINATORS and name not in filters
        ]
        if not missing:
            return filters
        candidates = self.fact_index.search(
            question, limit=MODE_CANDIDATE_LIMIT, labels={family.fact_label}
        )
        if not candidates:
            return filters
        adopted = dict(filters)
        for name in missing:
            value = candidates[0].identifiers.get(name)
            if value is not None:
                adopted[name] = value
        return adopted

    def _fields_every_fact_has(
        self, family: Any, filters: Mapping[str, Any], requested_fields: Any
    ) -> Any:
        """Request only the fields the selected facts actually carry.

        Rule 에 이미 같은 처리를 하고 있다(``_fields_every_rule_has``). 원문의 빈 값을
        0 으로 바꾸지 않는 것이 이 저장소의 계약이라, 한 건만 비어 있어도 결과 검증이
        조회 전체를 막기 때문이다. 확장 family 에도 같은 이유가 그대로 적용된다.

        필수 필드는 남긴다. 그 필드가 비어 있으면 애초에 답을 만들 수 없고, 그때는
        조회 결과가 없다고 답하는 편이 맞다.
        """

        if not isinstance(requested_fields, list) or not requested_fields:
            return requested_fields
        discriminators = {
            name: value
            for name, value in filters.items()
            if name in FAMILY_DISCRIMINATORS
        }
        present = self.fact_index.fields_always_present(
            family.fact_label, discriminators
        )
        if present is None:
            return requested_fields
        kept = [
            name
            for name in requested_fields
            if name in present or name in family.mandatory_fields
        ]
        if not kept:
            return requested_fields
        # 걸러 내고 나니 보여 줄 값이 필수 필드밖에 남지 않는 경우가 있다. 집계처럼
        # 종류마다 채워진 수치가 다를 때 그렇다. 그때는 이 종류가 실제로 갖고 있는
        # 값 필드를 채워 넣는다. 없는 값을 만드는 것이 아니라 있는 값을 마저 묻는 것이다.
        if set(kept) <= set(family.mandatory_fields):
            kept = list(
                dict.fromkeys(
                    kept + [name for name in sorted(family.field_owners) if name in present]
                )
            )
        return kept

    def _accepted_resolved(
        self, question: str, resolved: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        """Keep only the values that were actually offered as choices.

        요청은 사용자가 조작해서 보낼 수 있다. 서버가 대화 상태를 들지 않으므로, 같은
        질문으로 선택지를 다시 만들어 그 안에 있는 값만 받아들인다. 선택지 생성이
        결정론적이라 무상태로도 검증된다.
        """

        if not isinstance(resolved, Mapping) or not resolved or self.choices is None:
            return {}
        # 앞선 턴에서 고른 값을 함께 넘겨 그때의 선택지를 되살린다. 되묻기가 이어지면
        # 후보가 앞 선택으로 좁혀지므로, 좁혀진 상태를 재현해야 대조가 맞는다.
        return {
            name: value
            for name, value in resolved.items()
            if self.choices.is_offered(question, str(name), value, resolved)
        }

    def _options_for(
        self, question: str, missing: tuple[MissingScope, ...], status: Any
    ) -> tuple[Choice, ...]:
        """Choices to offer with a clarification, or nothing when we cannot offer any.

        되묻기가 아닌 상태(범위 밖 등)에는 선택지를 붙이지 않는다. 고를 것을 주는 것이
        답이 아니라 데이터에 없다는 것이 답이기 때문이다.
        """

        if self.choices is None or status is not PlanningStatus.CLARIFICATION_REQUIRED:
            return ()
        if len(self._resolved) >= MAX_CLARIFICATION_ROUNDS:
            return ()
        # 부족 코드를 모르면 빈 목록을 넘긴다. 선택지 생성기가 질문이 무엇을
        # 가리키는지 보고 되물을 종류를 고르며, 마지막에는 "무엇을 알고 싶은지"로
        # 되돌아가므로 언제나 고를 것이 하나는 나온다.
        codes = [code.value for code in missing]
        return self.choices.for_missing(question, codes, self._resolved)

    def _modes_from_question(self, question: str) -> tuple[SelectionMode, ...]:
        """Derive which selection modes the question's wording actually points at.

        모델에게 열여덟 중 하나를 고르라고 시키는 대신, 질문과 적재된 사실의 표기를
        대조해 후보 라벨을 찾고 그 라벨이 속한 모드를 역산한다. family 를 더 얹어도
        선택지가 늘지 않으므로, 커버리지를 넓힐수록 나빠지던 구조가 뒤집힌다.

        여기서 고른 모드는 답이 아니라 조회 대상이며, 결과는 종전과 똑같이 Evidence
        검증을 거친다.
        """

        return self.fact_index.leading_modes(question, limit=MODE_CANDIDATE_LIMIT)

    def _fields_every_rule_has(self, rule_ids: list[str]) -> list[str]:
        """Request only the fields that every selected rule actually carries.

        수치가 아닌 요건은 value·unit·operator 가 비어 있다. 원문의 빈 값을 0 으로
        바꾸지 않는 것이 이 저장소의 계약이라 한 건만 비어도 결과 검증이 조회 전체를
        막는다. 어떤 필드를 공통으로 갖는지는 적재 데이터에서 계산한다.
        """

        presence = self.context.get("rule_field_presence")
        common: set[str] | None = None
        if isinstance(presence, Mapping):
            for rule_id in rule_ids:
                have = set(presence.get(rule_id, ()))
                common = have if common is None else (common & have)
        if not common:
            common = {"rule_type", "description_ko"}
        # 검증된 원문 설명은 수치를 문장 안에 그대로 담고 있어 근거를 잃지 않는다.
        return [name for name in RULE_FIELDS if name in common]

    def _deterministic_course_plan(self, question: str) -> QueryPlan | None:
        """Build a plan when loaded course identities and requested fields are explicit.

        This is semantic slot extraction, not an answer table: identities come from the
        Verified bundle and field hints apply to every course question alike.  The plan
        still traverses the normal Cypher validation, EXPLAIN and result grounding path.
        """

        mentions = self.course_resolver.find_mentions(question)
        if not mentions:
            return None
        requested = [
            field for field, pattern in _COURSE_FIELD_HINTS.items() if pattern.search(question)
        ]
        if _COURSE_IDENTITY_COMPARISON.search(question):
            requested = list(dict.fromkeys([*requested, "name_ko", "course_code"]))
        if (
            not requested
            and len(mentions) < 2
            and (
                not _COURSE_LIST_HINT.search(question)
                or re.search(r"(?:추천|진로|되고\s*싶)", question)
            )
        ):
            return None
        if len(mentions) > 1 or _COURSE_LIST_HINT.search(question):
            # Preserve the fields the user explicitly requested.  A multi-course
            # question asking for course codes must not silently become a generic
            # offering list.  Structural fields needed by the Claim are added by
            # normalisation and do not replace the requested values.
            requested = list(
                dict.fromkeys(
                    [*(requested or list(COURSE_DETAIL_FIELDS)), "name_ko", "completion_type"]
                )
            )
            default = self.context.get("default_scope") or {}
            filters: dict[str, Any] = {
                "academic_year": default.get("academic_year"),
                "course_codes": sorted({item.course_code for item in mentions}),
            }
            if not all(set(item.scope_types) == {"COMMON"} for item in mentions):
                filters["department_id"] = default.get("department_id")
            mode = SelectionMode.COURSE_LIST
        else:
            item = mentions[0]
            default = self.context.get("default_scope") or {}
            exact_name = item.name_ko in question
            filters = {
                "academic_year": default.get("academic_year"),
                ("name_ko" if exact_name else "course_code"): (
                    item.name_ko if exact_name else item.course_code
                ),
            }
            if set(item.scope_types) != {"COMMON"}:
                filters["department_id"] = default.get("department_id")
            mode = SelectionMode.SINGLE_COURSE
        return QueryPlan.from_dict(
            {
                "question": question,
                "intent": "COURSE_FACT_LOOKUP",
                "filters": filters,
                "requested_fields": list(dict.fromkeys(requested)),
                "evidence_required": True,
                "selection_mode": mode.value,
            },
            self.catalog,
        )

    def _verified_rule_rows(self) -> list[tuple[str, str]]:
        match_text = self.context.get("rule_match_text")
        if not isinstance(match_text, Mapping):
            return []
        verified = set(self._verified_rule_ids())
        return sorted(
            (rule_id, str(text))
            for rule_id, text in match_text.items()
            if rule_id in verified and isinstance(rule_id, str)
        )

    def _deterministic_rule_plan(self, question: str) -> QueryPlan | None:
        """Select a small verified rule family from general curriculum semantics."""

        if not _GENERAL_RULE_HINT.search(question):
            return None
        rows = self._verified_rule_rows()
        if not rows:
            return None
        selected: list[str] = []

        def take(*required: str, any_of: tuple[str, ...] = ()) -> None:
            for rule_id, text in rows:
                if all(term in text for term in required) and (
                    not any_of or any(term in text for term in any_of)
                ):
                    selected.append(rule_id)

        named_subject_rules: list[str] = []
        if re.search(r"(?:대체|반드시|필수|가능)", question):
            for rule_id, text in rows:
                # Long subject terms come from the verified rule text.  This lets a
                # mandatory named item be resolved without an application-owned
                # course/rule allowlist or answer value.
                subject_terms = {
                    term.rstrip("의은는이가을를")
                    for term in _WORD.findall(text)
                    if len(term.rstrip("의은는이가을를")) >= 6
                }
                if any(term in question for term in subject_terms):
                    named_subject_rules.append(rule_id)

        if re.search(
            r"(?:TOEIC|토익|TOEFL|토플|TEPS|텝스|OPIc|오픽|G-?TELP|FLEX)",
            question,
            re.IGNORECASE,
        ):
            # 시험 명칭은 atomic Verified Rule 설명에서 읽는다. 질문에 실제로 나온
            # 시험만 선택하며 점수값은 조회 결과가 제공한다.
            aliases = _SUBJECT_FIELD_QUESTION_ALIASES
            named_families = [name for name, pattern in aliases.items() if pattern.search(question)]
            for rule_id, text in rows:
                if any(
                    _SUBJECT_FIELD_RULE_LABELS[family].casefold() in text.casefold()
                    for family in named_families
                ):
                    selected.append(rule_id)
            if re.search(r"(?:학점|교양|인정|줄어|대신)", question):
                take("대학영어 이수 면제", "학점 인정")
            if re.search(r"(?:중\s*하나|하나만|둘\s*다|모두)", question):
                take("영어 공인시험 중 하나", "면제")
        elif named_subject_rules:
            selected.extend(named_subject_rules)
        elif "교양" in question and re.search(r"(?:초과|삭제)", question):
            take("교양학점", any_of=("초과", "최대"))
            selected = [
                rule_id
                for rule_id in selected
                if "예술대학" not in dict(rows)[rule_id]
            ]
        elif "교양" in question and "전공" in question and re.search(
            r"(?:영역별|나머지|잔여|졸업|부족|모자라|얼마나\s*남)", question
        ):
            take("일반 적용 대상", "교양", "최소")
            take("단일전공", "전공 합계")
            take("단일전공", "졸업학점 기준")
            take("단일전공", "졸업 잔여 기준")
        elif "균형교양" in question and re.search(r"(?:총학점|남은)", question):
            take("균형교양")
            take("일반 적용 대상", "교양", "최소")
        elif "균형교양" in question:
            take("균형교양")
        elif "편입" in question and "교양" in question:
            take("편입생", "교양")
        elif "대학영어" in question and "면제" in question:
            take("대학영어 이수 면제", "학점 인정")
            take("영어 공인시험 중 하나", "면제")
        elif "교양" in question and re.search(r"(?:최소|총학점|몇\s*학점)", question):
            take("일반 적용 대상", "교양", "최소")
        elif "전공필수" in question and "학점" in question:
            take("단일전공", "전공필수 기준")
        elif "전공" in question and "졸업잔여" in question:
            take("단일전공", "전공 합계")
            take("단일전공", "졸업 잔여 기준")
        elif "전공" in question and re.search(
            r"(?:전공\s*(?:합계|기준)|최소\s*전공|부족|모자라|얼마나\s*남)",
            question,
        ):
            take("단일전공", "전공 합계")
        elif ("졸업" in question or "총" in question) and re.search(
            r"(?:총\s*\d+(?:\.\d+)?\s*학점|몇\s*학점|절반)", question
        ):
            take("단일전공", "졸업학점 기준")
        elif "잔여" in question and "학점" in question:
            take("단일전공", "졸업 잔여 기준")

        selected = sorted(set(selected))
        if not selected:
            return None
        fields = self._fields_every_rule_has(selected)
        mode = SelectionMode.SINGLE_RULE if len(selected) == 1 else SelectionMode.MULTIPLE_RULES
        return QueryPlan.from_dict(
            {
                "question": question,
                "intent": "VERIFIED_RULE_LOOKUP",
                "filters": {
                    "academic_year": (self.context.get("default_scope") or {}).get(
                        "academic_year"
                    ),
                    "rule_ids": selected,
                },
                "requested_fields": fields,
                "evidence_required": True,
                "selection_mode": mode.value,
            },
            self.catalog,
        )

    def _deterministic_plan(self, question: str) -> QueryPlan | None:
        if re.search(r"(?:권장\s*과목|과목\s*추천)", question):
            default = self.context.get("default_scope") or {}
            return QueryPlan.from_dict(
                {
                    "question": question,
                    "intent": "DEPARTMENT_COURSE_RECOMMENDATIONS",
                    "filters": {
                        "academic_year": default.get("academic_year"),
                        "department_id": default.get("department_id"),
                    },
                    "requested_fields": [
                        "course_name_ko",
                        "course_code",
                        "recommended_grade_year",
                        "recommended_semester",
                        "credits",
                    ],
                    "evidence_required": True,
                    "selection_mode": SelectionMode.COURSE_RECOMMENDATION_LIST.value,
                },
                self.catalog,
            )
        if (
            re.search(r"(?:전공\s*필수|필수\s*전공)", question)
            and _COURSE_LIST_HINT.search(question)
            and not self.course_resolver.find_mentions(question)
        ):
            default = self.context.get("default_scope") or {}
            grade_match = re.search(r"(?<!\d)([1-6])\s*학년", question)
            semester_match = re.search(r"(?<!\d)([12])\s*학기", question)
            filters = {
                "academic_year": default.get("academic_year"),
                "department_id": default.get("department_id"),
                "completion_type": "MAJOR_REQUIRED",
            }
            if grade_match:
                filters["grade_year"] = int(grade_match.group(1))
            if semester_match:
                filters["semester"] = (
                    "FIRST" if semester_match.group(1) == "1" else "SECOND"
                )
            return QueryPlan.from_dict(
                {
                    "question": question,
                    "intent": "COURSE_LIST_BY_COMPLETION_TYPE",
                    "filters": filters,
                    "requested_fields": list(COURSE_DETAIL_FIELDS),
                    "evidence_required": True,
                    "selection_mode": SelectionMode.COURSE_LIST.value,
                },
                self.catalog,
            )
        # 과목명이 있고 질문이 그 과목의 학점·이수구분·개설 시기를 묻는다면
        # 졸업/전공이라는 주변 단어보다 실제 Course identity를 우선한다. 영어
        # 면제처럼 과목명이 함께 나와도 규칙 자체를 묻는 경우는 위 rule router가
        # 먼저 처리한다.
        if re.search(
            r"(?:TOEIC|토익|TOEFL|토플|TEPS|텝스|OPIc|오픽|G-?TELP|FLEX|대학영어.{0,12}면제)",
            question,
            re.IGNORECASE,
        ):
            rule = self._deterministic_rule_plan(question)
            if rule is not None:
                return rule
        course = self._deterministic_course_plan(question)
        if course is not None:
            return course
        rule = self._deterministic_rule_plan(question)
        if rule is not None:
            return rule
        return None

    def plan(
        self, question: str, *, resolved: Mapping[str, Any] | None = None
    ) -> PlanningOutcome:
        """Plan one question, optionally with values the user already picked.

        ``resolved`` 는 앞선 되묻기에서 사용자가 **선택지에서 고른** 값이다. 서버가 상태를
        들지 않으므로 여기서 같은 질문의 선택지를 다시 만들어 그 안의 값인지 대조하고,
        통과한 값만 계획에 잠가 넣는다. 계획 모델이 다른 값을 내도 사용자가 고른 값이
        이긴다.

        **고를 것이 하나뿐이면 묻지 않고 그 값으로 계획을 다시 세운다.** 후보가 하나인
        것을 되묻는 것은 아는 것을 되묻는 셈이고, 사용자에게 의미 없는 클릭을 시킨다.
        적재 후보가 하나뿐인 학년도·학과를 되묻지 않는 것과 같은 원칙이다.
        """

        if not isinstance(question, str) or not question.strip():
            raise QueryPlanError("question must be a non-empty string")
        question = question.strip()
        self._resolved = self._reconciled(self._accepted_resolved(question, resolved))
        question_classification = classify_graduation_question(question)
        if question_classification is GraduationQuestionClass.FULL_PERSONAL_HISTORY:
            return PlanningOutcome(
                status=PlanningStatus.UNSUPPORTED,
                unsupported_reason=UnsupportedReason.PERSONAL_HISTORY,
            )
        direct = self._deterministic_plan(question)
        if direct is not None and not self._resolved:
            return PlanningOutcome(status=PlanningStatus.READY, plan=direct)
        if _matches_review_required_rule(question, self.context):
            return PlanningOutcome(status=PlanningStatus.UNRESOLVED)
        if self._points_at_nothing(question):
            return PlanningOutcome(status=PlanningStatus.OUT_OF_SCOPE)
        outcome = self._plan_once(question, question_classification)
        for _ in range(MAX_AUTO_ADOPTED_CHOICES):
            if outcome.status is not PlanningStatus.CLARIFICATION_REQUIRED:
                return outcome
            if len(outcome.options) != 1:
                return outcome
            only = outcome.options[0]
            if only.filter_name in self._resolved:
                return outcome
            if not self._safe_to_adopt(question, only):
                return outcome
            self._resolved[only.filter_name] = only.value
            outcome = self._plan_once(question, question_classification)
        return outcome

    def _points_at_nothing(self, question: str) -> bool:
        """Whether the question overlaps no loaded fact at all.

        `ㅇ런아러`, `asdfasdf` 처럼 뜻이 없는 입력은 적재된 어느 사실과도 표기가 겹치지
        않는다. 종전에는 이런 입력도 계획 모델을 거쳐 되묻기로 넘어갔고, 되묻기가 전체
        메뉴를 내주는 바람에 하나만 고르면 근거가 붙은 `검증된 답변` 까지 갔다. 답에
        쓰인 사실은 모두 진짜였지만 **아무도 그것을 묻지 않았다.**

        말이 되는 범위 밖 질문(`오늘 점심 뭐 먹지?`)은 계획 모델이 이미 걸러 낸다. 여기서
        막는 것은 계획 모델이 판단할 거리조차 없는 입력이다. 실측에서 정상 질문 32개 중
        31개가 이 검사를 통과했고, 걸린 하나(`시간표 알려줘`)는 적재 데이터에 그 개념이
        없어 거절이 맞는 경우였다.

        되묻기로 값을 고른 뒤에는 검사하지 않는다. 그 값 자체가 적재 데이터에서 나왔고
        이미 서버가 대조해 통과시킨 것이라, 질문 문장이 짧아도 가리키는 사실이 있다.
        """

        if self._resolved:
            return False
        return not self.fact_index.search(question, limit=1)

    def _settled_mode(self) -> str | None:
        """The query kind the user's own picks already determine, or ``None``."""

        chosen = self._resolved.get("selection_mode")
        if isinstance(chosen, str):
            return chosen
        for name in self._resolved:
            implied = FILTER_IMPLIED_MODE.get(name)
            if implied is not None:
                return implied.value
        return None

    @staticmethod
    def _reconciled(resolved: dict[str, Any]) -> dict[str, Any]:
        """Let a later, narrower pick refine the query kind the user picked first.

        되묻기가 이어지면 앞에서 고른 조회 종류를 뒤에서 고른 값이 좁힌다. "학사규칙"을
        고른 뒤 규칙을 **하나** 고르면 그것은 이미 단일 규칙 조회다. 그런데 앞서 고른
        MULTIPLE_RULES 를 그대로 들고 있으면 계획 계약이 "규칙 두 개 이상"을 요구해
        깨지고, 사용자는 다 골랐는데도 답을 받지 못한다.

        조회 종류를 추측해서 바꾸는 것이 아니라, 고른 개수가 이미 정해 놓은 종류로
        맞추는 것뿐이다. 좁혀진 계획도 종전과 똑같이 계약 검증과 근거 검증을 거친다.
        """

        chosen = resolved.get("selection_mode")
        rule_ids = resolved.get("rule_ids")
        if not isinstance(rule_ids, list) or not rule_ids:
            return resolved
        narrowed = (
            SelectionMode.SINGLE_RULE.value
            if len(rule_ids) == 1
            else SelectionMode.MULTIPLE_RULES.value
        )
        # 조회 종류를 따로 고르지 않았어도 개수가 이미 그것을 정한다. 한 선택지가 여러
        # 규칙을 함께 담을 수 있게 되면서(같은 라벨로 묶임) `rule_ids` 만 확정된 채
        # 두 건이 오는 경우가 생겼다. 이때 `FILTER_IMPLIED_MODE` 는 SINGLE_RULE 을
        # 가리키므로 그대로 두면 "규칙 하나" 계약에 걸려 계획이 깨진다.
        if (chosen is None or chosen in RULE_SELECTION_MODES) and chosen != narrowed:
            return {**resolved, "selection_mode": narrowed}
        return resolved

    def _safe_to_adopt(self, question: str, choice: Choice) -> bool:
        """Whether picking this single choice for the user cannot answer the wrong thing.

        고를 것이 하나뿐이어도 그것이 질문과 어긋나면 자동으로 채택하면 안 된다.

        **조회 종류는 대신 골라 주지 않는다.** 후보가 하나로 좁혀졌다는 것은 그 종류로만
        답할 수 있다는 뜻이 아니라 검색이 약하게 걸렸다는 뜻일 수 있다. "무엇을 묻는가"는
        사용자가 정할 몫이다. 개체(과목·이수요건)가 하나뿐인 경우만 자동으로 채택한다.

        질문이 적재된 과목을 이름으로 지목했는데 남은 후보가 이수요건이라면, 채택하는
        순간 묻지 않은 것을 답하게 되므로 그때도 사용자에게 보여 주고 고르게 한다.
        """

        if choice.filter_name in {"selection_mode", REQUESTED_FIELDS}:
            return False
        if not self._names_a_course(question):
            return True
        return choice.filter_name in COURSE_IDENTIFYING_FILTERS

    def _plan_once(
        self,
        question: str,
        question_classification: GraduationQuestionClass,
    ) -> PlanningOutcome:
        """Run one planning pass with the values settled so far."""

        attempts: list[PlanningAttempt] = []
        previous_error: str | None = None
        clarification_retried = False
        payload: Mapping[str, Any] = {}
        for index in range(MAX_PLANNING_ATTEMPTS):
            generation = self.client.generate_json(
                system_prompt=PLANNER_SYSTEM_PROMPT,
                user_prompt=planner_prompt(
                    question,
                    self._context_for(question),
                    question_classification=question_classification.value,
                    previous_error=previous_error,
                    settled_choices=self._resolved,
                ),
                response_schema=planner_response_schema(self.catalog),
            )
            payload = generation.payload if isinstance(generation.payload, Mapping) else {}
            last = index == MAX_PLANNING_ATTEMPTS - 1
            try:
                status = PlanningStatus(payload.get("status"))
                message = payload.get("message")
                if message is not None and not isinstance(message, str):
                    raise LLMResponseError(
                        "LLM_PLAN_MESSAGE_INVALID", "planner message must be text"
                    )
                payload = dict(payload)
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
                if isinstance(filters, Mapping):
                    filters = {
                        key: value for key, value in dict(filters).items() if value is not None
                    }
                payload["filters"] = filters
                payload["requested_fields"] = requested_fields
                if self._outside_supported_scope(filters):
                    return PlanningOutcome(status=PlanningStatus.OUT_OF_SCOPE)
                if status is PlanningStatus.CLARIFICATION_REQUIRED:
                    # 모델의 자유문구가 아니라 QueryPlan 계약과 실제 결과의 stable
                    # identity 검증으로 모호성을 판정한다. 이미 완전한 계획이면 DB
                    # 조회 뒤 후보 수가 0/1/복수인지 판정할 수 있으므로 영문 되묻기를
                    # 사용자에게 내보내거나 같은 계획을 다시 생성하지 않는다.
                    try:
                        settled = self._normalise(question, payload)
                    except (ValueError, QueryPlanError):
                        settled = None
                    missing_now = self._missing_scope(payload)
                    scope_is_settled = not missing_now or (
                        settled is not None
                        and self._scope_settled(missing_now, settled)
                    )
                    if (
                        settled is not None
                        and scope_is_settled
                        and not self._ignores_named_course(question, settled)
                    ):
                        aspect = self._aspect_clarification(question, settled)
                        if aspect is not None:
                            attempts.append(
                                self._record(index, AttemptOutcome.CLARIFICATION, payload)
                            )
                            return PlanningOutcome(
                                status=PlanningStatus.CLARIFICATION_REQUIRED,
                                missing=(MissingScope.COURSE_ASPECT,),
                                attempts=tuple(attempts),
                                options=aspect,
                            )
                        attempts.append(
                            self._record(index, AttemptOutcome.BROADENED, payload)
                        )
                        return PlanningOutcome(
                            status=PlanningStatus.READY,
                            plan=QueryPlan.from_dict(settled, self.catalog),
                            attempts=tuple(attempts),
                        )
                if status is not PlanningStatus.READY:
                    missing = self._missing_scope(payload)
                    # 되물으려는 범위를 적재된 데이터가 이미 하나로 정해 두었고 나머지
                    # 계획 계약도 만족한다면, 그것은 모호한 것이 아니라 확정된 것이다.
                    # 사용자에게 다시 묻지 않고 그 값으로 조회한다.
                    if status is PlanningStatus.CLARIFICATION_REQUIRED and missing:
                        try:
                            settled = self._normalise(question, payload)
                        except (ValueError, QueryPlanError):
                            settled = None
                        if (
                            settled is not None
                            and self._scope_settled(missing, settled)
                            and not self._ignores_named_course(question, settled)
                        ):
                            # 이 경로도 READY 로 나가므로 같은 검사를 거쳐야 한다.
                            # 한쪽에만 두면 계획 모델이 어느 경로로 오느냐에 따라 되묻기가
                            # 붙거나 말거나 해서 동작이 갈린다.
                            aspect = self._aspect_clarification(question, settled)
                            if aspect is not None:
                                attempts.append(
                                    self._record(
                                        index, AttemptOutcome.CLARIFICATION, payload
                                    )
                                )
                                return PlanningOutcome(
                                    status=PlanningStatus.CLARIFICATION_REQUIRED,
                                    missing=(MissingScope.COURSE_ASPECT,),
                                    attempts=tuple(attempts),
                                    options=aspect,
                                )
                            attempts.append(
                                self._record(index, AttemptOutcome.BROADENED, payload)
                            )
                            return PlanningOutcome(
                                status=PlanningStatus.READY,
                                plan=QueryPlan.from_dict(settled, self.catalog),
                                attempts=tuple(attempts),
                            )
                    # 모델의 모호성 판단을 조용히 READY 로 바꾸지 않는다. 되묻지 않아도
                    # 되는 이유가 있으면 한 번만 알려 주고 다시 계획하게 한다.
                    if (
                        status is PlanningStatus.CLARIFICATION_REQUIRED
                        and not last
                        and not clarification_retried
                    ):
                        redundant = self._redundant_clarification(
                            question, payload, missing
                        )
                        if redundant is not None:
                            attempts.append(
                                self._record(index, AttemptOutcome.CLARIFICATION, payload)
                            )
                            previous_error = redundant
                            clarification_retried = True
                            continue
                    attempts.append(
                        self._record(
                            index,
                            AttemptOutcome.CLARIFICATION
                            if status is PlanningStatus.CLARIFICATION_REQUIRED
                            else AttemptOutcome.NOT_ANSWERABLE,
                            payload,
                        )
                    )
                    return PlanningOutcome(
                        status=status,
                        message=self._safe_status_message(
                            status, selection_mode, filters
                        ),
                        missing=missing,
                        attempts=tuple(attempts),
                        options=self._options_for(question, missing, status),
                        unsupported_reason=(
                            UnsupportedReason.SINGLE_CONDITION_COMPARISON
                            if status is PlanningStatus.UNSUPPORTED
                            and question_classification
                            is GraduationQuestionClass.SINGLE_CONDITION_COMPARISON
                            else UnsupportedReason.GENERAL_FEATURE
                            if status is PlanningStatus.UNSUPPORTED
                            else None
                        ),
                    )
                plan_payload = self._normalise(question, payload)
                if self._outside_supported_scope(plan_payload.get("filters")):
                    return PlanningOutcome(status=PlanningStatus.OUT_OF_SCOPE)
                # 질문이 적재된 과목을 이름으로 지목했는데 계획이 그 과목을 가리키지
                # 않으면, 근거가 붙은 다른 사실로 엉뚱한 답을 하게 된다. 종전에는
                # 되묻기를 READY 로 바꿀 때만 이 검사를 했고 계획 모델이 곧바로 READY 를
                # 내면 걸러지지 않았다. 계약 위반으로 되돌려 다시 계획하게 한다.
                if self._ignores_named_course(question, plan_payload):
                    raise QueryPlanError(self._named_course_error(question))
                # 어느 과목인지는 정해졌는데 그 과목의 **무엇을** 묻는지가 비어 있으면,
                # 개설 정보를 통째로 답하는 대신 고르게 한다. 이때 선택지 라벨은 `학점`
                # 이고 답은 `3학점` 이라 서로 달라, 고르는 행위가 질문을 실제로 좁힌다.
                aspect = self._aspect_clarification(question, plan_payload)
                if aspect is not None:
                    attempts.append(
                        self._record(index, AttemptOutcome.CLARIFICATION, payload)
                    )
                    return PlanningOutcome(
                        status=PlanningStatus.CLARIFICATION_REQUIRED,
                        missing=(MissingScope.COURSE_ASPECT,),
                        attempts=tuple(attempts),
                        options=aspect,
                    )
                attempts.append(self._record(index, AttemptOutcome.ACCEPTED, payload))
                return PlanningOutcome(
                    status=status,
                    plan=QueryPlan.from_dict(plan_payload, self.catalog),
                    message=None,
                    attempts=tuple(attempts),
                )
            except (ValueError, QueryPlanError, LLMResponseError) as exc:
                previous_error = str(exc)
                attempts.append(
                    self._record(
                        index,
                        AttemptOutcome.CONTRACT_REJECTED,
                        payload,
                        contract_error=previous_error,
                    )
                )
                if not last:
                    continue
                # 시도를 다 써도 계획이 서지 않았다. 사용자에게는 "처리하지 못했다"가
                # 아니라 **무엇을 고르면 되는지**를 주는 편이 낫다. 종전에는 이 자리를
                # 넓히기가 메웠는데, 넓히기는 묻지 않은 요건까지 답에 섞었다.
                offered = self._options_for(
                    question, (), PlanningStatus.CLARIFICATION_REQUIRED
                )
                if offered:
                    attempts.append(
                        self._record(index, AttemptOutcome.CLARIFICATION, payload)
                    )
                    return PlanningOutcome(
                        status=PlanningStatus.CLARIFICATION_REQUIRED,
                        attempts=tuple(attempts),
                        options=offered,
                    )
                error = LLMResponseError(
                    "LLM_PLAN_CONTRACT_INVALID", "planner failed the QueryPlan contract"
                )
                error.attempts = tuple(attempts)
                raise error from exc
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
