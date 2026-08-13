"""Natural-language question to validated QueryPlan using a local model."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

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
    MissingScope,
    PlanningAttempt,
    PlanningOutcome,
    PlanningStatus,
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
    "rule_match_text",
)
# 질문 낱말이 규칙 설명과 겹치는지 볼 때 인정하는 최소 길이. 조사가 붙은 낱말은
# 앞에서부터 잘라 가며 맞춰 본다.
MIN_MATCH_PREFIX = 2
# 계획 시도 상한. 계약 위반 문구를 되먹여 스스로 고칠 기회를 준다.
MAX_PLANNING_ATTEMPTS = 3
# 어떤 필터가 채워지면 그 부족 코드가 해소되는지. 필터로 메울 수 없는 코드는 넣지
# 않으며, 그런 코드는 되묻기로 남는다.
SCOPE_FILLING_FILTERS: dict[MissingScope, tuple[str, ...]] = {
    MissingScope.ACADEMIC_YEAR: ("academic_year",),
    MissingScope.DEPARTMENT: ("department_id",),
    MissingScope.COURSE_IDENTITY: ("name_ko", "course_code"),
    MissingScope.RULE_TOPIC: ("rule_id", "rule_ids"),
}
# 어떤 과목을 묻는지 계획이 실제로 담고 있는지 확인할 때 보는 필터.
COURSE_IDENTIFYING_FILTERS = ("name_ko", "course_code", "course_name_ko")
_WORD = re.compile(r"[가-힣A-Za-z]{2,}")

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
                "minItems": 1,
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
            rule_ids.append(
                {
                    "rule_id": props["rule_id"],
                    "rule_type": props.get("rule_type"),
                    "semantic_hint_without_values": hint,
                }
            )
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
    return {
        "academic_years": sorted(academic_years),
        "question_matchable_values": question_matchable_values,
        "filterable_values": filterable_values,
        "departments": sorted(departments, key=lambda item: item["department_id"]),
        "verified_rule_identifiers": sorted(rule_ids, key=lambda item: item["rule_id"]),
        "rule_field_presence": rule_field_presence,
        "rule_match_text": rule_match_text,
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
        return adopted

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

    @staticmethod
    def _mode_for_fields(selection_mode: Any, requested_fields: Any) -> Any:
        """Correct the mode when the requested fields belong to exactly one family.

        작은 모델은 물음의 종류를 자주 SINGLE_COURSE 로 몰아 놓고, 정작 요청 필드는
        옳은 fact family 의 것을 고른다. 어느 필드가 어느 family 소유인지는 온톨로지
        선언이 이미 정하고 있으므로, 요청 필드를 전부 담을 수 있는 모드가 하나뿐이면
        그 모드로 고친다.

        후보가 둘 이상이거나 지금 모드로도 충분하면 손대지 않는다. 질문 문자열을 보지
        않으므로 질문별 분기가 아니며, 고친 계획도 같은 계약 검증을 다시 거친다.
        """

        if not isinstance(requested_fields, list) or not requested_fields:
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
        selection_mode = self._mode_for_fields(selection_mode, requested_fields)
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
        # 확장 family 도 같은 이유로 구조적 보강만 한다. 문장을 만들 수 없는 필드
        # 조합이 오면 답변 단계에서 안전 실패로 끝나므로, 질문별 분기 없이 family 가
        # 선언한 최소 필드를 채워 준다.
        family = family_for_mode(selection_mode)
        if family is not None and isinstance(requested_fields, list):
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
        filters = self._complete_scope(filters, requested_fields, family)
        # 고른 모드가 쓸 수 없는 필터는 조회 경로에 붙일 자리가 없어 Cypher 생성에서
        # 반드시 실패한다(예: Rule 질의에 department_id). 그 필터는 그 모드에서
        # 애초에 무의미하므로 떨어뜨리고, 남은 조건으로 조회한다. 조회 범위만 넓어질
        # 뿐 없는 사실을 만들지 않으며, 결과는 그대로 근거 검증을 거친다.
        allowed = allowed_filters_for_mode(selection_mode)
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
            "evidence_required": payload.get("evidence_required"),
            "selection_mode": selection_mode,
        }

    def _names_a_course(self, question: str) -> bool:
        """Say whether the question states a loaded course name verbatim."""

        values = self.context.get("question_matchable_values")
        if not isinstance(values, Mapping):
            return False
        names = values.get("name_ko")
        if not isinstance(names, list):
            return False
        return any(isinstance(name, str) and name and name in question for name in names)

    def _ignores_named_course(self, question: str, plan_payload: Mapping[str, Any]) -> bool:
        """Catch a plan that would answer about something else than the named course.

        질문이 적재된 과목을 이름으로 지목했는데 계획에 그 과목을 가리키는 필터가 하나도
        없으면, 조회 결과는 근거가 붙더라도 묻지 않은 것을 답하게 된다. 커버리지를
        넓히는 경로가 다른 질문에 답하는 통로가 되지 않도록 여기서 막는다.
        """

        if not self._names_a_course(question):
            return False
        filters = plan_payload.get("filters") or {}
        return not any(name in filters for name in COURSE_IDENTIFYING_FILTERS)

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

        texts = self.context.get("rule_match_text")
        if not isinstance(texts, Mapping) or not texts:
            return []
        tokens = set(_WORD.findall(question))
        if not tokens:
            return []
        scores: dict[str, tuple[int, int]] = {}
        for rule_id, text in texts.items():
            if not isinstance(text, str):
                continue
            matched = 0
            length = 0
            for token in tokens:
                for size in range(len(token), MIN_MATCH_PREFIX - 1, -1):
                    if token[:size] in text:
                        matched += 1
                        length += size
                        break
            scores[rule_id] = (matched, length)
        best = max(scores.values(), default=(0, 0))
        # 겹친 낱말 수를 먼저 본다. 길이만 재면 조사가 붙은 낱말이 우연히 길게 겹친
        # 규칙 하나가 이겨, 정작 물어본 요건이 빠진다.
        if best[0] <= 0:
            return []
        return sorted(
            rule_id for rule_id, score in scores.items() if score[0] == best[0]
        )

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

    def _broaden(
        self,
        question: str,
        payload: Mapping[str, Any],
        missing: tuple[MissingScope, ...] = (),
    ) -> tuple[dict[str, Any], str] | None:
        """Answer a wider question rather than refusing, when the mode allows it.

        지금까지는 조회 범위를 좁히지 못하면 곧바로 거절했다. 그런데 근거 계약이
        요구하는 것은 정밀도가 아니라 Evidence다. 어느 이수요건인지 못 고르겠으면
        적재된 VERIFIED 이수요건을 전부 근거와 함께 보여 주는 편이, 아무것도 답하지
        않는 것보다 낫고 계약도 그대로 지킨다.

        넓힌 결과도 뒤의 Cypher 검증·EXPLAIN·결과 검증을 똑같이 통과해야 하며,
        없는 사실을 만들어 내지 않는다. 넓혔다는 사실은 호출자에게 사유 코드로
        돌려주어 화면에 밝힌다.
        """

        # 질문이 적재된 과목을 이름으로 지목했다면 이수요건 질문이 아니다. 그런 질문을
        # 이수요건 전체로 넓히면 묻지 않은 것을 답하게 되므로 넓히지 않는다.
        if self._names_a_course(question):
            return None
        # 이수요건 모드를 골랐거나, 어느 이수요건인지 모르겠다고 밝힌 경우가 대상이다.
        # 후자는 모드를 잘못 골랐어도 이수요건 질문이라는 것만은 스스로 판단한 것이다.
        selection_mode = payload.get("selection_mode")
        if (
            selection_mode
            not in {
                SelectionMode.SINGLE_RULE.value,
                SelectionMode.MULTIPLE_RULES.value,
            }
            and MissingScope.RULE_TOPIC not in missing
        ):
            return None
        # 확인된 이수요건을 전부 쏟아내면 묻지 않은 전공 요건까지 답에 섞인다. 질문과
        # 낱말이 겹치는 요건이 있으면 그것만 남기고, 하나도 없을 때만 전부 보여 준다.
        related = self._rules_related_to(question)
        reason = "RULE_TOPIC_NARROWED"
        if not related:
            related = self._verified_rule_ids()
            reason = "RULE_TOPIC_UNRESOLVED"
        if not related:
            return None
        widened = dict(payload)
        widened["selection_mode"] = (
            SelectionMode.SINGLE_RULE.value
            if len(related) == 1
            else SelectionMode.MULTIPLE_RULES.value
        )
        filters = dict(payload.get("filters") or {})
        filters.pop("rule_id", None)
        filters["rule_ids"] = related
        widened["filters"] = filters
        # 넓힌 조회에는 모든 VERIFIED Rule 이 반드시 갖는 필드만 요청한다. value·unit·
        # operator 는 수치가 아닌 규칙에서 비어 있고, 원문의 빈 값을 0 으로 바꾸지 않는
        # 것이 이 저장소의 계약이므로 한 건이라도 비면 결과 검증이 전체를 막는다.
        # 검증된 원문 설명은 수치를 문장 안에 그대로 담고 있어 근거를 잃지 않는다.
        widened["requested_fields"] = self._fields_every_rule_has(related)
        # 넓힌 조회일수록 근거 요구를 낮추지 않는다. 모델이 이 값을 비워 두면 계획
        # 계약이 거부하므로 여기서 명시한다.
        widened["evidence_required"] = True
        try:
            return self._normalise(question, widened), reason
        except (ValueError, QueryPlanError):
            return None

    def plan(self, question: str) -> PlanningOutcome:
        if not isinstance(question, str) or not question.strip():
            raise QueryPlanError("question must be a non-empty string")
        question = question.strip()
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
                    previous_error=previous_error,
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
                    # 어느 이수요건인지 못 고른 되묻기라면, 되묻는 대신 적재된
                    # 이수요건 전부를 근거와 함께 보여 준다. OUT_OF_SCOPE 처럼
                    # 데이터가 없다는 판단은 그대로 둔다.
                    if status is PlanningStatus.CLARIFICATION_REQUIRED:
                        broadened = self._broaden(question, payload, missing)
                        if broadened is not None:
                            plan_payload, reason = broadened
                            attempts.append(
                                self._record(index, AttemptOutcome.BROADENED, payload)
                            )
                            return PlanningOutcome(
                                status=PlanningStatus.READY,
                                plan=QueryPlan.from_dict(plan_payload, self.catalog),
                                attempts=tuple(attempts),
                                broadened=reason,
                            )
                    return PlanningOutcome(
                        status=status,
                        message=message,
                        missing=missing,
                        attempts=tuple(attempts),
                    )
                plan_payload = self._normalise(question, payload)
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
                broadened = self._broaden(question, payload)
                if broadened is not None:
                    plan_payload, reason = broadened
                    attempts.append(
                        self._record(index, AttemptOutcome.BROADENED, payload)
                    )
                    return PlanningOutcome(
                        status=PlanningStatus.READY,
                        plan=QueryPlan.from_dict(plan_payload, self.catalog),
                        attempts=tuple(attempts),
                        broadened=reason,
                    )
                error = LLMResponseError(
                    "LLM_PLAN_CONTRACT_INVALID", "planner failed the QueryPlan contract"
                )
                error.attempts = tuple(attempts)
                raise error from exc
        raise AssertionError("unreachable planner retry state")
