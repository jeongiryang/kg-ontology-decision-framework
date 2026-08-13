"""Declarative fact families that widen answer coverage without weakening grounding.

기존 답변 경로는 ``CourseOffering``과 ``Rule`` 두 fact family에만 연결돼 있었다. 그래서
Verified KG에 적재돼 있고 Evidence까지 붙어 있는 나머지 사실이 질의로 도달하지 못했다.
이 모듈은 그 사실들을 **선언으로** 등록해 도달 범위를 넓힌다.

확장해도 근거 규칙은 그대로다. 여기에 등록할 수 있는 fact label의 조건은 다음과 같다.

- ``ontology_spec.json``이 선언한 라벨일 것
- ``status`` 속성으로 ``VERIFIED`` 판별이 가능할 것
- ``(fact)-[:SUPPORTED_BY]->(Evidence)`` **직접** 경로를 가질 것

즉 이 모듈은 "무엇을 물어볼 수 있는가"만 넓히고, "무엇을 근거로 답하는가"는 넓히지
않는다. 값은 종전과 동일하게 ``ResultValidator``가 승인한 행에서만 나온다.

기존 두 family는 의도적으로 이 선언에 포함하지 않았다. 이미 검증된 경로를 재작성하면
회귀 위험만 커지므로, 신규 family만 선언형으로 처리한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class SelectionMode(StrEnum):
    """Expected result cardinality and fact family semantics."""

    SINGLE_RULE = "SINGLE_RULE"
    MULTIPLE_RULES = "MULTIPLE_RULES"
    SINGLE_COURSE = "SINGLE_COURSE"
    COURSE_LIST = "COURSE_LIST"
    # 아래는 확장된 fact family다. 각 모드는 fact label 하나에 1:1로 대응하며,
    # 이 대응이 provenance 계약(fact_label 단일성)을 그대로 유지시킨다.
    CREDIT_ALLOCATION_LIST = "CREDIT_ALLOCATION_LIST"
    ROADMAP_LIST = "ROADMAP_LIST"
    EDUCATION_GOAL_LIST = "EDUCATION_GOAL_LIST"
    CAREER_FIELD_LIST = "CAREER_FIELD_LIST"
    TALENT_PROFILE_LIST = "TALENT_PROFILE_LIST"
    COURSE_RECOMMENDATION_LIST = "COURSE_RECOMMENDATION_LIST"


@dataclass(frozen=True, slots=True)
class FilterBinding:
    """Query contract binding, validated against the ontology catalog at runtime."""

    label: str
    property_name: str
    operator: str = "EQUALS"


# 질의 정책 메타데이터이며 스키마 정의의 복제가 아니다. 여기 등장하는 모든
# 라벨/속성은 plan 승인 전에 생성된 온톨로지 카탈로그로 검사된다.
BASE_FILTER_BINDINGS: dict[str, FilterBinding] = {
    "academic_year": FilterBinding("CurriculumVersion", "academic_year"),
    "department_id": FilterBinding("Department", "department_id"),
    "grade_year": FilterBinding("CourseOffering", "grade_year", "PARAMETER_IN_PROPERTY"),
    "semester": FilterBinding("CourseOffering", "semester"),
    "completion_type": FilterBinding("CourseOffering", "completion_type"),
    "credits": FilterBinding("CourseOffering", "credits"),
    "course_code": FilterBinding("Course", "course_code"),
    "name_ko": FilterBinding("Course", "name_ko"),
    "rule_id": FilterBinding("Rule", "rule_id"),
    "rule_ids": FilterBinding("Rule", "rule_id", "PROPERTY_IN_PARAMETER"),
    "area_id": FilterBinding("EducationArea", "area_id"),
    "major_type": FilterBinding("ApplicabilityScope", "major_type"),
    "admission_type": FilterBinding("ApplicabilityScope", "admission_type"),
    # 확장 family 전용 필터
    "credit_category": FilterBinding("CreditAllocation", "credit_category"),
    "source_was_blank": FilterBinding("CreditAllocation", "source_was_blank"),
    "is_total": FilterBinding("CreditAllocation", "is_total"),
    "recommended_grade_year": FilterBinding(
        "CourseRecommendation", "recommended_grade_year"
    ),
    "recommended_semester": FilterBinding(
        "CourseRecommendation", "recommended_semester"
    ),
    "entry_type": FilterBinding("RoadmapEntry", "entry_type"),
    "goal_scope": FilterBinding("EducationGoal", "goal_scope"),
}


@dataclass(frozen=True, slots=True)
class FactFamily:
    """One evidence-backed fact label and the only query shape allowed to reach it."""

    fact_label: str
    fact_id_property: str
    selection_mode: SelectionMode
    # alias 는 생성 Cypher의 변수명이다. 검증기가 라벨을 다시 확인하므로 alias 자체는
    # 신뢰 대상이 아니고, 오직 스캐폴드 가독성을 위한 것이다.
    aliases: Mapping[str, str]
    base_matches: tuple[str, ...]
    conditional_matches: Mapping[str, str]
    field_owners: Mapping[str, str]
    filter_overrides: Mapping[str, FilterBinding]
    required_filters: frozenset[str]
    allowed_filters: frozenset[str]
    # 렌더 가능한 문장을 만들려면 반드시 있어야 하는 필드. planner 가 빠뜨리면
    # 질문별 분기 없이 구조적으로 보강한다.
    mandatory_fields: tuple[str, ...]
    order_fields: tuple[str, ...]
    # 질문에 명시되지 않았을 때 planner 가 채우는 범위 필터. 답을 고르는 값이 아니라
    # 원문 표에서 어떤 행이 답변 대상인지 정하는 구조적 조건이다.
    default_filters: Mapping[str, Any]

    @property
    def fact_alias(self) -> str:
        return self.aliases[self.fact_label]

    @property
    def evidence_alias(self) -> str:
        return self.aliases["Evidence"]


def _department_scoped(
    fact_label: str,
    fact_id_property: str,
    selection_mode: SelectionMode,
    anchor: str,
    *,
    field_owners: Mapping[str, str],
    filter_overrides: Mapping[str, FilterBinding] = {},
    conditional_matches: Mapping[str, str] = {},
    conditional_aliases: Mapping[str, str] = {},
    extra_filters: frozenset[str] = frozenset(),
    mandatory_fields: tuple[str, ...] = (),
    order_fields: tuple[str, ...] = (),
    default_filters: Mapping[str, Any] = {},
) -> FactFamily:
    """Build a family anchored under one department's curriculum version.

    ``anchor``는 fact 노드까지 가는 MATCH 한 줄이다. 모든 확장 family는 학년도와 학과로
    범위가 고정되며, 이 범위 고정이 여러 학과·연도 데이터가 섞이는 것을 막는다.
    """

    aliases = {
        "CurriculumVersion": "cv",
        "Department": "d",
        fact_label: "f",
        "Evidence": "e",
        **conditional_aliases,
    }
    return FactFamily(
        fact_label=fact_label,
        fact_id_property=fact_id_property,
        selection_mode=selection_mode,
        aliases=aliases,
        base_matches=(
            "MATCH (cv:CurriculumVersion)-[:FOR_DEPARTMENT]->(d:Department)",
            anchor,
            "MATCH (f)-[:SUPPORTED_BY]->(e:Evidence)",
        ),
        conditional_matches=dict(conditional_matches),
        field_owners=dict(field_owners),
        filter_overrides=dict(filter_overrides),
        required_filters=frozenset({"academic_year", "department_id"}),
        allowed_filters=frozenset({"academic_year", "department_id"}) | extra_filters,
        mandatory_fields=mandatory_fields,
        order_fields=order_fields,
        default_filters=dict(default_filters),
    )


EXTENDED_FAMILIES: dict[SelectionMode, FactFamily] = {
    # 학년·학기별 교양 학점 배분표. allocated_credits 는 원문이 빈칸이면 null 이므로
    # ResultValidator 의 null 거부 규칙이 그대로 적용된다(0 으로 바꾸지 않는다).
    SelectionMode.CREDIT_ALLOCATION_LIST: _department_scoped(
        "CreditAllocation",
        "allocation_id",
        SelectionMode.CREDIT_ALLOCATION_LIST,
        "MATCH (cv)-[:HAS_CREDIT_ALLOCATION]->(f:CreditAllocation)",
        field_owners={
            "credit_category": "f",
            "grade_year": "f",
            "semester": "f",
            "allocated_credits": "f",
            "is_total": "f",
        },
        filter_overrides={
            "grade_year": FilterBinding("CreditAllocation", "grade_year"),
            "semester": FilterBinding("CreditAllocation", "semester"),
        },
        conditional_matches={
            "area_id": "MATCH (f)-[:ALLOCATES_TO]->(a:EducationArea)",
        },
        conditional_aliases={"EducationArea": "a"},
        extra_filters=frozenset(
            {
                "grade_year",
                "semester",
                "credit_category",
                "area_id",
                "source_was_blank",
                "is_total",
            }
        ),
        # is_total 이 없으면 합계 행이 개별 학기 행과 같은 모양으로 나열돼 원문과
        # 다르게 읽힌다. 답변 정확성에 직결되므로 필수 필드로 둔다.
        mandatory_fields=("credit_category", "allocated_credits", "is_total"),
        order_fields=("grade_year", "semester", "credit_category"),
        # 학점 배분표 117행 중 42행은 원문이 빈칸이라 allocated_credits 가 null 이다.
        # 빈칸을 0 으로 바꾸지 않는 것이 데이터 계약이므로, 값이 적힌 행만 답변
        # 대상으로 삼는다. 빈칸 행을 보려면 질문 계획에서 이 필터를 뒤집어야 한다.
        # 합계 행(is_total=true)은 원문에 학년·학기가 없다. 개별 행과 한 결과에
        # 섞으면 학년 표시가 비어 답변을 만들 수 없으므로 기본은 개별 행만 본다.
        # 합계를 물으면 계획에서 is_total=true 로 뒤집어 따로 조회한다.
        default_filters={"source_was_blank": False, "is_total": False},
    ),
    # 학년·학기 권장 이수 로드맵.
    SelectionMode.ROADMAP_LIST: _department_scoped(
        "RoadmapEntry",
        "roadmap_entry_id",
        SelectionMode.ROADMAP_LIST,
        "MATCH (cv)-[:HAS_ROADMAP_ENTRY]->(f:RoadmapEntry)",
        field_owners={
            "raw_label": "f",
            "entry_type": "f",
            "grade_year": "f",
            "semester": "f",
            "is_required": "f",
            "is_extracurricular": "f",
        },
        filter_overrides={
            "grade_year": FilterBinding("RoadmapEntry", "grade_year"),
            "semester": FilterBinding("RoadmapEntry", "semester"),
        },
        extra_filters=frozenset({"grade_year", "semester", "entry_type"}),
        mandatory_fields=("raw_label", "entry_type"),
        order_fields=("grade_year", "semester", "raw_label"),
    ),
    # 학과 교육목표. 대학(Institution) 소속 목표는 경로가 달라 이 family 범위 밖이다.
    SelectionMode.EDUCATION_GOAL_LIST: _department_scoped(
        "EducationGoal",
        "goal_id",
        SelectionMode.EDUCATION_GOAL_LIST,
        "MATCH (d)-[:HAS_EDUCATION_GOAL]->(f:EducationGoal)",
        field_owners={
            "description_ko": "f",
            "goal_order": "f",
            "goal_scope": "f",
        },
        filter_overrides={
            "goal_scope": FilterBinding("EducationGoal", "goal_scope"),
        },
        extra_filters=frozenset({"goal_scope"}),
        mandatory_fields=("description_ko",),
        order_fields=("goal_order",),
    ),
    # 졸업 후 진출 분야.
    SelectionMode.CAREER_FIELD_LIST: _department_scoped(
        "CareerField",
        "career_field_id",
        SelectionMode.CAREER_FIELD_LIST,
        "MATCH (d)-[:HAS_CAREER_FIELD]->(f:CareerField)",
        field_owners={
            "name_ko": "f",
            "field_order": "f",
        },
        filter_overrides={
            "name_ko": FilterBinding("CareerField", "name_ko"),
        },
        mandatory_fields=("name_ko",),
        order_fields=("field_order",),
    ),
    # 학과 인재상.
    SelectionMode.TALENT_PROFILE_LIST: _department_scoped(
        "TalentProfile",
        "talent_profile_id",
        SelectionMode.TALENT_PROFILE_LIST,
        "MATCH (d)-[:DEFINES_TALENT_PROFILE]->(f:TalentProfile)",
        field_owners={
            "description_ko": "f",
            "profile_order": "f",
        },
        mandatory_fields=("description_ko",),
        order_fields=("profile_order",),
    ),
    # 학과가 권장하는 교양 과목. Course 노드가 아니라 권장 항목 자체가 fact 다.
    SelectionMode.COURSE_RECOMMENDATION_LIST: _department_scoped(
        "CourseRecommendation",
        "recommendation_id",
        SelectionMode.COURSE_RECOMMENDATION_LIST,
        "MATCH (cv)-[:HAS_RECOMMENDATION]->(f:CourseRecommendation)",
        field_owners={
            "course_name_ko": "f",
            "course_code": "f",
            "area_raw": "f",
            "recommended_grade_year": "f",
            "recommended_semester": "f",
            "credits": "f",
        },
        filter_overrides={
            "course_code": FilterBinding("CourseRecommendation", "course_code"),
            "credits": FilterBinding("CourseRecommendation", "credits"),
        },
        extra_filters=frozenset(
            {"course_code", "recommended_grade_year", "recommended_semester"}
        ),
        mandatory_fields=("course_name_ko",),
        order_fields=("recommended_grade_year", "recommended_semester", "course_name_ko"),
    ),
}

# 기존 네 모드가 쓸 수 있는 필터와 필드. 확장 family 는 자기 선언이 범위를 정하지만,
# 기존 모드는 그런 선언이 없어 계획 모델에게 전체 목록이 노출됐다. 모드가 정해진 뒤에는
# 그 모드에 해당하는 것만 보여 주려고 여기서 범위를 밝힌다.
BASE_MODE_FILTERS: dict[SelectionMode, frozenset[str]] = {
    SelectionMode.SINGLE_COURSE: frozenset(
        {
            "academic_year",
            "department_id",
            "course_code",
            "name_ko",
            "grade_year",
            "semester",
            "completion_type",
            "credits",
        }
    ),
    SelectionMode.COURSE_LIST: frozenset(
        {
            "academic_year",
            "department_id",
            "completion_type",
            "grade_year",
            "semester",
            "credits",
        }
    ),
    SelectionMode.SINGLE_RULE: frozenset(
        {"academic_year", "rule_id", "rule_ids", "area_id", "major_type", "admission_type"}
    ),
    SelectionMode.MULTIPLE_RULES: frozenset(
        {"academic_year", "rule_ids", "area_id", "major_type", "admission_type"}
    ),
}
_COURSE_FIELDS = frozenset(
    {
        "course_code",
        "name_ko",
        "grade_year",
        "semester",
        "credits",
        "completion_type",
        "lecture_hours",
        "practice_hours",
    }
)
_RULE_FIELDS = frozenset(
    {"rule_id", "rule_type", "operator", "value", "unit", "description_ko"}
)
BASE_MODE_FIELDS: dict[SelectionMode, frozenset[str]] = {
    SelectionMode.SINGLE_COURSE: _COURSE_FIELDS,
    SelectionMode.COURSE_LIST: _COURSE_FIELDS,
    SelectionMode.SINGLE_RULE: _RULE_FIELDS,
    SelectionMode.MULTIPLE_RULES: _RULE_FIELDS,
}


def allowed_filters_for_mode(selection_mode: object) -> frozenset[str]:
    """Filters the given selection mode may use, or all of them when unknown."""

    family = family_for_mode(selection_mode)
    if family is not None:
        return family.allowed_filters
    if isinstance(selection_mode, str):
        try:
            selection_mode = SelectionMode(selection_mode)
        except ValueError:
            return frozenset(BASE_FILTER_BINDINGS)
    if isinstance(selection_mode, SelectionMode):
        return BASE_MODE_FILTERS.get(selection_mode, frozenset(BASE_FILTER_BINDINGS))
    return frozenset(BASE_FILTER_BINDINGS)


def allowed_fields_for_mode(selection_mode: object) -> frozenset[str] | None:
    """Requestable fields for the mode; None means the caller should not restrict."""

    family = family_for_mode(selection_mode)
    if family is not None:
        return frozenset(family.field_owners)
    if isinstance(selection_mode, str):
        try:
            selection_mode = SelectionMode(selection_mode)
        except ValueError:
            return None
    if isinstance(selection_mode, SelectionMode):
        return BASE_MODE_FIELDS.get(selection_mode)
    return None


EXTENDED_FACT_LABELS = frozenset(
    family.fact_label for family in EXTENDED_FAMILIES.values()
)
FAMILY_BY_FACT_LABEL: dict[str, FactFamily] = {
    family.fact_label: family for family in EXTENDED_FAMILIES.values()
}
# 확장 family 에서만 쓰는 필터. 기존 두 family 의 plan 에 섞여 들어오면 거부한다.
EXTENDED_ONLY_FILTERS = frozenset(
    {
        "credit_category",
        "source_was_blank",
        "is_total",
        "recommended_grade_year",
        "recommended_semester",
        "entry_type",
        "goal_scope",
    }
)


def family_for_mode(selection_mode: object) -> FactFamily | None:
    """Return the extended family for a selection mode, or None for the base modes."""

    if isinstance(selection_mode, str):
        try:
            selection_mode = SelectionMode(selection_mode)
        except ValueError:
            return None
    if isinstance(selection_mode, SelectionMode):
        return EXTENDED_FAMILIES.get(selection_mode)
    return None


def resolve_filter_bindings(selection_mode: object) -> dict[str, FilterBinding]:
    """Resolve filter bindings for one selection mode.

    같은 필터 이름이 fact family 마다 다른 라벨을 가리킬 수 있다. 예를 들어
    ``grade_year`` 는 ``CourseOffering`` 에서는 배열 속성이지만 ``CreditAllocation``
    에서는 정수 속성이다. 이름을 통일해 두면 planner 가 다루기 쉽고, 실제 바인딩은
    여기서 family 별로 결정되므로 검증은 여전히 라벨 단위로 엄격하다.
    """

    bindings = dict(BASE_FILTER_BINDINGS)
    family = family_for_mode(selection_mode)
    if family is not None:
        bindings.update(family.filter_overrides)
    return bindings
