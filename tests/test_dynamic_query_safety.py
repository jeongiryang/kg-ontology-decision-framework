from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kg_builder.query.cypher_validator import CypherValidationError, CypherValidator
from kg_builder.query.query_explainer import ExplainedCypher
from kg_builder.query.query_plan import QueryPlan, QueryPlanError
from kg_builder.query.query_trace import TraceStatus
from kg_builder.query.result_validator import ResultValidationError, ResultValidator
from kg_builder.query.safety_pipeline import SafetyPipeline, SafetyPipelineError
from kg_builder.query.schema_catalog import (
    DEFAULT_QUERY_SCHEMA_PATH,
    DEFAULT_SPEC_PATH,
    SchemaCatalog,
    SchemaCatalogError,
)
from kg_builder.query.schema_exporter import (
    build_query_schema,
    check_query_schema,
    render_query_schema,
    write_query_schema,
)


SAFE_QUERY = """
MATCH (cv:CurriculumVersion)-[:FOR_DEPARTMENT]->(d:Department)
MATCH (cv)-[:HAS_OFFERING]->(o:CourseOffering)-[:OF_COURSE]->(c:Course)
MATCH (o)-[:SUPPORTED_BY]->(e:Evidence)
WHERE cv.academic_year = $academic_year
  AND d.name_ko = $department_code
  AND $grade_year IN o.grade_year
  AND o.semester = $semester
  AND o.completion_type = $completion_type
  AND o.status = 'VERIFIED'
  AND e.verification_status = 'VERIFIED'
RETURN c.course_code AS course_code,
       c.name_ko AS name_ko,
       o.credits AS credits,
       cv.academic_year AS academic_year,
       d.name_ko AS department_code,
       o.grade_year AS grade_year,
       o.semester AS semester,
       o.completion_type AS completion_type,
       o.status AS fact_status,
       e.evidence_id AS evidence_id,
       e.excerpt_page AS excerpt_page,
       e.source_pdf_page AS source_pdf_page,
       e.printed_page AS printed_page,
       e.raw_text AS source_text,
       e.verification_status AS evidence_verification_status
LIMIT 100
"""


def plan_payload() -> dict[str, Any]:
    return {
        "question": "2026년 컴공 3학년 2학기 전공선택 과목은?",
        "filters": {
            "academic_year": 2026,
            "department_code": "컴퓨터공학과",
            "grade_year": 3,
            "semester": "SECOND",
            "completion_type": "MAJOR_ELECTIVE",
        },
        "requested_fields": ["course_code", "name_ko", "credits"],
        "evidence_required": True,
        "intent": "설명 및 추적용 선택 메타데이터",
    }


class SchemaExportTests(unittest.TestCase):
    def test_generation_is_deterministic_and_current(self) -> None:
        self.assertEqual(render_query_schema(), render_query_schema())
        self.assertTrue(check_query_schema())

    def test_generated_content_is_derived_from_source(self) -> None:
        spec = json.loads(DEFAULT_SPEC_PATH.read_text(encoding="utf-8"))
        generated = build_query_schema()
        self.assertEqual(
            {item["label"] for item in generated["nodes"]},
            {item["name"] for item in spec["node_labels"]},
        )
        self.assertEqual(
            {item["type"] for item in generated["relationships"]},
            {item["name"] for item in spec["relationship_types"]},
        )
        source_properties = {
            (node["name"], prop["name"])
            for node in spec["node_labels"]
            for prop in node.get("properties", [])
        }
        generated_properties = {
            (node["label"], prop["name"])
            for node in generated["nodes"]
            for prop in node["properties"]
        }
        self.assertEqual(generated_properties, source_properties)

    def test_stale_source_hash_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            spec = directory / "ontology_spec.json"
            output = directory / "llm_query_schema.json"
            spec.write_bytes(DEFAULT_SPEC_PATH.read_bytes())
            write_query_schema(spec, output)
            source = json.loads(spec.read_text(encoding="utf-8"))
            source["status"] = "changed-after-generation"
            spec.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(SchemaCatalogError, "stale"):
                SchemaCatalog.from_generated(output, spec)


class QueryPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = SchemaCatalog.from_generated()

    def test_valid_plan(self) -> None:
        plan = QueryPlan.from_dict(plan_payload(), self.catalog)
        self.assertEqual(plan.filters["semester"], "SECOND")
        self.assertEqual(plan.requested_fields, ("course_code", "name_ko", "credits"))

    def test_invalid_plan_contracts(self) -> None:
        mutations = []
        for key, value in (
            ("question", ""),
            ("requested_fields", ["not_in_ontology"]),
        ):
            payload = plan_payload()
            payload[key] = value
            mutations.append(payload)
        payload = plan_payload()
        payload["filters"]["semester"] = "THIRD"
        mutations.append(payload)
        payload = plan_payload()
        del payload["filters"]["department_code"]
        mutations.append(payload)
        payload = plan_payload()
        payload["filters"]["unsupported"] = 1
        mutations.append(payload)
        for payload in mutations:
            with self.subTest(payload=payload), self.assertRaises(QueryPlanError):
                QueryPlan.from_dict(payload, self.catalog)


class CypherValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = SchemaCatalog.from_generated()
        cls.plan = QueryPlan.from_dict(plan_payload(), cls.catalog)
        cls.validator = CypherValidator(cls.catalog)

    def assert_rejected(self, query: str, code: str) -> None:
        with self.assertRaises(CypherValidationError) as raised:
            self.validator.validate(self.plan, query)
        self.assertEqual(raised.exception.code, code)

    def test_safe_query_passes(self) -> None:
        result = self.validator.validate(self.plan, SAFE_QUERY)
        self.assertEqual(result.limit, 100)
        self.assertIn("CourseOffering", result.labels)
        self.assertEqual(result.parameters, self.plan.filters)

    def test_comments_are_not_treated_as_write_tokens(self) -> None:
        query = "// CREATE DELETE DROP\n" + SAFE_QUERY
        self.assertEqual(self.validator.validate(self.plan, query).limit, 100)

    def test_write_and_multi_statement_are_rejected(self) -> None:
        self.assert_rejected(SAFE_QUERY.replace("RETURN", "DELETE o RETURN", 1), "CYPHER_FORBIDDEN_KEYWORD")
        self.assert_rejected(SAFE_QUERY + "; MATCH (n:Course) RETURN n LIMIT 1", "CYPHER_MULTI_STATEMENT")

    def test_unknown_label_relationship_endpoint_and_property(self) -> None:
        self.assert_rejected(SAFE_QUERY.replace("Course)", "UnknownLabel)", 1), "CYPHER_UNKNOWN_LABEL")
        self.assert_rejected(SAFE_QUERY.replace("OF_COURSE", "UNKNOWN_REL", 1), "CYPHER_UNKNOWN_RELATIONSHIP")
        self.assert_rejected(
            SAFE_QUERY.replace("(cv)-[:HAS_OFFERING]->(o:CourseOffering)", "(c)-[:HAS_OFFERING]->(o:CourseOffering)"),
            "CYPHER_RELATIONSHIP_ENDPOINT",
        )
        self.assert_rejected(SAFE_QUERY.replace("c.name_ko", "c.unknown_property", 1), "CYPHER_UNKNOWN_PROPERTY")

    def test_parameter_limit_evidence_and_verified_policies(self) -> None:
        self.assert_rejected(SAFE_QUERY.replace("$semester", "'SECOND'", 1), "CYPHER_LITERAL_VALUE")
        self.assert_rejected(SAFE_QUERY.replace("$academic_year", "2026", 1), "CYPHER_LITERAL_VALUE")
        self.assert_rejected(SAFE_QUERY.replace("LIMIT 100", "LIMIT 101"), "CYPHER_LIMIT_EXCEEDED")
        self.assert_rejected(SAFE_QUERY.replace("-[:SUPPORTED_BY]->", "-[:OF_COURSE]->"), "CYPHER_RELATIONSHIP_ENDPOINT")
        self.assert_rejected(
            SAFE_QUERY.replace("e.verification_status = 'VERIFIED'", "e.verification_status = $semester"),
            "CYPHER_VERIFIED_FILTER_REQUIRED",
        )

    def test_variable_length_path_is_rejected(self) -> None:
        self.assert_rejected(SAFE_QUERY.replace("[:HAS_OFFERING]", "[:HAS_OFFERING*]"), "CYPHER_VARIABLE_LENGTH_PATH")


def valid_row() -> dict[str, Any]:
    return {
        "course_code": "CDA0091",
        "name_ko": "인공지능",
        "credits": 3,
        "academic_year": 2026,
        "department_code": "컴퓨터공학과",
        "grade_year": [3],
        "semester": "SECOND",
        "completion_type": "MAJOR_ELECTIVE",
        "fact_status": "VERIFIED",
        "evidence_id": "evidence:test",
        "excerpt_page": 18,
        "source_pdf_page": 263,
        "printed_page": 255,
        "source_text": "검증된 원문",
        "evidence_verification_status": "VERIFIED",
    }


class ResultValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        catalog = SchemaCatalog.from_generated()
        cls.plan = QueryPlan.from_dict(plan_payload(), catalog)

    def test_valid_result(self) -> None:
        result = ResultValidator().validate(self.plan, [valid_row()])
        self.assertEqual((result.row_count, result.evidence_count), (1, 1))

    def test_fields_evidence_scope_and_duplicates(self) -> None:
        cases: list[tuple[dict[str, Any], str]] = []
        row = valid_row()
        del row["credits"]
        cases.append((row, "RESULT_FIELD_MISSING"))
        row = valid_row()
        row["evidence_verification_status"] = "REVIEW_REQUIRED"
        cases.append((row, "RESULT_EVIDENCE_NOT_VERIFIED"))
        row = valid_row()
        row["academic_year"] = 2025
        cases.append((row, "RESULT_SCOPE_MISMATCH"))
        for row, code in cases:
            with self.subTest(code=code), self.assertRaises(ResultValidationError) as raised:
                ResultValidator().validate(self.plan, [row])
            self.assertEqual(raised.exception.code, code)
        with self.assertRaises(ResultValidationError) as raised:
            ResultValidator().validate(self.plan, [valid_row(), copy.deepcopy(valid_row())])
        self.assertEqual(raised.exception.code, "RESULT_DUPLICATE_ROW")


class FakeExplainer:
    def explain(self, validated):
        return ExplainedCypher(validated, ("NodeIndexSeek",), ())


class FakeExecutor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, explained):
        return self.rows


class PipelineTraceTests(unittest.TestCase):
    def test_success_and_failure_trace_all_stages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace_dir = Path(directory)
            pipeline = SafetyPipeline(FakeExplainer(), FakeExecutor([valid_row()]), trace_dir=trace_dir)
            outcome = pipeline.run(plan_payload(), SAFE_QUERY)
            trace = json.loads(outcome.trace_path.read_text(encoding="utf-8"))
            self.assertEqual([event["status"] for event in trace["events"]], ["PASS"] * 6)
            self.assertEqual(trace["ontology_version"], "0.2.0")

            with self.assertRaises(SafetyPipelineError) as raised:
                pipeline.run(plan_payload(), SAFE_QUERY.replace("LIMIT 100", "LIMIT 1000"))
            failed = json.loads(raised.exception.trace_path.read_text(encoding="utf-8"))
            self.assertEqual(failed["events"][2]["status"], TraceStatus.FAIL)
            self.assertEqual([event["status"] for event in failed["events"][3:]], ["SKIPPED"] * 3)


if __name__ == "__main__":
    unittest.main()
