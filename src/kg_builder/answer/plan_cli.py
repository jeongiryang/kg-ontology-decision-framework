"""Answer one explicitly supplied QueryPlan without a planning model.

자연어 CLI(`kg_builder.answer.cli`)는 로컬 LLM이 있어야 동작한다. 이 CLI는 QueryPlan을
직접 받아 **LLM 두 호출만** 결정론적 대체물로 바꾸고, 나머지 경로는 그대로 통과시킨다.

    QueryPlan 검증 → 스키마 선택 → Cypher 검증 → Neo4j EXPLAIN → 실행 →
    결과 검증 → Claim 생성 → Claim 재검증 → 한국어 렌더 → Citation

즉 안전 관문 6개와 근거 검증이 모두 실제로 실행된다. 답변 커버리지가 실제로 넓어졌는지
LLM 품질과 분리해서 확인할 때 쓴다. `NEO4J_QUERY_*` 읽기 전용 설정이 필요하다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from neo4j import GraphDatabase

from kg_builder.config import ConfigurationError, Neo4jQuerySettings
from kg_builder.llm.cypher_generator import build_syntax_scaffold
from kg_builder.llm.models import PlanningOutcome, PlanningStatus
from kg_builder.query.fact_families import EXTENDED_FAMILIES
from kg_builder.query.natural_language_service import NaturalLanguageQueryService
from kg_builder.query.query_executor import DynamicQueryExecutor
from kg_builder.query.query_explainer import QueryExplainer
from kg_builder.query.query_plan import QueryPlan
from kg_builder.query.safety_pipeline import SafetyPipeline
from kg_builder.query.schema_catalog import DEFAULT_SPEC_PATH, ROOT, SchemaCatalog
from kg_builder.query.schema_selector import QuerySchemaSelector

from .service import CurriculumChatService


class FixedQueryPlanner:
    """Return one caller-supplied plan; it never contacts a model."""

    def __init__(self, payload: Mapping[str, Any], catalog: SchemaCatalog):
        self.payload = dict(payload)
        self.catalog = catalog

    def plan(self, question: str) -> PlanningOutcome:
        payload = dict(self.payload)
        payload["question"] = question
        return PlanningOutcome(
            status=PlanningStatus.READY,
            plan=QueryPlan.from_dict(payload, self.catalog),
        )


class ScaffoldCypherGenerator:
    """Emit the plan's syntax scaffold verbatim instead of asking a model to write it.

    스캐폴드는 생성 모델에게 주는 문법 레일이며 정답 값을 담지 않는다. 그대로 제출해도
    CypherValidator, EXPLAIN, ResultValidator 를 예외 없이 통과해야 한다.
    """

    def generate(self, plan, schema_subset, *, previous_error_code=None) -> str:
        del previous_error_code
        return build_syntax_scaffold(plan, schema_subset)


def default_bundle_path() -> Path:
    """Locate a verified bundle without pinning one academic year in code."""

    candidates = sorted(ROOT.glob("data/verified/*/*_kg_data.json"))
    if not candidates:
        raise SampleScopeError("no verified bundle found under data/verified/")
    return candidates[-1]


class SampleScopeError(RuntimeError):
    """Raised when the bundle has no runnable example for a fact family."""


def bundle_scope(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Read the academic year and department that the bundle actually contains."""

    years: set[int] = set()
    departments: list[str] = []
    for node in bundle["nodes"]:
        labels, properties = set(node["labels"]), node["properties"]
        if "CurriculumVersion" in labels and isinstance(
            properties.get("academic_year"), int
        ):
            years.add(properties["academic_year"])
        if "Department" in labels and isinstance(properties.get("department_id"), str):
            departments.append(properties["department_id"])
    if not years or not departments:
        raise SampleScopeError("bundle has no CurriculumVersion/Department to scope by")
    return {"academic_year": max(years), "department_id": sorted(departments)[0]}


def _facts(bundle: Mapping[str, Any], label: str) -> list[Mapping[str, Any]]:
    return [
        node["properties"]
        for node in bundle["nodes"]
        if label in node["labels"] and node["properties"].get("status") == "VERIFIED"
    ]


def example_plans(bundle_path: Path | None = None) -> list[dict[str, Any]]:
    """Build one runnable plan per extended fact family from the bundle itself.

    학년도·학과·범주 값을 코드에 적지 않는다. 다른 연도나 학과의 bundle 을 넣어도
    그 데이터에 실제로 있는 값으로 예시가 만들어져야, 이 CLI 가 특정 데이터에
    묶이지 않는다.
    """

    path = bundle_path or default_bundle_path()
    bundle = json.loads(path.read_text(encoding="utf-8"))
    scope = bundle_scope(bundle)

    # 값이 적혀 있고 합계가 아닌 배분 행에서 실제 범주 하나를 고른다.
    allocations = [
        item
        for item in _facts(bundle, "CreditAllocation")
        if item.get("source_was_blank") is False
        and item.get("is_total") is False
        and isinstance(item.get("credit_category"), str)
    ]
    # 학년·학기가 채워진 로드맵 항목에서 실제 시기를 고른다.
    roadmap = [
        item
        for item in _facts(bundle, "RoadmapEntry")
        if isinstance(item.get("grade_year"), int) and isinstance(item.get("semester"), str)
    ]
    # 집계는 종류마다 채워진 수치가 다르다. 과목 수와 학점이 함께 있는 종류를 골라
    # 예시가 특정 aggregate_type 문자열에 묶이지 않게 한다.
    aggregates = [
        item
        for item in _facts(bundle, "CurriculumAggregate")
        if item.get("course_count") is not None and item.get("credit_value") is not None
    ]
    competency_aggregate_ids = {
        relationship["from_id"]
        for relationship in bundle["relationships"]
        if relationship["type"] == "AGGREGATES_FOR"
    }
    competency_aggregates = [
        node["properties"]
        for node in bundle["nodes"]
        if "CurriculumAggregate" in node["labels"]
        and node["id"] in competency_aggregate_ids
        and node["properties"].get("status") == "VERIFIED"
        and node["properties"].get("course_count") is not None
    ]
    if not allocations or not roadmap:
        raise SampleScopeError("bundle lacks credit-allocation or roadmap examples")
    if not aggregates or not competency_aggregates:
        raise SampleScopeError("bundle lacks curriculum-aggregate examples")
    allocation = sorted(allocations, key=lambda item: item["credit_category"])[0]
    entry = sorted(roadmap, key=lambda item: (item["grade_year"], item["semester"]))[0]
    # 역량에 매달린 집계는 COMPETENCY_AGGREGATE_LIST 가 이름과 함께 답한다. 일반
    # 모드 예시는 그 연결이 없는 집계를 골라야 두 모드의 쓰임이 구분돼 드러난다.
    standalone = [
        node["properties"]["aggregate_type"]
        for node in bundle["nodes"]
        if "CurriculumAggregate" in node["labels"]
        and node["id"] not in competency_aggregate_ids
        and node["properties"].get("status") == "VERIFIED"
        and node["properties"].get("course_count") is not None
    ]
    aggregate_type = sorted(standalone or [item["aggregate_type"] for item in aggregates])[0]
    competency_aggregate_type = sorted(
        item["aggregate_type"] for item in competency_aggregates
    )[0]

    samples: dict[str, tuple[dict[str, Any], list[str]]] = {
        "CREDIT_ALLOCATION_LIST": (
            # source_was_blank=False 는 원문에 값이 적힌 행만 고른다. 빈칸 행의
            # allocated_credits 는 null 이며 0 으로 바꾸지 않는 것이 데이터 계약이다.
            {
                **scope,
                "credit_category": allocation["credit_category"],
                "source_was_blank": False,
                "is_total": False,
            },
            ["credit_category", "allocated_credits", "is_total", "grade_year", "semester"],
        ),
        "ROADMAP_LIST": (
            {**scope, "grade_year": entry["grade_year"], "semester": entry["semester"]},
            ["raw_label", "entry_type"],
        ),
        "EDUCATION_GOAL_LIST": (dict(scope), ["description_ko", "goal_order"]),
        "CAREER_FIELD_LIST": (dict(scope), ["name_ko", "field_order"]),
        "TALENT_PROFILE_LIST": (dict(scope), ["description_ko", "profile_order"]),
        "COURSE_RECOMMENDATION_LIST": (
            dict(scope),
            [
                "course_name_ko",
                "course_code",
                "recommended_grade_year",
                "recommended_semester",
                "credits",
            ],
        ),
        "UNIVERSITY_GOAL_LIST": (
            {**scope, "goal_scope": "UNIVERSITY"},
            ["description_ko", "goal_order"],
        ),
        "MAJOR_COMPETENCY_LIST": (
            {**scope, "competency_type": "MAJOR"},
            ["name_ko", "description_ko"],
        ),
        # 대학 핵심역량은 description_ko 가 전부 비어 있다. 값이 없는 속성을 요청하면
        # ResultValidator 가 결과 전체를 막으므로 이름만 요청한다.
        "UNIVERSITY_COMPETENCY_LIST": (
            {**scope, "competency_type": "UNIVERSITY_CORE"},
            ["name_ko"],
        ),
        "CURRICULUM_AGGREGATE_LIST": (
            {**scope, "aggregate_type": aggregate_type},
            ["aggregate_type", "is_total", "course_count", "credit_value"],
        ),
        "COMPETENCY_AGGREGATE_LIST": (
            {**scope, "aggregate_type": competency_aggregate_type},
            ["aggregate_type", "is_total", "name_ko", "course_count", "credit_value"],
        ),
        "GOAL_COMPETENCY_ALIGNMENT_LIST": (
            {**scope, "alignment_strengths": ["HIGH", "LOW"]},
            ["alignment_type", "strength", "description_ko", "name_ko"],
        ),
        "CORE_COMPETENCY_ALIGNMENT_LIST": (
            {**scope, "alignment_strengths": ["HIGH", "LOW"]},
            ["alignment_type", "strength", "normalized_name_ko", "name_ko"],
        ),
        "GOAL_ALIGNMENT_LIST": (
            {**scope, "alignment_strengths": ["HIGH", "LOW"]},
            ["alignment_type", "strength", "description_ko", "name_ko"],
        ),
    }
    plans = []
    for mode in EXTENDED_FAMILIES:
        filters, fields = samples[mode.value]
        plans.append(
            {
                "selection_mode": mode.value,
                "filters": filters,
                "requested_fields": fields,
                "evidence_required": True,
            }
        )
    return plans


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plan",
        help=(
            "QueryPlan JSON with selection_mode, filters, requested_fields and "
            "evidence_required. Run --print-examples for runnable plans built from "
            "the verified bundle."
        ),
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        default=None,
        help="verified bundle used to build the built-in examples (default: newest)",
    )
    parser.add_argument(
        "--question",
        default="계획 직접 지정 질의",
        help="trace label only; it does not steer the query",
    )
    parser.add_argument(
        "--all-examples",
        action="store_true",
        help="run one built-in plan per extended fact family",
    )
    parser.add_argument(
        "--print-examples",
        action="store_true",
        help="print the built-in example plans and exit without touching the database",
    )
    return parser


def _run(service: CurriculumChatService, question: str, payload: Mapping[str, Any]) -> int:
    result = service.ask(question)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    del payload
    return 0 if result.status.value in {"ANSWERABLE", "NOT_FOUND"} else 2


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        examples = example_plans(args.bundle)
    except (SampleScopeError, OSError, ValueError) as exc:
        if args.plan is None:
            print(f"cannot build example plans: {exc}", file=sys.stderr)
            return 2
        examples = []
    if args.print_examples:
        print(json.dumps(examples, ensure_ascii=False, indent=2))
        return 0
    if bool(args.plan) == bool(args.all_examples):
        print("choose exactly one of --plan or --all-examples", file=sys.stderr)
        return 2
    if args.plan:
        try:
            payloads = [json.loads(args.plan)]
        except json.JSONDecodeError as exc:
            print(f"invalid --plan JSON: {exc}", file=sys.stderr)
            return 2
        if not isinstance(payloads[0], Mapping):
            print("--plan must be a JSON object", file=sys.stderr)
            return 2
    else:
        payloads = examples

    try:
        neo4j_settings = Neo4jQuerySettings.from_env()
    except ConfigurationError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    catalog = SchemaCatalog.from_spec(DEFAULT_SPEC_PATH)
    exit_code = 0
    with GraphDatabase.driver(
        neo4j_settings.uri, auth=(neo4j_settings.user, neo4j_settings.password)
    ) as driver:
        driver.verify_connectivity()
        pipeline = SafetyPipeline(
            QueryExplainer(driver, neo4j_settings.database),
            DynamicQueryExecutor(driver, neo4j_settings.database),
        )
        for payload in payloads:
            if len(payloads) > 1:
                print(f"----- {payload.get('selection_mode')} -----")
            service = CurriculumChatService(
                NaturalLanguageQueryService(
                    FixedQueryPlanner(payload, catalog),
                    ScaffoldCypherGenerator(),
                    pipeline,
                    QuerySchemaSelector(),
                    model="fixed-plan",
                    generator_retries=0,
                )
            )
            exit_code = max(exit_code, _run(service, args.question, payload))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
