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
from kg_builder.query.cypher_validator import CypherValidationError, CypherValidator
from kg_builder.llm.models import (
    GraduationQuestionClass,
    PlanningOutcome,
    PlanningStatus,
    UnsupportedReason,
)
from kg_builder.llm.planner import LocalQueryPlanner, classify_graduation_question
from kg_builder.query.natural_language_service import NaturalLanguageQueryService
from kg_builder.query.query_plan import QueryPlan, SelectionMode
from kg_builder.query.safety_pipeline import SafetyPipeline
from kg_builder.query.schema_catalog import SchemaCatalog
from kg_builder.query.schema_selector import QuerySchemaSelector
from kg_builder.query.progress import ProgressPhase, ProgressState

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
    def test_colloquial_when_requests_grade_and_semester(self):
        # The course identity comes from the Verified bundle.  The model payload is
        # unused because the deterministic course-slot path can safely normalize this
        # general Korean wording without supplying an answer value.
        outcome = LocalQueryPlanner(SequenceClient([])).plan("자료구조는 언제 들어?")
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(outcome.plan.selection_mode, SelectionMode.SINGLE_COURSE)
        self.assertEqual(
            set(outcome.plan.requested_fields),
            {"grade_year", "semester"},
        )

    def test_single_course_aspect_keeps_only_requested_fact_fields(self):
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "CDA0008을 안 들으면 졸업 못 해?"
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertNotIn("name_ko", outcome.plan.requested_fields)
        self.assertIn("completion_type", outcome.plan.requested_fields)

    def test_zero_credit_required_list_uses_general_filters(self):
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "0학점인 전공필수도 빠짐없이 알려 줘."
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(outcome.plan.selection_mode, SelectionMode.COURSE_LIST)
        self.assertEqual(outcome.plan.filters["completion_type"], "MAJOR_REQUIRED")
        self.assertEqual(outcome.plan.filters["credits"], 0)

    def test_topic_only_required_course_question_selects_required_list(self):
        outcome = LocalQueryPlanner(SequenceClient([])).plan("전공필수는?")
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(outcome.plan.selection_mode, SelectionMode.COURSE_LIST)
        self.assertEqual(outcome.plan.filters["completion_type"], "MAJOR_REQUIRED")

    def test_generic_department_all_course_names_is_a_complete_course_list(self):
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "컴공과 과목 이름을 전부 보여 줘."
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(outcome.plan.selection_mode, SelectionMode.COURSE_LIST)
        self.assertEqual(outcome.plan.filters["academic_year"], 2026)
        self.assertEqual(
            outcome.plan.filters["department_id"], "department:cwnu:cse"
        )
        self.assertNotIn("completion_type", outcome.plan.filters)
        self.assertIn("name_ko", outcome.plan.requested_fields)

    def test_multiple_course_classifications_do_not_collapse_to_one_filter(self):
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "컴공과 전공필수와 전공선택 과목 이름을 모두 알려 줘."
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(outcome.plan.selection_mode, SelectionMode.COURSE_LIST)
        self.assertNotIn("completion_type", outcome.plan.filters)
        self.assertIn("completion_type", outcome.plan.requested_fields)

    def test_area_course_list_uses_data_derived_descendant_area_scope(self):
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "균형교양 과목명을 전부 출력해 줘."
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(outcome.plan.selection_mode, SelectionMode.COURSE_LIST)
        self.assertEqual(len(outcome.plan.filters["area_ids"]), 4)
        self.assertNotIn("department_id", outcome.plan.filters)
        subset = QuerySchemaSelector().select(outcome.plan)
        scaffold = build_syntax_scaffold(outcome.plan, subset)
        self.assertIn("MATCH (o)-[:IN_AREA]->(a:EducationArea)", scaffold)
        self.assertIn("a.area_id IN $area_ids", scaffold)

    def test_required_course_list_with_requested_credits_is_deterministic(self):
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "전공필수 목록과 각 과목의 학점을 한꺼번에 알려 줘."
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(outcome.plan.selection_mode, SelectionMode.COURSE_LIST)
        self.assertEqual(outcome.plan.filters["completion_type"], "MAJOR_REQUIRED")
        self.assertIn("credits", outcome.plan.requested_fields)

    def test_general_graduation_and_major_credit_criteria_resolve_from_rule_index(self):
        questions = (
            "컴퓨터공학과 졸업학점 최소 기준을 확인해 줘.",
            "전공 학점 합계 기준은 몇 학점 이상이야?",
        )
        for question in questions:
            with self.subTest(question=question):
                outcome = LocalQueryPlanner(SequenceClient([])).plan(question)
                self.assertEqual(outcome.status, PlanningStatus.READY)
                self.assertIn(
                    outcome.plan.selection_mode,
                    {SelectionMode.SINGLE_RULE, SelectionMode.MULTIPLE_RULES},
                )

    def test_all_english_exam_thresholds_select_verified_atomic_rule_family(self):
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "대학영어 대체 공인시험별 최소 기준을 전부 확인해 줘."
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(outcome.plan.selection_mode, SelectionMode.MULTIPLE_RULES)
        self.assertGreaterEqual(len(outcome.plan.filters["rule_ids"]), 2)
        self.assertTrue(outcome.plan.evidence_required)

    def test_general_english_standard_selects_verified_atomic_rule_family(self):
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "컴퓨터공학과 학생이 졸업하려면 필요한 영어 기준을 알려 줘."
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(outcome.plan.selection_mode, SelectionMode.MULTIPLE_RULES)
        self.assertGreaterEqual(len(outcome.plan.filters["rule_ids"]), 2)
        self.assertTrue(outcome.plan.evidence_required)

    def test_general_course_list_language_is_deterministic(self):
        for question in (
            "2026 컴공 3학년 과목을 한 번에 정리해 줘.",
            "전공선택 과목을 빠짐없이 알려 주세요.",
            "컴공 수업 뭐 있는지 이름만 몽땅 보여 줘.",
        ):
            with self.subTest(question=question):
                outcome = LocalQueryPlanner(SequenceClient([])).plan(question)
                self.assertEqual(outcome.status, PlanningStatus.READY)
                self.assertEqual(
                    outcome.plan.selection_mode, SelectionMode.COURSE_LIST
                )

    def test_course_advice_retrieves_verified_course_details_before_recommending(self):
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "웹 개발 진로라면 웹프로그래밍 과목 정보를 바탕으로 조언해 줘."
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(outcome.plan.selection_mode, SelectionMode.SINGLE_COURSE)
        self.assertTrue(
            {"name_ko", "grade_year", "semester", "credits", "completion_type"}
            .issubset(outcome.plan.requested_fields)
        )

    def test_open_ended_career_sequence_uses_verified_recommendation_family(self):
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "AI 개발자가 목표인데 교육과정상 어떤 과목 순서로 살펴보면 좋을까?"
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(
            outcome.plan.selection_mode,
            SelectionMode.COURSE_RECOMMENDATION_LIST,
        )

    def test_open_career_choices_use_verified_career_field_family(self):
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "진로 선택지와 진출 분야를 비교해 줘."
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(outcome.plan.selection_mode, SelectionMode.CAREER_FIELD_LIST)

    def test_combined_requirement_and_timing_keeps_all_requested_fields(self):
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "자료구조를 안 들으면 안 되는지, 그리고 언제 편성됐는지 같이 알려 줘."
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertTrue(
            {"grade_year", "semester", "completion_type"}
            .issubset(outcome.plan.requested_fields)
        )

    def test_multi_course_bare_credit_noun_is_a_requested_field(self):
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "알고리즘하고 운영체제의 학점과 이수구분을 같이 보고 싶어."
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(outcome.plan.selection_mode, SelectionMode.COURSE_LIST)
        self.assertTrue(
            {"credits", "completion_type", "name_ko"}.issubset(
                outcome.plan.requested_fields
            )
        )

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
            # 적재된 사실을 가리키는 질문이어야 계획 모델까지 간다. 아무것과도 겹치지
            # 않는 입력은 계획 단계 앞에서 범위 밖으로 끝난다.
            outcome = LocalQueryPlanner(SequenceClient([payload])).plan("교양 학점은?")
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
        client = SequenceClient([ambiguous_course])
        outcome = LocalQueryPlanner(client).plan("동명과목은?")
        self.assertEqual(outcome.status, PlanningStatus.CLARIFICATION_REQUIRED)
        self.assertIsNone(outcome.plan)
        self.assertEqual(len(client.prompts), 1)

        ready_course = dict(ambiguous_course)
        ready_course["status"] = "READY"
        ready_course["message"] = None
        client = SequenceClient([ambiguous_course, ready_course])
        outcome = LocalQueryPlanner(client).plan("동명과목은 몇 학년 몇 학기?")
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertIsNotNone(outcome.plan)
        self.assertEqual(outcome.plan.selection_mode, SelectionMode.SINGLE_COURSE)

    def test_poc_defaults_fill_only_omitted_course_scope_and_keep_requested_fields(self):
        payload = {
            "status": "READY",
            "intent": "course offering",
            "filters": {"name_ko": "자료구조"},
            "requested_fields": ["grade_year", "semester"],
            "evidence_required": True,
            "message": None,
            "selection_mode": "SINGLE_COURSE",
        }
        outcome = LocalQueryPlanner(SequenceClient([payload])).plan("개설 학년과 학기")
        self.assertEqual(outcome.plan.filters["academic_year"], 2026)
        self.assertEqual(
            outcome.plan.filters["department_id"], "department:cwnu:cse"
        )
        self.assertNotIn("grade_year", outcome.plan.filters)
        self.assertNotIn("semester", outcome.plan.filters)
        self.assertEqual(outcome.plan.requested_fields, ("grade_year", "semester"))

    def test_interrogative_course_fields_are_outputs_not_search_filters(self):
        payload = {
            "status": "READY",
            "intent": "course offering",
            "filters": {
                "name_ko": "자료구조",
                "grade_year": 2,
                "semester": "FIRST",
            },
            "requested_fields": ["grade_year", "semester"],
            "evidence_required": True,
            "message": None,
            "selection_mode": "SINGLE_COURSE",
        }
        outcome = LocalQueryPlanner(SequenceClient([payload])).plan(
            "자료구조는 몇 학년 몇 학기에 개설되나?"
        )
        self.assertNotIn("grade_year", outcome.plan.filters)
        self.assertNotIn("semester", outcome.plan.filters)
        self.assertEqual(outcome.plan.requested_fields, ("grade_year", "semester"))

    def test_course_code_question_uses_requested_field_not_filter(self):
        payload = {
            "status": "READY",
            "intent": "course identity",
            "filters": {"name_ko": "이산수학", "course_code": "UNKNOWN"},
            "requested_fields": ["name_ko"],
            "evidence_required": True,
            "message": None,
            "selection_mode": "SINGLE_COURSE",
        }
        outcome = LocalQueryPlanner(SequenceClient([payload])).plan(
            "이산수학의 과목코드가 뭐야"
        )
        self.assertEqual(outcome.plan.filters["name_ko"], "이산수학")
        self.assertNotIn("course_code", outcome.plan.filters)
        self.assertIn("course_code", outcome.plan.requested_fields)

    def test_ready_course_plan_enforces_evidence_when_model_flag_is_false(self):
        payload = {
            "status": "CLARIFICATION_REQUIRED",
            "intent": "course identity",
            "filters": {"name_ko": "이산수학"},
            "requested_fields": ["course_code"],
            "evidence_required": False,
            "message": "Please provide more information.",
            "selection_mode": "SINGLE_COURSE",
        }
        outcome = LocalQueryPlanner(SequenceClient([payload])).plan(
            "이산수학의 과목코드가 뭐야"
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertTrue(outcome.plan.evidence_required)
        self.assertEqual(outcome.plan.filters["name_ko"], "이산수학")
        self.assertEqual(outcome.plan.requested_fields, ("course_code",))

    def test_model_english_clarification_is_replaced_with_safe_korean_template(self):
        payload = {
            "status": "CLARIFICATION_REQUIRED",
            "intent": "course",
            "filters": {},
            "requested_fields": ["semester"],
            "evidence_required": True,
            "message": "Please provide more information.",
            "selection_mode": "SINGLE_COURSE",
        }
        outcome = LocalQueryPlanner(SequenceClient([payload])).plan("어느 과목인가요?")
        self.assertEqual(outcome.status, PlanningStatus.CLARIFICATION_REQUIRED)
        self.assertEqual(outcome.message, "과목명 또는 학수번호를 입력해 주세요.")
        self.assertNotIn("Please", outcome.message)

    def test_explicit_unsupported_scope_is_not_overwritten_by_defaults(self):
        payload = {
            "status": "READY",
            "intent": "course offering",
            "filters": {
                "academic_year": 2025,
                "department_id": "department:other",
                "name_ko": "자료구조",
            },
            "requested_fields": ["semester"],
            "evidence_required": True,
            "message": None,
            "selection_mode": "SINGLE_COURSE",
        }
        outcome = LocalQueryPlanner(SequenceClient([payload])).plan("다른 범위 질문")
        self.assertEqual(outcome.status, PlanningStatus.OUT_OF_SCOPE)
        self.assertIsNone(outcome.plan)

    def test_personal_history_graduation_judgment_is_deterministically_unsupported(self):
        client = SequenceClient([])
        outcome = LocalQueryPlanner(client).plan(
            "데이터베이스개론을 들었는데 뭘 해야 졸업하려면?"
        )
        self.assertEqual(outcome.status, PlanningStatus.UNSUPPORTED)
        self.assertEqual(
            outcome.unsupported_reason, UnsupportedReason.PERSONAL_HISTORY
        )
        self.assertFalse(client.prompts)

    def test_general_graduation_rule_with_pronouns_uses_verified_atomic_rule(self):
        question = (
            "컴공과 학생인데 내가 졸업하고 싶은데 졸업하기 위해서 영어 대체로 "
            "토익 점수를 얼마나 받아야 할까? 최소 기준점이 있어?"
        )
        client = SequenceClient([])
        outcome = LocalQueryPlanner(client).plan(question)
        self.assertEqual(
            classify_graduation_question(question),
            GraduationQuestionClass.GENERAL_RULE,
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertIsNotNone(outcome.plan)
        self.assertTrue(outcome.plan.evidence_required)
        self.assertEqual(outcome.plan.selection_mode.value, "SINGLE_RULE")
        self.assertEqual(len(outcome.plan.filters["rule_ids"]), 1)
        self.assertFalse(client.prompts)
        context = LocalQueryPlanner(SequenceClient([])).context
        serialized_review = str(context["review_required_rule_identifiers"])
        self.assertNotIn("TOEIC.score", serialized_review)
        self.assertNotIn("700", serialized_review)

    def test_single_condition_comparison_uses_the_same_verified_rule_path(self):
        question = "토익 700점이면 영어 대체 기준을 충족해?"
        client = SequenceClient([])
        outcome = LocalQueryPlanner(client).plan(question)
        self.assertEqual(
            classify_graduation_question(question),
            GraduationQuestionClass.SINGLE_CONDITION_COMPARISON,
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertIsNotNone(outcome.plan)
        self.assertTrue(outcome.plan.evidence_required)
        self.assertEqual(len(outcome.plan.filters["rule_ids"]), 1)
        self.assertFalse(client.prompts)

    def test_full_personal_history_remains_unsupported_without_llm(self):
        question = "내가 지금까지 들은 과목과 학점으로 졸업할 수 있어?"
        client = SequenceClient([])
        outcome = LocalQueryPlanner(client).plan(question)
        self.assertEqual(
            classify_graduation_question(question),
            GraduationQuestionClass.FULL_PERSONAL_HISTORY,
        )
        self.assertEqual(outcome.status, PlanningStatus.UNSUPPORTED)
        self.assertEqual(
            outcome.unsupported_reason, UnsupportedReason.PERSONAL_HISTORY
        )
        self.assertFalse(client.prompts)

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

    def test_course_list_scaffold_uses_bounded_complete_catalog_limit(self) -> None:
        payload = plan_payload()
        payload["selection_mode"] = "COURSE_LIST"
        payload["filters"] = {
            "academic_year": 2026,
            "area_ids": [
                "area:general:balanced:digital-communication",
                "area:general:balanced:humanities-arts",
            ],
        }
        payload["requested_fields"] = ["name_ko", "completion_type"]
        plan = QueryPlan.from_dict(payload, SchemaCatalog.from_generated())
        scaffold = build_syntax_scaffold(plan, QuerySchemaSelector().select(plan))

        validated = CypherValidator(SchemaCatalog.from_generated()).validate(plan, scaffold)
        self.assertEqual(validated.limit, 250)
        self.assertIn("c.course_id AS course_identity", scaffold)
        self.assertIn("a.name_ko AS area_name", scaffold)

        with self.assertRaises(CypherValidationError) as raised:
            CypherValidator(SchemaCatalog.from_generated()).validate(
                plan, scaffold.replace("LIMIT 250", "LIMIT 100")
            )
        self.assertEqual(raised.exception.code, "CYPHER_LIST_LIMIT_INCOMPLETE")

    def test_single_course_code_lookup_adds_grounded_subject_without_answer_slot(self) -> None:
        payload = plan_payload()
        payload["selection_mode"] = "SINGLE_COURSE"
        payload["filters"] = {
            "academic_year": 2026,
            "department_id": "department:cwnu:cse",
            "course_code": "CDA0008",
        }
        payload["requested_fields"] = ["completion_type"]
        plan = QueryPlan.from_dict(payload, SchemaCatalog.from_generated())

        self.assertEqual(plan.requested_fields, ("completion_type",))
        scaffold = build_syntax_scaffold(plan, QuerySchemaSelector().select(plan))
        validated = CypherValidator(SchemaCatalog.from_generated()).validate(
            plan, scaffold
        )

        self.assertIn("c.name_ko AS name_ko", scaffold)
        self.assertIn("c.course_id AS course_identity", scaffold)
        self.assertEqual(validated.provenance.fact_label, "CourseOffering")

        missing_subject = scaffold.replace(",\n       c.name_ko AS name_ko", "")
        with self.assertRaises(CypherValidationError) as raised:
            CypherValidator(SchemaCatalog.from_generated()).validate(
                plan, missing_subject
            )
        self.assertEqual(raised.exception.code, "CYPHER_RETURN_FIELD_MISMATCH")

    def test_unique_common_course_identity_uses_common_curriculum_path(self) -> None:
        client = SequenceClient([])
        outcome = LocalQueryPlanner(client).plan(
            "컴퓨터프로그래밍의 이수구분을 알려줘"
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertNotIn("department_id", outcome.plan.filters)
        subset = QuerySchemaSelector().select(outcome.plan)
        scaffold = build_syntax_scaffold(outcome.plan, subset)
        validated = CypherValidator(SchemaCatalog.from_generated()).validate(
            outcome.plan, scaffold
        )
        self.assertEqual(validated.provenance.fact_label, "CourseOffering")
        self.assertNotIn("FOR_DEPARTMENT", scaffold)
        self.assertIn("MATCH (cv:CurriculumVersion)", scaffold)
        self.assertFalse(client.prompts)

    def test_multiple_data_derived_spelling_variants_resolve_to_stable_codes(self) -> None:
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "운영체제와 데이터통신의 이수구분을 비교해줘"
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(outcome.plan.selection_mode, SelectionMode.COURSE_LIST)
        self.assertEqual(len(outcome.plan.filters["course_codes"]), 2)

    def test_multi_course_code_request_keeps_the_requested_identity_field(self) -> None:
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "자료구조하고 이산수학 학수번호를 각각 알려줘"
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(outcome.plan.selection_mode, SelectionMode.COURSE_LIST)
        self.assertEqual(len(outcome.plan.filters["course_codes"]), 2)
        self.assertIn("course_code", outcome.plan.requested_fields)

    def test_major_required_list_preserves_explicit_grade_and_semester_scope(self) -> None:
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "3학년 1학기 전공필수 과목 중 우선순위를 알려줘"
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(outcome.plan.filters["completion_type"], "MAJOR_REQUIRED")
        self.assertEqual(outcome.plan.filters["grade_year"], 3)
        self.assertEqual(outcome.plan.filters["semester"], "FIRST")

    def test_major_required_list_accepts_reversed_controlled_term_order(self) -> None:
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "컴퓨터공학과 필수 전공은 총 몇 과목이고 몇 학점이야?"
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertEqual(outcome.plan.selection_mode, SelectionMode.COURSE_LIST)
        self.assertEqual(outcome.plan.filters["completion_type"], "MAJOR_REQUIRED")

    def test_minimum_credit_rule_accepts_natural_lower_bound_synonym(self) -> None:
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "컴공 2026 교양은 적어도 몇 학점이어야 하나요?"
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertTrue(any(
            "min-total-default" in value
            for value in outcome.plan.filters["rule_ids"]
        ))

    def test_balanced_general_rule_does_not_need_a_model_for_same_area_question(self) -> None:
        client = SequenceClient([])
        outcome = LocalQueryPlanner(client).plan(
            "균형교양을 같은 영역에서만 12학점 들으면 되는 거지?"
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        self.assertGreaterEqual(len(outcome.plan.filters["rule_ids"]), 2)
        self.assertFalse(client.prompts)

    def test_compact_credit_deficit_question_selects_general_and_major_rules(self) -> None:
        outcome = LocalQueryPlanner(SequenceClient([])).plan(
            "전공 51, 교양 29, 일선 14인데 영역별로 얼마나 부족해?"
        )
        self.assertEqual(outcome.status, PlanningStatus.READY)
        rule_ids = set(outcome.plan.filters["rule_ids"])
        self.assertTrue(any("min-total-default" in item for item in rule_ids))
        self.assertTrue(any("major-total" in item for item in rule_ids))

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

    def test_progress_reports_only_actual_pipeline_milestones(self) -> None:
        generator = SequenceGenerator([SAFE_QUERY])
        events = []
        with tempfile.TemporaryDirectory() as directory:
            result = self.service(generator, directory).ask(
                self.plan.question, progress_callback=events.append
            )
        self.assertEqual(result.status, "ANSWERABLE")
        completed = [
            event.phase
            for event in events
            if event.state is ProgressState.COMPLETED
        ]
        self.assertEqual(
            completed,
            [
                ProgressPhase.QUESTION_ANALYSIS,
                ProgressPhase.SCHEMA_SELECTION,
                ProgressPhase.CYPHER_GENERATION,
                ProgressPhase.STATIC_VALIDATION,
                ProgressPhase.NEO4J_EXPLAIN,
                ProgressPhase.GRAPH_EXECUTION,
                ProgressPhase.RESULT_VALIDATION,
            ],
        )
        static = next(
            event
            for event in events
            if event.phase is ProgressPhase.STATIC_VALIDATION
            and event.state is ProgressState.COMPLETED
        )
        self.assertEqual(static.details["validated_cypher"].strip(), SAFE_QUERY.strip())

    def test_resolved_and_progress_are_keyword_only(self) -> None:
        generator = SequenceGenerator([SAFE_QUERY])
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(generator, directory)
            with self.assertRaises(TypeError):
                service.ask(self.plan.question, lambda event: None)

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
