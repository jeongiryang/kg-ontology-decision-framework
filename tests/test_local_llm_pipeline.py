from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from kg_builder.llm.client import LLMConfigurationError, LLMGeneration, LocalLLMSettings
from kg_builder.llm.cypher_generator import LocalCypherGenerator, build_syntax_scaffold
from kg_builder.query.cypher_validator import CypherValidator
from kg_builder.llm.models import PlanningOutcome, PlanningStatus
from kg_builder.llm.planner import LocalQueryPlanner
from kg_builder.query.natural_language_service import NaturalLanguageQueryService
from kg_builder.query.query_plan import QueryPlan
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
    def test_settings_are_local_only(self) -> None:
        with self.assertRaises(LLMConfigurationError):
            LocalLLMSettings("https://cloud.example", "model").validate()
        LocalLLMSettings("http://127.0.0.1:11434", "model").validate()

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

    def test_generator_returns_only_candidate_cypher(self) -> None:
        plan = QueryPlan.from_dict(plan_payload(), SchemaCatalog.from_generated())
        client = SequenceClient([{"cypher": SAFE_QUERY}])
        result = LocalCypherGenerator(client).generate(
            plan, QuerySchemaSelector().select(plan)
        )
        self.assertEqual(result.strip(), SAFE_QUERY.strip())
        self.assertNotIn(plan.question, client.prompts[0])

    def test_generated_course_scaffold_passes_the_existing_validator(self) -> None:
        plan = QueryPlan.from_dict(plan_payload(), SchemaCatalog.from_generated())
        subset = QuerySchemaSelector().select(plan)
        scaffold = build_syntax_scaffold(plan, subset)
        validated = CypherValidator(SchemaCatalog.from_generated()).validate(plan, scaffold)
        self.assertEqual(validated.provenance.fact_label, "CourseOffering")

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

    def test_second_unsafe_candidate_stops_without_direct_execution(self) -> None:
        bad = SAFE_QUERY.replace("MATCH", "CREATE", 1)
        generator = SequenceGenerator([bad, bad])
        with tempfile.TemporaryDirectory() as directory:
            result = self.service(generator, directory).ask(self.plan.question)
        self.assertEqual(result.status, "SAFE_FAILURE")
        self.assertEqual(result.error_stage, "CYPHER_VALIDATION")

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


if __name__ == "__main__":
    unittest.main()
