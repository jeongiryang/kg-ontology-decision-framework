from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from kg_builder.llm.client import (
    LLMConfigurationError,
    LLMGeneration,
    LLMProvider,
    LLMResponseError,
    LLMSettings,
)
from kg_builder.llm.cypher_generator import LocalCypherGenerator, build_syntax_scaffold
from kg_builder.query.cypher_validator import CypherValidator
from kg_builder.llm.models import PlanningOutcome, PlanningStatus
from kg_builder.llm.planner import LocalQueryPlanner
from kg_builder.query.natural_language_service import NaturalLanguageQueryService
from kg_builder.query.query_plan import QueryPlan, SelectionMode
from kg_builder.query.safety_pipeline import SafetyPipeline
from kg_builder.query.schema_catalog import SchemaCatalog
from kg_builder.query.schema_selector import QuerySchemaSelector

from tests.test_dynamic_query_safety import (
    SAFE_QUERY,
    FakeExecutor,
    FakeExplainer,
    plan_payload,
    valid_row,
)


class SequenceClient:
    model = "fake-local-model"

    def __init__(self, payloads: list[dict[str, Any]]):
        self.payloads = list(payloads)
        self.prompts: list[str] = []

    def generate_json(self, *, system_prompt, user_prompt, response_schema):
        self.prompts.append(user_prompt)
        return LLMGeneration(self.payloads.pop(0), 0.01, self.model)


class RedirectFailingClient:
    model = "fake-local-model"

    def generate_json(self, *, system_prompt, user_prompt, response_schema):
        del system_prompt, user_prompt, response_schema
        raise LLMResponseError(
            "LLM_HTTP_REDIRECT_REJECTED",
            "ollama rejected an HTTP 3xx redirect; the request was not retried",
        )


def ready_planner_payload() -> dict[str, Any]:
    return {
        "status": "READY",
        "intent": "search_course_offerings",
        "filters": plan_payload()["filters"],
        "requested_fields": plan_payload()["requested_fields"],
        "evidence_required": True,
        "message": None,
        "selection_mode": "COURSE_LIST",
    }


class LocalLLMContractTests(unittest.TestCase):
    def test_numeric_rule_plan_is_enriched_with_semantic_claim_fields(self) -> None:
        payload = {
            "status": "READY",
            "intent": "minimum",
            "filters": {
                "academic_year": 2026,
                "rule_ids": ["rule:cwnu:2026:general:min-total-default"],
            },
            "requested_fields": ["value"],
            "evidence_required": True,
            "selection_mode": "SINGLE_RULE",
            "message": None,
        }
        outcome = LocalQueryPlanner(SequenceClient([payload])).plan("최소학점은?")
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(
            set(outcome.plan.requested_fields),
            {"value", "rule_type", "operator", "unit", "description_ko"},
        )

    def test_settings_are_local_only(self) -> None:
        with self.assertRaises(LLMConfigurationError):
            LLMSettings(
                LLMProvider.OLLAMA, "https://cloud.example", "model"
            ).validate()
        LLMSettings(
            LLMProvider.OLLAMA, "http://127.0.0.1:11434", "model"
        ).validate()

    def test_planner_validates_and_retries_one_bad_contract(self) -> None:
        bad = ready_planner_payload()
        bad["filters"] = {**bad["filters"], "semester": "THIRD"}
        client = SequenceClient([bad, ready_planner_payload()])
        outcome = LocalQueryPlanner(client).plan(plan_payload()["question"])
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(outcome.plan.filters["semester"], "SECOND")
        self.assertEqual(len(client.prompts), 2)
        self.assertIn("previous_contract_error", client.prompts[1])

    def test_planner_ambiguity_and_scope_statuses_do_not_create_plan(self) -> None:
        for status in ("CLARIFICATION_REQUIRED", "OUT_OF_SCOPE", "UNSUPPORTED"):
            payload = {
                "status": status,
                "intent": None,
                "filters": {},
                "requested_fields": [],
                "evidence_required": True,
                "message": "safe stop",
                "selection_mode": "COURSE_LIST",
            }
            outcome = LocalQueryPlanner(SequenceClient([payload])).plan("질문")
            self.assertEqual(outcome.status.value, status)
            self.assertIsNone(outcome.plan)

        ambiguous_course = {
            "status": "CLARIFICATION_REQUIRED",
            "intent": "course lookup",
            "filters": {
                "academic_year": 2026,
                "department_id": "department:cwnu:cse",
                "name_ko": "동명과목",
            },
            "requested_fields": ["grade_year", "semester"],
            "evidence_required": True,
            "message": "학수번호를 지정하세요",
            "selection_mode": "SINGLE_COURSE",
        }
        client = SequenceClient([ambiguous_course, ambiguous_course])
        outcome = LocalQueryPlanner(client).plan("동명과목은?")
        self.assertEqual(outcome.status, PlanningStatus.CLARIFICATION_REQUIRED)
        self.assertIsNone(outcome.plan)
        self.assertEqual(len(client.prompts), 2)

        ready_course = dict(ambiguous_course)
        ready_course["status"] = "READY"
        ready_course["message"] = None
        client = SequenceClient([ambiguous_course, ready_course])
        outcome = LocalQueryPlanner(client).plan("동명과목은?")
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertIsNotNone(outcome.plan)
        self.assertEqual(outcome.plan.selection_mode, SelectionMode.SINGLE_COURSE)

    def test_generator_returns_only_candidate_cypher(self) -> None:
        plan = QueryPlan.from_dict(plan_payload(), SchemaCatalog.from_generated())
        client = SequenceClient([{"cypher": SAFE_QUERY}])
        result = LocalCypherGenerator(client).generate(
            plan, QuerySchemaSelector().select(plan)
        )
        self.assertEqual(result.strip(), SAFE_QUERY.strip())
        self.assertNotIn(plan.question, client.prompts[0])

    def test_generated_course_scaffold_passes_the_existing_validator(self) -> None:
        payload = plan_payload()
        payload["selection_mode"] = "SINGLE_COURSE"
        payload["filters"] = {
            "academic_year": 2026,
            "department_id": "department:cwnu:cse",
            "name_ko": "자료구조",
        }
        payload["requested_fields"] = ["grade_year", "semester"]
        plan = QueryPlan.from_dict(payload, SchemaCatalog.from_generated())
        subset = QuerySchemaSelector().select(plan)
        scaffold = build_syntax_scaffold(plan, subset)
        validated = CypherValidator(SchemaCatalog.from_generated()).validate(plan, scaffold)
        self.assertEqual(validated.provenance.fact_label, "CourseOffering")
        self.assertIn("c.course_id AS course_identity", scaffold)

    def test_generated_multi_rule_scaffold_passes_the_existing_validator(self) -> None:
        payload = {
            "question": "2026학년도 균형교양 이수요건은?",
            "filters": {
                "academic_year": 2026,
                "rule_ids": [
                    "rule:cwnu:2026:general:balanced-min-credits",
                    "rule:cwnu:2026:general:balanced-each-area-one",
                ],
            },
            "requested_fields": ["rule_type", "operator", "value", "unit", "description_ko"],
            "evidence_required": True,
            "intent": "rule lookup",
        }
        catalog = SchemaCatalog.from_generated()
        plan = QueryPlan.from_dict(payload, catalog)
        scaffold = build_syntax_scaffold(plan, QuerySchemaSelector().select(plan))
        validated = CypherValidator(catalog).validate(plan, scaffold)
        self.assertEqual(validated.provenance.fact_label, "Rule")
        self.assertIn("r.rule_id IN $rule_ids", scaffold)

    def test_schema_selector_is_ontology_derived_and_not_full_schema(self) -> None:
        plan = QueryPlan.from_dict(plan_payload(), SchemaCatalog.from_generated())
        selected = QuerySchemaSelector().select(plan)
        labels = {item["label"] for item in selected["nodes"]}
        self.assertIn("CourseOffering", labels)
        self.assertIn("Evidence", labels)
        self.assertLess(len(labels), 26)
        self.assertEqual(selected["source"]["schema_version"], "0.2.0")


class StubPlanner:
    def __init__(self, outcome: PlanningOutcome):
        self.outcome = outcome

    def plan(self, question: str) -> PlanningOutcome:
        return self.outcome


class SequenceGenerator:
    def __init__(self, candidates: list[str]):
        self.candidates = list(candidates)
        self.errors: list[str | None] = []

    def generate(self, plan, schema_subset, *, previous_error_code=None) -> str:
        self.errors.append(previous_error_code)
        return self.candidates.pop(0)


class NaturalLanguageServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = QueryPlan.from_dict(plan_payload(), SchemaCatalog.from_generated())

    def service(self, generator, directory: str, rows=None) -> NaturalLanguageQueryService:
        pipeline = SafetyPipeline(
            FakeExplainer(),
            FakeExecutor(rows if rows is not None else [valid_row()]),
            trace_dir=Path(directory),
        )
        return NaturalLanguageQueryService(
            StubPlanner(PlanningOutcome(PlanningStatus.READY, self.plan)),
            generator,
            pipeline,
            QuerySchemaSelector(),
            model="fake-local-model",
        )

    def test_safe_pipeline_is_mandatory_and_one_retry_can_recover(self) -> None:
        generator = SequenceGenerator([SAFE_QUERY.replace("MATCH", "CREATE", 1), SAFE_QUERY])
        with tempfile.TemporaryDirectory() as directory:
            result = self.service(generator, directory).ask(self.plan.question)
        self.assertEqual(result.status, "ANSWERABLE")
        self.assertEqual(result.evidence_count, 1)
        self.assertEqual(generator.errors[0], None)
        self.assertIsNotNone(generator.errors[1])
        self.assertNotIn("question", result.query_plan)

    def test_redirect_provider_failure_is_returned_as_safe_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = NaturalLanguageQueryService(
                LocalQueryPlanner(RedirectFailingClient()),
                SequenceGenerator([]),
                SafetyPipeline(
                    FakeExplainer(), FakeExecutor([]), trace_dir=Path(directory)
                ),
                QuerySchemaSelector(),
                model="fake-local-model",
            )
            result = service.ask("안전한 로컬 질문")
        self.assertEqual(result.status, "SAFE_FAILURE")
        self.assertEqual(result.error_stage, "PLANNING")
        self.assertEqual(result.error_code, "LLM_HTTP_REDIRECT_REJECTED")
        self.assertIsNone(result.cypher)

    def test_unsafe_candidates_never_execute_and_fall_back_to_the_scaffold(self) -> None:
        """모델이 쓴 위험한 Cypher 는 한 건도 실행되지 않아야 한다.

        재시도를 다 쓰면 계획에서 결정론적으로 만든 스캐폴드로 되돌아간다. 스캐폴드도
        검증기를 그대로 통과해야 하므로, 이 경로가 안전 관문을 건너뛰지 않는다. 여기서
        확인할 것은 "무엇이 실행됐는가"이지 상태 문자열이 아니다.
        """

        bad = SAFE_QUERY.replace("MATCH", "CREATE", 1)
        generator = SequenceGenerator([bad, bad])
        with tempfile.TemporaryDirectory() as directory:
            result = self.service(generator, directory).ask(self.plan.question)
        self.assertNotIn("CREATE", result.cypher or "")
        self.assertEqual(result.status, "ANSWERABLE")
        # 모델은 재시도 횟수만큼만 호출된다. 마지막 한 번은 모델을 부르지 않고
        # 스캐폴드를 쓰므로 준비한 후보가 모두 소진돼 있어야 한다.
        self.assertEqual(len(generator.errors), 2)
        self.assertEqual(generator.candidates, [])

    def test_empty_rows_are_not_found_and_ambiguous_plan_does_not_generate(self) -> None:
        generator = SequenceGenerator([SAFE_QUERY])
        with tempfile.TemporaryDirectory() as directory:
            result = self.service(generator, directory, rows=[]).ask(self.plan.question)
        self.assertEqual(result.status, "NOT_FOUND")
        self.assertEqual(result.evidence_count, 0)

        with tempfile.TemporaryDirectory() as directory:
            pipeline = SafetyPipeline(
                FakeExplainer(), FakeExecutor([]), trace_dir=Path(directory)
            )
            service = NaturalLanguageQueryService(
                StubPlanner(
                    PlanningOutcome(
                        PlanningStatus.CLARIFICATION_REQUIRED, message="학과를 지정하세요"
                    )
                ),
                SequenceGenerator([]),
                pipeline,
                QuerySchemaSelector(),
                model="fake-local-model",
            )
            result = service.ask("어느 과목?")
        self.assertEqual(result.status, "CLARIFICATION_REQUIRED")
        self.assertIsNone(result.cypher)

    def test_single_course_uses_stable_identity_for_ambiguity(self) -> None:
        payload = {
            "question": "2026학년도 컴퓨터공학과 동명과목은 언제 개설되나?",
            "filters": {
                "academic_year": 2026,
                "department_id": "department:cwnu:cse",
                "name_ko": "동명과목",
            },
            "requested_fields": ["grade_year", "semester"],
            "evidence_required": True,
            "intent": "course lookup",
            "selection_mode": SelectionMode.SINGLE_COURSE.value,
        }
        plan = QueryPlan.from_dict(payload, SchemaCatalog.from_generated())
        query = build_syntax_scaffold(plan, QuerySchemaSelector().select(plan))

        def row(identity: str, suffix: str) -> dict[str, Any]:
            return {
                "grade_year": [2],
                "semester": "FIRST",
                "academic_year": 2026,
                "department_id": "department:cwnu:cse",
                "name_ko": "동명과목",
                "fact_id": f"offering:{suffix}",
                "fact_label": "CourseOffering",
                "fact_status": "VERIFIED",
                "evidence_id": f"evidence:{suffix}",
                "excerpt_page": 17,
                "source_pdf_page": 262,
                "printed_page": 254,
                "source_text": "검증된 동명 과목 편성",
                "evidence_verification_status": "VERIFIED",
                "course_identity": identity,
            }

        with tempfile.TemporaryDirectory() as directory:
            service = NaturalLanguageQueryService(
                StubPlanner(PlanningOutcome(PlanningStatus.READY, plan)),
                SequenceGenerator([query]),
                SafetyPipeline(
                    FakeExplainer(),
                    FakeExecutor(
                        [
                            row("course:cwnu:ONE", "one"),
                            row("course:cwnu:TWO", "two"),
                        ]
                    ),
                    trace_dir=Path(directory),
                ),
                QuerySchemaSelector(),
                model="fake-local-model",
            )
            result = service.ask(plan.question)
        self.assertEqual(result.status, "CLARIFICATION_REQUIRED")
        self.assertEqual(result.error_code, "RESULT_COURSE_AMBIGUOUS")

        first_evidence = row("course:cwnu:ONE", "one-first")
        second_evidence = row("course:cwnu:ONE", "one-second")
        second_evidence["fact_id"] = first_evidence["fact_id"]
        same_identity_rows = [first_evidence, second_evidence]
        with tempfile.TemporaryDirectory() as directory:
            service = NaturalLanguageQueryService(
                StubPlanner(PlanningOutcome(PlanningStatus.READY, plan)),
                SequenceGenerator([query]),
                SafetyPipeline(
                    FakeExplainer(),
                    FakeExecutor(same_identity_rows),
                    trace_dir=Path(directory),
                ),
                QuerySchemaSelector(),
                model="fake-local-model",
            )
            result = service.ask(plan.question)
        self.assertEqual(result.status, "ANSWERABLE")
        self.assertEqual(len(result.rows), 2)

    def test_course_code_takes_precedence_over_name(self) -> None:
        payload = {
            "question": "학수번호가 있는 과목 조회",
            "filters": {
                "academic_year": 2026,
                "department_id": "department:cwnu:cse",
                "course_code": "CDA0008",
                "name_ko": "오래된 표시명",
            },
            "requested_fields": ["semester"],
            "evidence_required": True,
            "selection_mode": "SINGLE_COURSE",
        }
        plan = QueryPlan.from_dict(payload, SchemaCatalog.from_generated())
        self.assertEqual(plan.filters["course_code"], "CDA0008")
        self.assertNotIn("name_ko", plan.filters)


if __name__ == "__main__":
    unittest.main()
