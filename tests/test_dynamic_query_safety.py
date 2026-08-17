from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kg_builder.config import ConfigurationError, Neo4jQuerySettings
from kg_builder.query.cypher_validator import (
    CypherValidationError,
    CypherValidator,
    ProvenanceContract,
    ValidatedCypher,
    lex_cypher,
)
from kg_builder.query.query_explainer import ExplainedCypher, unsafe_plan_operators
from kg_builder.query.query_executor import DynamicQueryExecutor, QueryExecutionError
from kg_builder.query.query_plan import QueryPlan, QueryPlanError
from kg_builder.query.query_trace import TracePolicy, TraceStatus
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
  AND d.department_id = $department_id
  AND $grade_year IN o.grade_year
  AND o.semester = $semester
  AND o.completion_type = $completion_type
  AND o.status = 'VERIFIED'
  AND e.verification_status = 'VERIFIED'
RETURN c.course_code AS course_code,
       c.name_ko AS name_ko,
       o.credits AS credits,
       cv.academic_year AS academic_year,
       d.department_id AS department_id,
       o.grade_year AS grade_year,
       o.semester AS semester,
       o.completion_type AS completion_type,
       o.offering_id AS fact_id,
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
            "department_id": "department:cwnu:cse",
            "grade_year": 3,
            "semester": "SECOND",
            "completion_type": "MAJOR_ELECTIVE",
        },
        "requested_fields": ["course_code", "name_ko", "credits"],
        "evidence_required": True,
        "intent": "설명 및 추적용 선택 메타데이터",
    }


def provenance() -> ProvenanceContract:
    return ProvenanceContract("o", "CourseOffering", "offering_id", "e")


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
        fact_labels = set(generated["query_policy"]["provenance"]["fact_labels"])
        self.assertIn("CourseOffering", fact_labels)
        self.assertIn("CreditRequirement", fact_labels)
        self.assertNotIn("Course", fact_labels)

    def test_stale_or_modified_schema_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            spec = directory / "ontology_spec.json"
            output = directory / "llm_query_schema.json"
            spec.write_bytes(DEFAULT_SPEC_PATH.read_bytes())
            write_query_schema(spec, output)
            generated = json.loads(output.read_text(encoding="utf-8"))
            generated["query_policy"]["maximum_result_rows"] = 99
            output.write_text(json.dumps(generated), encoding="utf-8")
            self.assertFalse(check_query_schema(spec, output))
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

    def test_valid_dynamic_plan(self) -> None:
        plan = QueryPlan.from_dict(plan_payload(), self.catalog)
        self.assertEqual(plan.filters["department_id"], "department:cwnu:cse")
        self.assertEqual(plan.requested_fields, ("course_code", "name_ko", "credits"))
        self.assertIsNotNone(plan.intent)

        rule_plan = plan_payload()
        rule_plan["filters"] = {
            "academic_year": 2026,
            "rule_id": "rule:cwnu:2026:general:min-total-default",
        }
        rule_plan["requested_fields"] = ["value", "operator", "unit"]
        self.assertNotIn(
            "department_id", QueryPlan.from_dict(rule_plan, self.catalog).filters
        )

    def test_invalid_plan_contracts(self) -> None:
        mutations = []
        for key, value in (("question", ""), ("requested_fields", ["not_in_ontology"])):
            payload = plan_payload()
            payload[key] = value
            mutations.append(payload)
        payload = plan_payload()
        payload["question"] = "x" * 2_001
        mutations.append(payload)
        payload = plan_payload()
        payload["filters"]["semester"] = "THIRD"
        mutations.append(payload)
        payload = plan_payload()
        del payload["filters"]["department_id"]
        mutations.append(payload)
        payload = plan_payload()
        payload["filters"]["department_code"] = "CSE"
        mutations.append(payload)
        payload = plan_payload()
        payload["evidence_required"] = False
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

    def assert_rejected(self, query: str, code: str | None = None) -> None:
        with self.assertRaises(CypherValidationError) as raised:
            self.validator.validate(self.plan, query)
        if code:
            self.assertEqual(raised.exception.code, code)

    def test_safe_query_and_limited_with_order_skip_pass(self) -> None:
        result = self.validator.validate(self.plan, SAFE_QUERY)
        self.assertEqual(result.limit, 100)
        self.assertEqual(result.provenance.fact_label, "CourseOffering")
        extended = SAFE_QUERY.replace(
            "RETURN c.course_code", "WITH cv, d, o, c, e\nRETURN c.course_code"
        ).replace("LIMIT 100", "ORDER BY course_code ASC\nSKIP 0\nLIMIT 100")
        self.assertEqual(self.validator.validate(self.plan, extended).limit, 100)

    def test_comments_are_removed_from_approved_canonical_cypher(self) -> None:
        markers = (
            "synthetic-system-prompt-marker",
            "synthetic-api-key-marker",
            "synthetic-password-marker",
            "/home/synthetic/private/path",
        )
        query = (
            "// " + " ".join(markers) + "\n"
            + SAFE_QUERY.replace(
                "MATCH (o)-[:SUPPORTED_BY]->(e:Evidence)",
                "MATCH (o)-[:SUPPORTED_BY]->(e:Evidence) /* "
                + " ".join(markers)
                + " */",
            )
        )
        validated = self.validator.validate(self.plan, query)
        for marker in markers:
            self.assertNotIn(marker, validated.text)
        self.assertNotIn("//", validated.text)
        self.assertNotIn("/*", validated.text)
        self.assertIn("MATCH (o)-[:SUPPORTED_BY]->(e:Evidence)", validated.text)

    def test_comment_like_text_in_strings_and_backticks_is_preserved_by_lexer(self) -> None:
        source = (
            "RETURN 'https://example.test/path', '문자열 // 내용', "
            "'문자열 /* 내용 */', `identifier///*value` // actual-comment"
        )
        lexed = lex_cypher(source)
        self.assertIn("'https://example.test/path'", lexed.canonical)
        self.assertIn("'문자열 // 내용'", lexed.canonical)
        self.assertIn("'문자열 /* 내용 */'", lexed.canonical)
        self.assertIn("`identifier///*value`", lexed.canonical)
        self.assertNotIn("actual-comment", lexed.canonical)
        self.assertEqual(lexed.backtick_identifiers[0].value, "identifier///*value")

    def test_unterminated_comment_empty_canonical_and_token_join_are_rejected(self) -> None:
        with self.assertRaises(CypherValidationError) as raised:
            self.validator.validate(self.plan, SAFE_QUERY + "/* not closed")
        self.assertEqual(raised.exception.code, "CYPHER_UNTERMINATED_TOKEN")

        with self.assertRaises(CypherValidationError) as raised:
            self.validator.validate(
                self.plan, "/* synthetic-api-key-marker comments only */"
            )
        self.assertEqual(raised.exception.code, "CYPHER_EMPTY")
        self.assertNotIn("synthetic-api-key-marker", str(raised.exception))

        joined = SAFE_QUERY.replace("RETURN", "IN/* split */SERT (:Course) RETURN", 1)
        with self.assertRaises(CypherValidationError):
            self.validator.validate(self.plan, joined)
        self.assertRegex(lex_cypher(joined).canonical, r"IN\s+SERT")

    def test_all_write_and_unsupported_clauses_are_rejected(self) -> None:
        fragments = (
            "CREATE (:Course)",
            "INSERT (:Course)",
            "MERGE (:Course)",
            "DELETE o",
            "DETACH DELETE o",
            "SET o.status = 'VERIFIED'",
            "REMOVE o.status",
            "DROP INDEX example",
            "ALTER DATABASE neo4j",
            "RENAME",
            "LOAD CSV FROM 'VERIFIED' AS line",
            "FOREACH (x IN [] | DELETE o)",
            "CALL apoc.help($course_code)",
            "USE neo4j",
            "SHOW DATABASES",
            "GRANT MATCH ON GRAPH neo4j TO role",
            "DENY MATCH ON GRAPH neo4j TO role",
            "REVOKE MATCH ON GRAPH neo4j FROM role",
            "START DATABASE neo4j",
            "STOP DATABASE neo4j",
            "TERMINATE TRANSACTIONS",
            "UNION MATCH (x:Course) RETURN x.course_code AS course_code",
        )
        for fragment in fragments:
            with self.subTest(fragment=fragment):
                self.assert_rejected(
                    SAFE_QUERY.replace("RETURN", fragment + " RETURN", 1),
                    "CYPHER_FORBIDDEN_KEYWORD",
                )

    def test_case_comment_semicolon_backtick_and_subquery_bypasses(self) -> None:
        self.assert_rejected(
            SAFE_QUERY.replace("RETURN", "iNsErT (:Course) RETURN", 1),
            "CYPHER_FORBIDDEN_KEYWORD",
        )
        self.assert_rejected(
            SAFE_QUERY.replace("RETURN", "IN/* split */SERT (:Course) RETURN", 1)
        )
        commented = self.validator.validate(self.plan, "// CREATE DELETE\n" + SAFE_QUERY)
        self.assertEqual(commented.limit, 100)
        self.assertNotIn("CREATE DELETE", commented.text)
        self.assert_rejected(SAFE_QUERY + ";", "CYPHER_SEMICOLON")
        self.assert_rejected(
            SAFE_QUERY + "; MATCH (n:Course) RETURN n LIMIT 1", "CYPHER_SEMICOLON"
        )
        self.assert_rejected(
            SAFE_QUERY.replace("Course)", "`Course`)", 1), "CYPHER_BACKTICK_IDENTIFIER"
        )
        subquery = SAFE_QUERY.replace(
            "AND e.verification_status = 'VERIFIED'",
            "AND e.verification_status = 'VERIFIED' "
            "AND EXISTS { MATCH (x:CourseOffering)-[:OF_COURSE]->(c) }",
        )
        self.assert_rejected(subquery, "CYPHER_SUBQUERY_FORBIDDEN")

    def test_functions_aggregates_variable_paths_and_limit_are_rejected(self) -> None:
        self.assert_rejected(
            SAFE_QUERY.replace("c.name_ko AS name_ko", "collect(c.name_ko) AS name_ko"),
            "CYPHER_FUNCTION_FORBIDDEN",
        )
        self.assert_rejected(
            SAFE_QUERY.replace("[:HAS_OFFERING]", "[:HAS_OFFERING*]"),
            "CYPHER_MATCH_PATTERN_UNSUPPORTED",
        )
        self.assert_rejected(SAFE_QUERY.replace("LIMIT 100", "LIMIT 101"), "CYPHER_LIMIT_EXCEEDED")

    def test_filter_tautology_and_parameter_scope_return_are_rejected(self) -> None:
        tautology = SAFE_QUERY.replace(
            "cv.academic_year = $academic_year", "$academic_year = $academic_year"
        )
        self.assert_rejected(tautology, "CYPHER_WHERE_UNSUPPORTED")
        parameter_return = SAFE_QUERY.replace(
            "cv.academic_year AS academic_year", "$academic_year AS academic_year"
        )
        self.assert_rejected(parameter_return, "CYPHER_PARAMETER_USAGE")
        wrong_property = SAFE_QUERY.replace(
            "d.department_id = $department_id", "d.name_ko = $department_id"
        )
        self.assert_rejected(wrong_property, "CYPHER_FILTER_BINDING")

    def test_unrelated_evidence_and_distinct_cannot_hide_provenance(self) -> None:
        unrelated = (
            SAFE_QUERY.replace(
                "MATCH (o)-[:SUPPORTED_BY]->(e:Evidence)",
                "MATCH (cv)-[:HAS_RULE]->(r:Rule)-[:SUPPORTED_BY]->(e:Evidence)",
            )
            .replace("o.status = 'VERIFIED'", "r.status = 'VERIFIED'")
            .replace("RETURN c.course_code", "RETURN DISTINCT c.course_code")
        )
        self.assert_rejected(unrelated, "CYPHER_FACT_FIELD_PROVENANCE")

    def test_unknown_schema_items_and_wrong_direction_are_rejected(self) -> None:
        self.assert_rejected(SAFE_QUERY.replace("Course)", "UnknownLabel)", 1), "CYPHER_UNKNOWN_LABEL")
        self.assert_rejected(SAFE_QUERY.replace("OF_COURSE", "UNKNOWN_REL", 1), "CYPHER_UNKNOWN_RELATIONSHIP")
        self.assert_rejected(
            SAFE_QUERY.replace(
                "(cv)-[:HAS_OFFERING]->(o:CourseOffering)",
                "(c)-[:HAS_OFFERING]->(o:CourseOffering)",
            ),
            "CYPHER_RELATIONSHIP_ENDPOINT",
        )
        self.assert_rejected(
            SAFE_QUERY.replace("c.name_ko", "c.unknown_property", 1),
            "CYPHER_UNKNOWN_PROPERTY",
        )

    def test_approval_objects_cannot_be_directly_constructed(self) -> None:
        with self.assertRaises(TypeError):
            ValidatedCypher(
                "MATCH (n:Course) RETURN n LIMIT 1",
                {},
                1,
                ("Course",),
                (),
                provenance(),
            )
        validated = self.validator.validate(self.plan, SAFE_QUERY)
        with self.assertRaises(TypeError):
            ExplainedCypher(validated, (), ())


class ExplainOperatorTests(unittest.TestCase):
    def test_write_and_scan_operators_are_rejected(self) -> None:
        operators = (
            "Create",
            "CreateNode",
            "Delete",
            "DetachDelete",
            "SetProperty",
            "SetProperties",
            "SetLabels",
            "RemoveLabels",
            "Merge",
            "Foreach",
            "ProcedureCall",
            "AdministrationCommand",
            "SchemaCommand",
            "AllNodesScan",
            "CartesianProduct",
        )
        self.assertEqual(unsafe_plan_operators(operators), operators)
        self.assertEqual(unsafe_plan_operators(("NodeIndexSeek", "Expand(All)")), ())


class ExecutorDefenseTests(unittest.TestCase):
    def test_direct_or_privately_forged_execution_is_rejected(self) -> None:
        executor = DynamicQueryExecutor(None, "neo4j")
        with self.assertRaises(QueryExecutionError) as raised:
            executor.execute(object())  # type: ignore[arg-type]
        self.assertEqual(raised.exception.code, "EXPLAIN_APPROVAL_REQUIRED")

        forged_write = ValidatedCypher._issue(
            text="MATCH (n:Course) INSERT (:Course) RETURN n.course_id AS fact_id LIMIT 1",
            parameters={},
            limit=1,
            labels=("Course",),
            relationship_types=(),
            provenance=provenance(),
        )
        forged_explain = ExplainedCypher._issue(forged_write, ("NodeIndexSeek",), ())
        with self.assertRaises(QueryExecutionError) as raised:
            executor.execute(forged_explain)
        self.assertEqual(raised.exception.code, "EXECUTOR_SAFETY_REJECTED")

        forged_comment = ValidatedCypher._issue(
            text=(
                "// synthetic-password-marker\n"
                "MATCH (n:Course) RETURN n.course_id AS fact_id LIMIT 1"
            ),
            parameters={},
            limit=1,
            labels=("Course",),
            relationship_types=(),
            provenance=provenance(),
        )
        with self.assertRaises(QueryExecutionError) as raised:
            executor.execute(
                ExplainedCypher._issue(forged_comment, ("NodeIndexSeek",), ())
            )
        self.assertEqual(raised.exception.code, "EXECUTOR_SAFETY_REJECTED")
        self.assertNotIn("synthetic-password-marker", str(raised.exception))


class QueryCredentialContractTests(unittest.TestCase):
    def test_query_credentials_do_not_fall_back_to_ingestion_credentials(self) -> None:
        ingestion_only = {
            "NEO4J_URI": "neo4j://localhost:7687",
            "NEO4J_USER": "ingestion-user",
            "NEO4J_PASSWORD": "not-logged",
            "NEO4J_DATABASE": "neo4j",
        }
        with tempfile.NamedTemporaryFile("w", suffix=".env") as dotenv_file:
            for name, value in ingestion_only.items():
                dotenv_file.write(f"{name}={value}\n")
            dotenv_file.flush()
            with patch.dict(os.environ, {}, clear=True), self.assertRaises(ConfigurationError):
                Neo4jQuerySettings.from_env(dotenv_path=dotenv_file.name)


def valid_row() -> dict[str, Any]:
    return {
        "course_code": "CDA0091",
        "name_ko": "인공지능",
        "credits": 3,
        "academic_year": 2026,
        "department_id": "department:cwnu:cse",
        "grade_year": [3],
        "semester": "SECOND",
        "completion_type": "MAJOR_ELECTIVE",
        "fact_id": "offering:cwnu:2026:cse:CDA0091:g3:SECOND",
        "fact_label": "CourseOffering",
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
        cls.plan = QueryPlan.from_dict(plan_payload(), SchemaCatalog.from_generated())

    def test_valid_result(self) -> None:
        result = ResultValidator().validate(self.plan, [valid_row()], provenance())
        self.assertEqual((result.row_count, result.evidence_count), (1, 1))

    def test_fields_evidence_scope_fact_and_duplicates(self) -> None:
        cases: list[tuple[dict[str, Any], str]] = []
        for mutation, code in (
            (("credits", None), "RESULT_FIELD_NULL"),
            (("evidence_verification_status", "REVIEW_REQUIRED"), "RESULT_EVIDENCE_NOT_VERIFIED"),
            (("academic_year", 2025), "RESULT_SCOPE_MISMATCH"),
            (("fact_label", "Rule"), "RESULT_FACT_INVALID"),
        ):
            row = valid_row()
            row[mutation[0]] = mutation[1]
            cases.append((row, code))
        for row, code in cases:
            with self.subTest(code=code), self.assertRaises(ResultValidationError) as raised:
                ResultValidator().validate(self.plan, [row], provenance())
            self.assertEqual(raised.exception.code, code)
        duplicate = copy.deepcopy(valid_row())
        duplicate["name_ko"] = "다른 표시값"
        with self.assertRaises(ResultValidationError) as raised:
            ResultValidator().validate(self.plan, [valid_row(), duplicate], provenance())
        self.assertEqual(raised.exception.code, "RESULT_DUPLICATE_PROVENANCE")

    def test_row_and_total_serialized_size_limits(self) -> None:
        row = valid_row()
        row["source_text"] = "가" * 40_000
        with self.assertRaises(ResultValidationError) as raised:
            ResultValidator(max_row_bytes=1_000).validate(self.plan, [row], provenance())
        self.assertEqual(raised.exception.code, "RESULT_ROW_BYTES_EXCEEDED")
        rows = []
        for index in range(2):
            row = valid_row()
            row["fact_id"] += f":{index}"
            row["evidence_id"] += f":{index}"
            rows.append(row)
        with self.assertRaises(ResultValidationError) as raised:
            ResultValidator(max_response_bytes=100).validate(self.plan, rows, provenance())
        self.assertEqual(raised.exception.code, "RESULT_TOTAL_BYTES_EXCEEDED")


class FakeExplainer:
    def __init__(self) -> None:
        self.validated: ValidatedCypher | None = None

    def explain(self, validated: ValidatedCypher) -> ExplainedCypher:
        self.validated = validated
        return ExplainedCypher._issue(validated, ("NodeIndexSeek",), ())


class FakeExecutor:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self.explained: ExplainedCypher | None = None

    def execute(self, explained: ExplainedCypher) -> list[dict[str, Any]]:
        self.explained = explained
        return self.rows


class PipelineTraceTests(unittest.TestCase):
    def test_canonical_cypher_reaches_explain_executor_progress_and_outcome(self) -> None:
        markers = (
            "synthetic-system-prompt-marker",
            "synthetic-api-key-marker",
            "synthetic-password-marker",
            "/home/synthetic/private/path",
        )
        query = "// " + " ".join(markers) + "\n" + SAFE_QUERY
        events = []
        explainer = FakeExplainer()
        executor = FakeExecutor([valid_row()])
        with tempfile.TemporaryDirectory() as directory:
            outcome = SafetyPipeline(
                explainer, executor, trace_dir=Path(directory)
            ).run(plan_payload(), query, progress_callback=events.append)
            trace_text = outcome.trace_path.read_text(encoding="utf-8")

        static_event = next(
            event
            for event in events
            if event.phase.value == "STATIC_VALIDATION"
            and event.state.value == "COMPLETED"
        )
        public_values = (
            outcome.validated_cypher,
            explainer.validated.text if explainer.validated else "",
            executor.explained.validated.text if executor.explained else "",
            static_event.details["validated_cypher"],
            trace_text,
        )
        for marker in markers:
            self.assertTrue(all(marker not in value for value in public_values))
        self.assertIsNotNone(explainer.validated)
        self.assertIsNotNone(executor.explained)
        self.assertEqual(outcome.validated_cypher, explainer.validated.text)
        self.assertEqual(outcome.validated_cypher, executor.explained.validated.text)

    def test_success_failure_and_default_question_privacy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = SafetyPipeline(
                FakeExplainer(), FakeExecutor([valid_row()]), trace_dir=Path(directory)
            )
            outcome = pipeline.run(plan_payload(), SAFE_QUERY)
            trace = json.loads(outcome.trace_path.read_text(encoding="utf-8"))
            self.assertEqual([event["status"] for event in trace["events"]], ["PASS"] * 6)
            self.assertNotIn("raw_question", trace)
            self.assertNotIn("question_fingerprint", trace)
            self.assertNotIn("question_sha256", trace)
            self.assertEqual(trace["question_length"], len(plan_payload()["question"]))

            with self.assertRaises(SafetyPipelineError) as raised:
                pipeline.run(plan_payload(), SAFE_QUERY.replace("LIMIT 100", "LIMIT 1000"))
            failed = json.loads(raised.exception.trace_path.read_text(encoding="utf-8"))
            self.assertEqual(failed["events"][2]["status"], TraceStatus.FAIL)
            self.assertEqual([event["status"] for event in failed["events"][3:]], ["SKIPPED"] * 3)

    def test_opt_in_raw_question_is_masked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request = plan_payload()
            request["question"] = "학번 202612345, student@example.invalid, 010-1234-5678 확인"
            with patch.dict(
                os.environ,
                {
                    "KG_QUERY_TRACE_RAW_QUESTION": "true",
                    "KG_QUERY_TRACE_RETENTION_DAYS": "7",
                },
            ):
                pipeline = SafetyPipeline(
                    FakeExplainer(), FakeExecutor([valid_row()]), trace_dir=Path(directory)
                )
                outcome = pipeline.run(request, SAFE_QUERY)
            payload = json.loads(outcome.trace_path.read_text(encoding="utf-8"))
            self.assertNotIn("202612345", payload["raw_question"])
            self.assertNotIn("student@example.invalid", payload["raw_question"])
            self.assertNotIn("010-1234-5678", payload["raw_question"])
            self.assertEqual(payload["retention_days"], 7)

    def test_opt_in_fingerprint_uses_hmac_and_requires_key(self) -> None:
        with patch.dict(
            os.environ,
            {"KG_QUERY_TRACE_FINGERPRINT": "true"},
            clear=True,
        ), self.assertRaisesRegex(ValueError, "HMAC_KEY"):
            TracePolicy.from_env()

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "KG_QUERY_TRACE_FINGERPRINT": "true",
                "KG_QUERY_TRACE_HMAC_KEY": "test-only-secret-key",
            },
            clear=True,
        ):
            pipeline = SafetyPipeline(
                FakeExplainer(), FakeExecutor([valid_row()]), trace_dir=Path(directory)
            )
            outcome = pipeline.run(plan_payload(), SAFE_QUERY)
            payload = json.loads(outcome.trace_path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["question_fingerprint"]), 64)
            self.assertNotEqual(
                payload["question_fingerprint"],
                __import__("hashlib").sha256(plan_payload()["question"].encode()).hexdigest(),
            )
            self.assertNotIn("test-only-secret-key", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
