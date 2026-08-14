"""확장 fact family가 도달 범위만 넓히고 근거 규칙은 그대로 지키는지 검증한다."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from kg_builder.answer.claim_builder import ClaimBuilder
from kg_builder.answer.claim_validator import ClaimValidator
from kg_builder.answer.contracts import ClaimType, GroundingError
from kg_builder.answer.korean_renderer import KoreanAnswerRenderer
from kg_builder.answer.plan_cli import (
    ScaffoldCypherGenerator,
    bundle_scope,
    default_bundle_path,
    example_plans,
)
from kg_builder.answer.renderer import CitationRenderer
from kg_builder.answer.service import CurriculumChatService
from kg_builder.llm.cypher_generator import build_syntax_scaffold
from kg_builder.query.cypher_validator import CypherValidationError, CypherValidator
from kg_builder.query.fact_families import (
    EXTENDED_FAMILIES,
    EXTENDED_FACT_LABELS,
    SelectionMode,
    resolve_filter_bindings,
)
from kg_builder.query.query_plan import QueryPlan, QueryPlanError
from kg_builder.query.schema_catalog import (
    DEFAULT_QUERY_SCHEMA_PATH,
    DEFAULT_SPEC_PATH,
    SchemaCatalog,
)
from kg_builder.query.schema_selector import QuerySchemaSelector

ROOT = Path(__file__).resolve().parents[1]
# 학년도·학과를 테스트에 적지 않는다. 다른 연도나 학과의 bundle 로 바뀌어도 같은
# 계약을 검사해야 하므로 적재된 데이터에서 범위를 읽는다.
BUNDLE_PATH = default_bundle_path()
SCOPE = bundle_scope(json.loads(BUNDLE_PATH.read_text(encoding="utf-8")))
DEPARTMENT = SCOPE["department_id"]
ACADEMIC_YEAR = SCOPE["academic_year"]
FACT_ID_PROPERTIES = {
    family.fact_label: family.fact_id_property for family in EXTENDED_FAMILIES.values()
}


def spec_catalog() -> SchemaCatalog:
    return SchemaCatalog.from_spec(DEFAULT_SPEC_PATH)


def generated_catalog() -> SchemaCatalog:
    return SchemaCatalog.from_generated(DEFAULT_QUERY_SCHEMA_PATH, DEFAULT_SPEC_PATH)


def evidence_columns(index: int) -> dict[str, Any]:
    return {
        "evidence_id": f"evidence:test:{index}",
        "excerpt_page": 3,
        "source_pdf_page": 248,
        "printed_page": 240,
        "source_text": "원문 근거 문장",
        "evidence_verification_status": "VERIFIED",
    }


def row(fact_label: str, fact_id: str, index: int, **values: Any) -> dict[str, Any]:
    payload = {
        "fact_id": fact_id,
        "fact_label": fact_label,
        "fact_status": "VERIFIED",
        **SCOPE,
        **values,
        **evidence_columns(index),
    }
    return payload


def narrative_rows() -> list[dict[str, Any]]:
    return [
        row("CareerField", "career:1", 1, name_ko="프로그램 개발실", field_order=1),
        row("CareerField", "career:2", 2, name_ko="벤처기업 창업", field_order=2),
    ]


def allocation_rows() -> list[dict[str, Any]]:
    return [
        row(
            "CreditAllocation",
            "alloc:1",
            1,
            credit_category="기초교양",
            allocated_credits=4,
            is_total=False,
            grade_year=1,
            semester="FIRST",
        ),
        row(
            "CreditAllocation",
            "alloc:total",
            2,
            credit_category="기초교양",
            allocated_credits=9,
            is_total=True,
            grade_year=None,
            semester=None,
        ),
    ]


def plan_payload(mode: str, fields: list[str], **filters: Any) -> dict[str, Any]:
    return {
        "filters": {**SCOPE, **filters},
        "requested_fields": fields,
        "evidence_required": True,
        "selection_mode": mode,
        "intent": None,
    }


def answer_for(rows: list[dict[str, Any]], payload: dict[str, Any]) -> str:
    claims = ClaimBuilder().build(rows, payload)
    validated = ClaimValidator().validate(claims, rows, payload)
    return KoreanAnswerRenderer().render(validated).answer_text


class FamilyDeclarationTests(unittest.TestCase):
    """선언한 라벨·속성이 온톨로지에 실제로 존재하는지 확인한다."""

    def setUp(self) -> None:
        self.catalog = spec_catalog()

    def test_every_family_label_and_field_is_declared(self) -> None:
        for mode, family in EXTENDED_FAMILIES.items():
            with self.subTest(mode=mode.value):
                self.assertIn(family.fact_label, self.catalog.nodes)
                properties = self.catalog.properties_for_labels({family.fact_label})
                self.assertIn(family.fact_id_property, properties)
                self.assertIn("status", properties)
                for field, alias in family.field_owners.items():
                    if alias == family.fact_alias:
                        self.assertIn(field, properties)

    def test_mandatory_fields_are_exposed_fields(self) -> None:
        for mode, family in EXTENDED_FAMILIES.items():
            with self.subTest(mode=mode.value):
                self.assertTrue(family.mandatory_fields)
                for field in family.mandatory_fields:
                    self.assertIn(field, family.field_owners)

    def test_every_family_reaches_evidence_directly(self) -> None:
        bundle = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
        supported = {
            relationship["from_id"]
            for relationship in bundle["relationships"]
            if relationship["type"] == "SUPPORTED_BY"
        }
        for label in sorted(EXTENDED_FACT_LABELS):
            with self.subTest(label=label):
                facts = [
                    node["id"] for node in bundle["nodes"] if label in node["labels"]
                ]
                self.assertTrue(facts, f"{label} 노드가 기준 데이터에 없다")
                missing = [fact for fact in facts if fact not in supported]
                self.assertEqual(
                    missing, [], f"{label}에 SUPPORTED_BY가 없는 노드가 있다"
                )

    def test_default_filters_are_allowed_filters(self) -> None:
        for mode, family in EXTENDED_FAMILIES.items():
            with self.subTest(mode=mode.value):
                for name in family.default_filters:
                    self.assertIn(name, family.allowed_filters)

    def test_credit_allocation_defaults_exclude_blank_and_total_rows(self) -> None:
        """원문 빈칸과 합계 행을 기본 조회에서 분리한다는 계약을 고정한다."""

        family = EXTENDED_FAMILIES[SelectionMode.CREDIT_ALLOCATION_LIST]
        self.assertEqual(
            dict(family.default_filters), {"source_was_blank": False, "is_total": False}
        )

    def test_filter_bindings_are_resolved_per_family(self) -> None:
        offering = resolve_filter_bindings(SelectionMode.COURSE_LIST)["grade_year"]
        self.assertEqual(offering.label, "CourseOffering")
        self.assertEqual(offering.operator, "PARAMETER_IN_PROPERTY")
        allocation = resolve_filter_bindings(SelectionMode.CREDIT_ALLOCATION_LIST)[
            "grade_year"
        ]
        self.assertEqual(allocation.label, "CreditAllocation")
        self.assertEqual(allocation.operator, "EQUALS")


class PlanContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = spec_catalog()

    def test_extended_plan_is_accepted(self) -> None:
        plan = QueryPlan.from_dict(
            {"question": "진출 분야", **plan_payload("CAREER_FIELD_LIST", ["name_ko", "field_order"])},
            self.catalog,
        )
        self.assertIs(plan.selection_mode, SelectionMode.CAREER_FIELD_LIST)

    def test_family_rejects_fields_of_another_family(self) -> None:
        with self.assertRaises(QueryPlanError):
            QueryPlan.from_dict(
                {
                    "question": "혼합",
                    **plan_payload("CAREER_FIELD_LIST", ["name_ko", "raw_label"]),
                },
                self.catalog,
            )

    def test_family_rejects_unsupported_filter(self) -> None:
        with self.assertRaises(QueryPlanError):
            QueryPlan.from_dict(
                {
                    "question": "필터",
                    **plan_payload(
                        "CAREER_FIELD_LIST", ["name_ko"], completion_type="MAJOR_REQUIRED"
                    ),
                },
                self.catalog,
            )

    def test_family_requires_department_scope(self) -> None:
        with self.assertRaises(QueryPlanError):
            QueryPlan.from_dict(
                {
                    "question": "학과 없음",
                    "filters": {"academic_year": 2026},
                    "requested_fields": ["name_ko"],
                    "evidence_required": True,
                    "selection_mode": "CAREER_FIELD_LIST",
                },
                self.catalog,
            )

    def test_boolean_filter_rejects_a_non_boolean_value(self) -> None:
        with self.assertRaises(QueryPlanError):
            QueryPlan.from_dict(
                {
                    "question": "불리언",
                    **plan_payload(
                        "CREDIT_ALLOCATION_LIST",
                        ["credit_category", "allocated_credits", "is_total"],
                        source_was_blank="false",
                    ),
                },
                self.catalog,
            )

    def test_extended_only_filter_is_rejected_without_a_family(self) -> None:
        with self.assertRaises(QueryPlanError):
            QueryPlan.from_dict(
                {
                    "question": "기존 모드",
                    "filters": {**SCOPE, "entry_type": "COURSE"},
                    "requested_fields": ["name_ko"],
                    "evidence_required": True,
                    "selection_mode": "COURSE_LIST",
                },
                self.catalog,
            )


class ScaffoldSafetyTests(unittest.TestCase):
    """확장 family의 생성 Cypher도 기존 검증기를 그대로 통과해야 한다."""

    def setUp(self) -> None:
        self.catalog = generated_catalog()
        self.selector = QuerySchemaSelector()

    def _validated(self, payload: dict[str, Any]):
        plan = QueryPlan.from_dict({"question": "테스트", **payload}, self.catalog)
        subset = self.selector.select(plan)
        cypher = build_syntax_scaffold(plan, subset)
        return plan, subset, CypherValidator(self.catalog).validate(plan, cypher)

    def test_every_example_plan_passes_static_validation(self) -> None:
        for payload in example_plans():
            with self.subTest(mode=payload["selection_mode"]):
                plan, subset, validated = self._validated(dict(payload))
                family = EXTENDED_FAMILIES[SelectionMode(payload["selection_mode"])]
                self.assertEqual(subset["selected_fact_family"], family.fact_label)
                self.assertEqual(validated.provenance.fact_label, family.fact_label)
                self.assertEqual(
                    validated.provenance.fact_id_property, family.fact_id_property
                )
                self.assertIn("SUPPORTED_BY", validated.relationship_types)
                self.assertEqual(set(validated.parameters), set(plan.filters))

    def test_scaffold_generator_matches_direct_scaffold(self) -> None:
        payload = dict(example_plans()[0])
        plan = QueryPlan.from_dict({"question": "테스트", **payload}, self.catalog)
        subset = self.selector.select(plan)
        self.assertEqual(
            ScaffoldCypherGenerator().generate(plan, subset),
            build_syntax_scaffold(plan, subset),
        )

    def test_scaffold_keeps_verified_predicates(self) -> None:
        for payload in example_plans():
            with self.subTest(mode=payload["selection_mode"]):
                _, _, validated = self._validated(dict(payload))
                self.assertIn("status = 'VERIFIED'", validated.text)
                self.assertIn("verification_status = 'VERIFIED'", validated.text)

    def test_filter_bound_to_the_wrong_label_is_rejected(self) -> None:
        payload = plan_payload(
            "CREDIT_ALLOCATION_LIST",
            ["credit_category", "allocated_credits", "is_total"],
            grade_year=1,
        )
        plan = QueryPlan.from_dict({"question": "테스트", **payload}, self.catalog)
        subset = self.selector.select(plan)
        cypher = build_syntax_scaffold(plan, subset)
        # CreditAllocation.grade_year 는 EQUALS 바인딩이다. CourseOffering 쪽 배열
        # 연산자로 바꿔 쓰면 검증기가 막아야 한다.
        tampered = cypher.replace(
            "f.grade_year = $grade_year", "$grade_year IN f.grade_year"
        )
        with self.assertRaises(CypherValidationError) as caught:
            CypherValidator(self.catalog).validate(plan, tampered)
        self.assertEqual(caught.exception.code, "CYPHER_FILTER_BINDING")

    def test_dropping_the_evidence_path_is_rejected(self) -> None:
        payload = dict(example_plans()[0])
        plan = QueryPlan.from_dict({"question": "테스트", **payload}, self.catalog)
        subset = self.selector.select(plan)
        cypher = build_syntax_scaffold(plan, subset)
        tampered = cypher.replace("MATCH (f)-[:SUPPORTED_BY]->(e:Evidence)\n", "")
        with self.assertRaises(CypherValidationError):
            CypherValidator(self.catalog).validate(plan, tampered)


class PlannerCompletionTests(unittest.TestCase):
    """계획 모델이 빠뜨린 구조적 값만 보강하는지 확인한다."""

    def setUp(self) -> None:
        from kg_builder.llm.client import LLMGeneration

        class SingleReplyClient:
            model = "fake-local-model"

            def __init__(self, payload):
                self.payload = payload

            def generate_json(self, *, system_prompt, user_prompt, response_schema):
                del system_prompt, user_prompt, response_schema
                return LLMGeneration(dict(self.payload), 0.01, self.model)

        self.client_factory = SingleReplyClient
        self.catalog = spec_catalog()

    def _plan(self, payload):
        from kg_builder.llm.planner import LocalQueryPlanner

        planner = LocalQueryPlanner(
            self.client_factory(payload),
            catalog=self.catalog,
            planner_context={
                "academic_years": [2026],
                "departments": [{"department_id": DEPARTMENT, "name_ko": "컴퓨터공학과"}],
                "verified_rule_identifiers": [],
                "supported_filters": [],
                "controlled_vocabularies": {},
                "supported_requested_fields": [],
            },
        )
        return planner.plan("테스트 질문")

    def test_single_academic_year_is_filled_in(self) -> None:
        outcome = self._plan(
            {
                "status": "READY",
                "intent": None,
                "filters": {"department_id": DEPARTMENT},
                "requested_fields": ["description_ko", "goal_order"],
                "evidence_required": True,
                "message": None,
                "selection_mode": "EDUCATION_GOAL_LIST",
            }
        )
        self.assertIsNotNone(outcome.plan)
        self.assertEqual(outcome.plan.filters["academic_year"], 2026)

    def test_mandatory_fields_are_added(self) -> None:
        outcome = self._plan(
            {
                "status": "READY",
                "intent": None,
                "filters": {"academic_year": 2026, "department_id": DEPARTMENT},
                "requested_fields": ["allocated_credits"],
                "evidence_required": True,
                "message": None,
                "selection_mode": "CREDIT_ALLOCATION_LIST",
            }
        )
        self.assertIsNotNone(outcome.plan)
        self.assertIn("credit_category", outcome.plan.requested_fields)
        self.assertIn("is_total", outcome.plan.requested_fields)

    def test_default_scope_filters_are_added(self) -> None:
        outcome = self._plan(
            {
                "status": "READY",
                "intent": None,
                "filters": {"academic_year": 2026, "department_id": DEPARTMENT},
                "requested_fields": ["credit_category", "allocated_credits", "is_total"],
                "evidence_required": True,
                "message": None,
                "selection_mode": "CREDIT_ALLOCATION_LIST",
            }
        )
        self.assertIsNotNone(outcome.plan)
        self.assertIs(outcome.plan.filters["source_was_blank"], False)
        self.assertIs(outcome.plan.filters["is_total"], False)

    def test_explicit_filter_wins_over_the_default(self) -> None:
        outcome = self._plan(
            {
                "status": "READY",
                "intent": None,
                "filters": {
                    "academic_year": 2026,
                    "department_id": DEPARTMENT,
                    "is_total": True,
                },
                "requested_fields": ["credit_category", "allocated_credits", "is_total"],
                "evidence_required": True,
                "message": None,
                "selection_mode": "CREDIT_ALLOCATION_LIST",
            }
        )
        self.assertIsNotNone(outcome.plan)
        self.assertIs(outcome.plan.filters["is_total"], True)

    def test_recommendation_accepts_its_own_period_filters(self) -> None:
        outcome = self._plan(
            {
                "status": "READY",
                "intent": None,
                "filters": {
                    "academic_year": 2026,
                    "department_id": DEPARTMENT,
                    "recommended_grade_year": 1,
                    "recommended_semester": "FIRST",
                },
                "requested_fields": ["course_name_ko", "course_code"],
                "evidence_required": True,
                "message": None,
                "selection_mode": "COURSE_RECOMMENDATION_LIST",
            }
        )
        self.assertIsNotNone(outcome.plan)
        self.assertEqual(outcome.plan.filters["recommended_grade_year"], 1)


class ExtendedAnswerTests(unittest.TestCase):
    def test_narrative_answer_lists_verified_text(self) -> None:
        rows = narrative_rows()
        payload = plan_payload("CAREER_FIELD_LIST", ["name_ko", "field_order"])
        text = answer_for(rows, payload)
        self.assertIn("졸업 후 진출 분야는", text)
        self.assertIn("프로그램 개발실", text)
        self.assertIn("벤처기업 창업", text)

    def test_allocation_answer_marks_the_source_total_row(self) -> None:
        payload = plan_payload(
            "CREDIT_ALLOCATION_LIST",
            ["credit_category", "allocated_credits", "is_total", "grade_year", "semester"],
        )
        text = answer_for(allocation_rows(), payload)
        self.assertIn("1학년 1학기 4학점", text)
        self.assertIn("합계 9학점", text)

    def test_allocation_claims_do_not_compute_a_sum(self) -> None:
        """항목 합계를 우리가 계산하지 않는다는 계약을 고정한다."""

        payload = plan_payload(
            "CREDIT_ALLOCATION_LIST",
            ["credit_category", "allocated_credits", "is_total"],
        )
        claims = ClaimBuilder().build(allocation_rows(), payload)
        self.assertEqual([claim.claim_type for claim in claims], [ClaimType.ALLOCATION_LIST])
        self.assertNotIn(ClaimType.AGGREGATE, {claim.claim_type for claim in claims})

    def test_roadmap_answer_groups_by_period_and_type(self) -> None:
        rows = [
            row(
                "RoadmapEntry",
                "roadmap:1",
                1,
                raw_label="웹프로그래밍",
                entry_type="COURSE",
                grade_year=1,
                semester="FIRST",
            ),
            row(
                "RoadmapEntry",
                "roadmap:2",
                2,
                raw_label="피지컬AI입문",
                entry_type="COURSE",
                grade_year=1,
                semester="FIRST",
            ),
        ]
        payload = plan_payload(
            "ROADMAP_LIST", ["raw_label", "entry_type", "grade_year", "semester"]
        )
        text = answer_for(rows, payload)
        self.assertIn("1학년 1학기 권장 교과목은", text)
        self.assertIn("웹프로그래밍", text)

    def test_recommendation_answer_includes_verified_details(self) -> None:
        rows = [
            row(
                "CourseRecommendation",
                "rec:1",
                1,
                course_name_ko="컴퓨터개론",
                course_code="GEA7260",
                recommended_grade_year=1,
                recommended_semester="FIRST",
                credits=3,
            )
        ]
        payload = plan_payload(
            "COURSE_RECOMMENDATION_LIST",
            [
                "course_name_ko",
                "course_code",
                "recommended_grade_year",
                "recommended_semester",
                "credits",
            ],
        )
        text = answer_for(rows, payload)
        self.assertIn("컴퓨터개론(GEA7260, 1학년 1학기, 3학점)", text)

    def test_answer_is_deterministic(self) -> None:
        rows = narrative_rows()
        payload = plan_payload("CAREER_FIELD_LIST", ["name_ko", "field_order"])
        self.assertEqual(answer_for(rows, payload), answer_for(rows, payload))


class ExtendedGroundingTests(unittest.TestCase):
    """확장해도 '근거 없는 값은 답변이 될 수 없다'가 유지되는지 확인한다."""

    def setUp(self) -> None:
        self.rows = narrative_rows()
        self.payload = plan_payload("CAREER_FIELD_LIST", ["name_ko", "field_order"])
        self.builder = ClaimBuilder()
        self.validator = ClaimValidator()

    def test_tampered_item_text_is_rejected(self) -> None:
        claims = self.builder.build(self.rows, self.payload)
        items = list(claims[0].value)
        forged = type(items[0])(items[0].fact_id, "존재하지 않는 진출 분야", items[0].order)
        tampered = (
            type(claims[0])(
                claims[0].claim_id,
                claims[0].claim_type,
                claims[0].provenance,
                claims[0].field,
                (forged, *items[1:]),
            ),
        )
        with self.assertRaises(GroundingError):
            self.validator.validate(tampered, self.rows, self.payload)

    def test_unverified_fact_is_rejected(self) -> None:
        rows = [dict(item) for item in self.rows]
        rows[0]["fact_status"] = "REVIEW_REQUIRED"
        claims = self.builder.build(self.rows, self.payload)
        with self.assertRaises(GroundingError) as caught:
            self.validator.validate(claims, rows, self.payload)
        self.assertEqual(caught.exception.code, "ANSWER_FACT_NOT_VERIFIED")

    def test_unverified_evidence_is_rejected(self) -> None:
        rows = [dict(item) for item in self.rows]
        rows[0]["evidence_verification_status"] = "REVIEW_REQUIRED"
        claims = self.builder.build(self.rows, self.payload)
        with self.assertRaises(GroundingError) as caught:
            self.validator.validate(claims, rows, self.payload)
        self.assertEqual(caught.exception.code, "ANSWER_EVIDENCE_NOT_VERIFIED")

    def test_row_without_the_mandatory_field_cannot_be_answered(self) -> None:
        rows = [
            row("CareerField", "career:1", 1, field_order=1),
        ]
        payload = plan_payload("CAREER_FIELD_LIST", ["field_order"])
        with self.assertRaises(GroundingError) as caught:
            self.builder.build(rows, payload)
        self.assertEqual(caught.exception.code, "ANSWER_RENDERING_UNSUPPORTED")

    def test_mismatched_selection_mode_is_rejected(self) -> None:
        payload = plan_payload("TALENT_PROFILE_LIST", ["name_ko", "field_order"])
        with self.assertRaises(GroundingError):
            self.builder.build(self.rows, payload)

    def test_mixed_fact_labels_are_rejected(self) -> None:
        rows = [
            *narrative_rows(),
            row("TalentProfile", "talent:1", 3, description_ko="인재", profile_order=1),
        ]
        with self.assertRaises(GroundingError) as caught:
            self.builder.build(rows, self.payload)
        self.assertEqual(caught.exception.code, "ANSWER_RENDERING_UNSUPPORTED")

    def test_citations_cover_every_used_fact(self) -> None:
        claims = self.builder.build(self.rows, self.payload)
        validated = self.validator.validate(claims, self.rows, self.payload)
        answer = KoreanAnswerRenderer().render(validated)
        response = CitationRenderer().render("request-1", answer)
        self.assertEqual(
            set(response.used_fact_ids), {item["fact_id"] for item in self.rows}
        )
        self.assertTrue(response.citations)
        for citation in response.citations:
            self.assertTrue(citation.source_text.strip())


class ExtendedServiceTests(unittest.TestCase):
    def test_service_answers_an_extended_family_result(self) -> None:
        from kg_builder.query.natural_language_service import NaturalLanguageResult

        rows = tuple(narrative_rows())
        payload = plan_payload("CAREER_FIELD_LIST", ["name_ko", "field_order"])

        class StubQueryService:
            def ask(self, question: str) -> NaturalLanguageResult:
                del question
                return NaturalLanguageResult(
                    request_id="request-1",
                    status="ANSWERABLE",
                    model="stub",
                    elapsed_seconds=0.0,
                    query_plan=payload,
                    rows=rows,
                    evidence_count=len(rows),
                )

        response = CurriculumChatService(StubQueryService()).ask("진출 분야는?")
        self.assertEqual(response.status.value, "ANSWERABLE")
        self.assertIn("졸업 후 진출 분야는", response.answer_text)
        self.assertTrue(response.citations)


if __name__ == "__main__":
    unittest.main()
