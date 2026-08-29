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
    # 아래는 대학(Institution) 소유 사실과 연계표를 여는 모드다. 한 fact label 이
    # 소유자에 따라 두 모드로 갈리는 경우가 있어(EducationGoal, Competency), 모드와
    # 라벨의 대응은 "모드 하나 -> 라벨 하나"이지 그 역은 아니다. 조회 결과가 언제나
    # 한 라벨에서만 나온다는 provenance 계약은 그대로다.
    MAJOR_COMPETENCY_LIST = "MAJOR_COMPETENCY_LIST"
    UNIVERSITY_COMPETENCY_LIST = "UNIVERSITY_COMPETENCY_LIST"
    UNIVERSITY_GOAL_LIST = "UNIVERSITY_GOAL_LIST"
    CURRICULUM_AGGREGATE_LIST = "CURRICULUM_AGGREGATE_LIST"
    COMPETENCY_AGGREGATE_LIST = "COMPETENCY_AGGREGATE_LIST"
    GOAL_COMPETENCY_ALIGNMENT_LIST = "GOAL_COMPETENCY_ALIGNMENT_LIST"
    CORE_COMPETENCY_ALIGNMENT_LIST = "CORE_COMPETENCY_ALIGNMENT_LIST"
    GOAL_ALIGNMENT_LIST = "GOAL_ALIGNMENT_LIST"


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
    "course_codes": FilterBinding("Course", "course_code", "PROPERTY_IN_PARAMETER"),
    "name_ko": FilterBinding("Course", "name_ko"),
    "rule_id": FilterBinding("Rule", "rule_id"),
    "rule_ids": FilterBinding("Rule", "rule_id", "PROPERTY_IN_PARAMETER"),
    "area_id": FilterBinding("EducationArea", "area_id"),
    "area_ids": FilterBinding(
        "EducationArea", "area_id", "PROPERTY_IN_PARAMETER"
    ),
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
    "goal_id": FilterBinding("EducationGoal", "goal_id"),
    "competency_type": FilterBinding("Competency", "competency_type"),
    "competency_id": FilterBinding("Competency", "competency_id"),
    "aggregate_type": FilterBinding("CurriculumAggregate", "aggregate_type"),
    "alignment_type": FilterBinding("Alignment", "alignment_type"),
    # 원문 공란(strength=NONE)을 답변에서 빼기 위한 필터다. 값을 바꾸지 않고 조회
    # 대상만 좁히며, 공란까지 보려면 계획에서 이 목록을 넓히면 된다.
    "alignment_strengths": FilterBinding(
        "Alignment", "strength", "PROPERTY_IN_PARAMETER"
    ),
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
    *anchors: str,
    field_owners: Mapping[str, str],
    filter_overrides: Mapping[str, FilterBinding] = {},
    conditional_matches: Mapping[str, str] = {},
    conditional_aliases: Mapping[str, str] = {},
    extra_filters: frozenset[str] = frozenset(),
    extra_required_filters: frozenset[str] = frozenset(),
    mandatory_fields: tuple[str, ...] = (),
    order_fields: tuple[str, ...] = (),
    default_filters: Mapping[str, Any] = {},
) -> FactFamily:
    """Build a family anchored under one department's curriculum version.

    ``anchors``는 fact 노드까지 가는 MATCH 줄들이다. 모든 확장 family는 학년도와 학과로
    범위가 고정되며, 이 범위 고정이 여러 학과·연도 데이터가 섞이는 것을 막는다.

    대학(Institution) 소유 사실도 같은 범위 고정을 쓴다. ``(d)-[:PART_OF]->(i)``를 거쳐
    가면 "이 학과가 속한 대학"으로 대상이 닫히므로, 학과 범위를 잃지 않고 대학 단위
    사실에 닿는다. 검증기가 가변 길이 경로와 ``OR``를 금지하므로 소유자가 다르면
    family를 따로 선언한다.
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
            *anchors,
            "MATCH (f)-[:SUPPORTED_BY]->(e:Evidence)",
        ),
        conditional_matches=dict(conditional_matches),
        field_owners=dict(field_owners),
        filter_overrides=dict(filter_overrides),
        required_filters=frozenset({"academic_year", "department_id"})
        | extra_required_filters,
        allowed_filters=frozenset({"academic_year", "department_id"})
        | extra_filters
        | extra_required_filters,
        mandatory_fields=mandatory_fields,
        order_fields=order_fields,
        default_filters=dict(default_filters),
    )


def _alignment_scoped(
    selection_mode: SelectionMode,
    alignment_type: str,
    *,
    source_matches: tuple[str, ...],
    target_matches: tuple[str, ...],
    source_alias: str,
    target_alias: str,
    source_field: str,
    aliases: Mapping[str, str],
    extra_filters: frozenset[str] = frozenset(),
) -> FactFamily:
    """Build one alignment-matrix family.

    ``Alignment``은 소유 관계가 없어 자기 자신만으로는 학과·학년도 범위를 닫을 수 없다.
    그래서 양끝(출발 노드와 도착 노드)을 각각 학과 아래에서 먼저 고정한 뒤, 그 둘을
    잇는 Alignment 만 남긴다. 범위 고정의 책임이 fact 가 아니라 양끝에 있다는 점이
    다른 family 와 다르다.

    ``alignment_type``마다 양끝의 라벨 조합이 하나로 정해져 있어 family 를 나눈다.
    한 family 안에서는 MATCH 모양이 고정되므로 검증기의 고정 길이 경로 제약을 지킨다.
    """

    return FactFamily(
        fact_label="Alignment",
        fact_id_property="alignment_id",
        selection_mode=selection_mode,
        aliases={
            "CurriculumVersion": "cv",
            "Department": "d",
            "Alignment": "f",
            "Evidence": "e",
            **aliases,
        },
        base_matches=(
            "MATCH (cv:CurriculumVersion)-[:FOR_DEPARTMENT]->(d:Department)",
            *source_matches,
            *target_matches,
            f"MATCH (f:Alignment)-[:ALIGNS_FROM]->({source_alias})",
            f"MATCH (f)-[:ALIGNS_TO]->({target_alias})",
            "MATCH (f)-[:SUPPORTED_BY]->(e:Evidence)",
        ),
        conditional_matches={},
        field_owners={
            "alignment_type": "f",
            "strength": "f",
            "source_value": "f",
            source_field: source_alias,
            "name_ko": target_alias,
        },
        filter_overrides={},
        required_filters=frozenset({"academic_year", "department_id"}),
        allowed_filters=frozenset(
            {"academic_year", "department_id", "alignment_type", "alignment_strengths"}
        )
        | extra_filters,
        # alignment_type 은 기본 필터이기도 하지만 필수 필드로도 둔다. 계획 모델을
        # 거치지 않는 경로에서는 기본 필터가 채워지지 않으므로, 이 값이 없으면 어떤
        # 연계표의 칸인지 알 수 없는 Claim 이 만들어진다.
        mandatory_fields=("alignment_type", "strength", source_field, "name_ko"),
        order_fields=("name_ko",),
        # 원문에서 공란인 칸은 strength=NONE 으로 적재돼 있다. "무엇과 연계되는가"를
        # 묻는 질문의 답이 아니므로 기본 조회 대상에서 뺀다. 값을 바꾸지 않고 범위만
        # 좁히는 것이며, 공란까지 보려면 계획에서 이 목록에 NONE 을 넣는다.
        default_filters={
            "alignment_type": alignment_type,
            "alignment_strengths": ["HIGH", "LOW"],
        },
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
    # 학과가 정의한 전공능력.
    SelectionMode.MAJOR_COMPETENCY_LIST: _department_scoped(
        "Competency",
        "competency_id",
        SelectionMode.MAJOR_COMPETENCY_LIST,
        "MATCH (d)-[:DEFINES_COMPETENCY]->(f:Competency)",
        field_owners={
            "name_ko": "f",
            "description_ko": "f",
            "competency_type": "f",
            "normalized_name_ko": "f",
        },
        extra_filters=frozenset({"competency_type", "competency_id"}),
        mandatory_fields=("name_ko",),
        order_fields=("name_ko",),
        # 소유자가 학과인 시점에 이미 전공능력만 남지만, 통제어휘 값을 함께 고정해
        # 데이터가 늘어도 이 모드의 의미가 흔들리지 않게 한다.
        default_filters={"competency_type": "MAJOR"},
    ),
    # 대학이 정의한 핵심역량. 학과를 거쳐 그 학과가 속한 대학으로 범위가 닫힌다.
    # description_ko 는 이 소유자에서 전부 null 이므로 요청 필드에 두지 않는다.
    # 값이 없는 속성을 요청하면 ResultValidator 가 결과 전체를 막는다.
    SelectionMode.UNIVERSITY_COMPETENCY_LIST: _department_scoped(
        "Competency",
        "competency_id",
        SelectionMode.UNIVERSITY_COMPETENCY_LIST,
        "MATCH (d)-[:PART_OF]->(i:Institution)",
        "MATCH (i)-[:DEFINES_COMPETENCY]->(f:Competency)",
        conditional_aliases={"Institution": "i"},
        field_owners={
            "name_ko": "f",
            "competency_type": "f",
            "normalized_name_ko": "f",
        },
        extra_filters=frozenset({"competency_type", "competency_id"}),
        mandatory_fields=("name_ko",),
        order_fields=("name_ko",),
        default_filters={"competency_type": "UNIVERSITY_CORE"},
    ),
    # 대학 교육목표. 학과 교육목표는 EDUCATION_GOAL_LIST 가 담당한다.
    SelectionMode.UNIVERSITY_GOAL_LIST: _department_scoped(
        "EducationGoal",
        "goal_id",
        SelectionMode.UNIVERSITY_GOAL_LIST,
        "MATCH (d)-[:PART_OF]->(i:Institution)",
        "MATCH (i)-[:HAS_EDUCATION_GOAL]->(f:EducationGoal)",
        conditional_aliases={"Institution": "i"},
        field_owners={
            "description_ko": "f",
            "name_ko": "f",
            "goal_order": "f",
            "goal_scope": "f",
        },
        extra_filters=frozenset({"goal_scope", "goal_id"}),
        mandatory_fields=("description_ko",),
        order_fields=("goal_order",),
        default_filters={"goal_scope": "UNIVERSITY"},
    ),
    # 교육과정 집계값. aggregate_type 마다 채워진 속성이 다르므로 필수 필드는
    # 종류와 합계 여부뿐이고, 나머지는 있는 것만 렌더한다.
    SelectionMode.CURRICULUM_AGGREGATE_LIST: _department_scoped(
        "CurriculumAggregate",
        "aggregate_id",
        SelectionMode.CURRICULUM_AGGREGATE_LIST,
        "MATCH (cv)-[:HAS_AGGREGATE]->(f:CurriculumAggregate)",
        field_owners={
            "aggregate_type": "f",
            "course_count": "f",
            "credit_value": "f",
            "lecture_hours": "f",
            "practice_hours": "f",
            "boolean_value": "f",
            "unit": "f",
            "is_total": "f",
        },
        filter_overrides={
            "is_total": FilterBinding("CurriculumAggregate", "is_total"),
        },
        # 집계는 종류마다 채워진 수치가 다르다. 종류를 고정하지 않고 조회하면 한
        # 결과에 빈 칸이 섞이고, 원문의 빈 값을 0 으로 바꾸지 않는 계약 때문에 결과
        # 검증이 조회 전체를 막는다. 어느 종류인지를 필수 범위로 둔다.
        extra_filters=frozenset({"is_total"}),
        extra_required_filters=frozenset({"aggregate_type"}),
        mandatory_fields=("aggregate_type", "is_total"),
        order_fields=("aggregate_type",),
    ),
    # 전공능력별 집계값. 어느 역량의 수치인지가 답변의 핵심이라 역량 이름을 함께
    # 조회한다. AGGREGATES_FOR 가 없는 집계는 위의 CURRICULUM_AGGREGATE_LIST 가 본다.
    SelectionMode.COMPETENCY_AGGREGATE_LIST: _department_scoped(
        "CurriculumAggregate",
        "aggregate_id",
        SelectionMode.COMPETENCY_AGGREGATE_LIST,
        "MATCH (cv)-[:HAS_AGGREGATE]->(f:CurriculumAggregate)",
        "MATCH (f)-[:AGGREGATES_FOR]->(ac:Competency)",
        conditional_aliases={"Competency": "ac"},
        field_owners={
            "aggregate_type": "f",
            "course_count": "f",
            "credit_value": "f",
            "unit": "f",
            "is_total": "f",
            "name_ko": "ac",
        },
        filter_overrides={
            "is_total": FilterBinding("CurriculumAggregate", "is_total"),
        },
        extra_filters=frozenset({"is_total", "competency_id"}),
        extra_required_filters=frozenset({"aggregate_type"}),
        mandatory_fields=("aggregate_type", "is_total", "name_ko"),
        order_fields=("name_ko",),
    ),
    # 학과 교육목표 -> 전공능력 연계표.
    SelectionMode.GOAL_COMPETENCY_ALIGNMENT_LIST: _alignment_scoped(
        SelectionMode.GOAL_COMPETENCY_ALIGNMENT_LIST,
        "DEPARTMENT_GOAL_TO_MAJOR_COMPETENCY",
        source_matches=("MATCH (d)-[:HAS_EDUCATION_GOAL]->(sg:EducationGoal)",),
        target_matches=("MATCH (d)-[:DEFINES_COMPETENCY]->(tc:Competency)",),
        source_alias="sg",
        target_alias="tc",
        source_field="description_ko",
        aliases={"EducationGoal": "sg", "Competency": "tc"},
        extra_filters=frozenset({"goal_id", "competency_id"}),
    ),
    # 대학 핵심역량 -> 전공능력 연계표. 양끝이 모두 Competency 라서 이름 속성이
    # 겹친다. RETURN 별칭은 온톨로지 속성명과 같아야 하므로 출발 쪽은
    # normalized_name_ko 를, 도착 쪽은 name_ko 를 쓴다.
    SelectionMode.CORE_COMPETENCY_ALIGNMENT_LIST: _alignment_scoped(
        SelectionMode.CORE_COMPETENCY_ALIGNMENT_LIST,
        "UNIVERSITY_CORE_TO_MAJOR_COMPETENCY",
        source_matches=(
            "MATCH (d)-[:PART_OF]->(i:Institution)",
            "MATCH (i)-[:DEFINES_COMPETENCY]->(sc:Competency)",
        ),
        target_matches=("MATCH (d)-[:DEFINES_COMPETENCY]->(tc:Competency)",),
        source_alias="sc",
        target_alias="tc",
        source_field="normalized_name_ko",
        aliases={"Institution": "i", "Competency": "tc"},
        extra_filters=frozenset({"competency_id"}),
    ),
    # 대학 교육목표 -> 학과 교육목표 연계표.
    SelectionMode.GOAL_ALIGNMENT_LIST: _alignment_scoped(
        SelectionMode.GOAL_ALIGNMENT_LIST,
        "UNIVERSITY_GOAL_TO_DEPARTMENT_GOAL",
        source_matches=(
            "MATCH (d)-[:PART_OF]->(i:Institution)",
            "MATCH (i)-[:HAS_EDUCATION_GOAL]->(sg:EducationGoal)",
        ),
        target_matches=("MATCH (d)-[:HAS_EDUCATION_GOAL]->(tg:EducationGoal)",),
        source_alias="sg",
        target_alias="tg",
        source_field="description_ko",
        aliases={"Institution": "i", "EducationGoal": "tg"},
        extra_filters=frozenset({"goal_id"}),
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
            "area_id",
            "area_ids",
            "course_codes",
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
        "goal_id",
        "competency_type",
        "competency_id",
        "aggregate_type",
        "alignment_type",
        "alignment_strengths",
    }
)


def family_for_mode(selection_mode: object) -> FactFamily | None:
    """Return the extended family for a selection mode, or None for the base modes.

    조회는 언제나 모드를 키로 한다. 한 fact label 이 소유자별로 여러 모드에 걸릴 수
    있으므로(대학 소유 / 학과 소유), 라벨을 키로 family 를 찾으면 안 된다.
    """

    if isinstance(selection_mode, str):
        try:
            selection_mode = SelectionMode(selection_mode)
        except ValueError:
            return None
    if isinstance(selection_mode, SelectionMode):
        return EXTENDED_FAMILIES.get(selection_mode)
    return None


def family_for_result(selection_mode: object, fact_label: str) -> FactFamily | None:
    """Return the family only when the mode and the returned fact label agree."""

    family = family_for_mode(selection_mode)
    if family is None or family.fact_label != fact_label:
        return None
    return family


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
