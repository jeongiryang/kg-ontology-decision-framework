from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from neo4j import GraphDatabase

from kg_builder.answer.generator import EvidenceAnswerGenerator
from kg_builder.answer.service import CurriculumChatService
from kg_builder.answer.validator import AnswerValidator
from kg_builder.config import Neo4jQuerySettings
from kg_builder.llm.client import LLMSettings, create_llm_client
from kg_builder.llm.cypher_generator import LocalCypherGenerator
from kg_builder.llm.planner import LocalQueryPlanner
from kg_builder.query.natural_language_service import (
    NaturalLanguageQueryService,
    NaturalLanguageResult,
)
from kg_builder.query.query_executor import DynamicQueryExecutor
from kg_builder.query.query_explainer import QueryExplainer
from kg_builder.query.safety_pipeline import SafetyPipeline
from kg_builder.query.schema_selector import QuerySchemaSelector


class RecordingQueryService:
    def __init__(self, wrapped: NaturalLanguageQueryService):
        self.wrapped = wrapped
        self.last_result: NaturalLanguageResult | None = None

    def ask(self, question: str) -> NaturalLanguageResult:
        self.last_result = self.wrapped.ask(question)
        return self.last_result


class RecordingAnswerValidator(AnswerValidator):
    def __init__(self):
        super().__init__()
        self.last_error_code: str | None = None

    def validate(self, draft, rows, *, question=None):
        try:
            result = super().validate(draft, rows, question=question)
            self.last_error_code = None
            return result
        except Exception as exc:
            self.last_error_code = getattr(exc, "code", exc.__class__.__name__)
            raise


@unittest.skipUnless(
    os.getenv("KG_LOCAL_LLM_INTEGRATION") == "1",
    "set KG_LOCAL_LLM_INTEGRATION=1 for grounded answer smoke tests",
)
class EvidenceAnswerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.neo4j = Neo4jQuerySettings.from_env()
        cls.llm = LLMSettings.from_env()
        cls.driver = GraphDatabase.driver(
            cls.neo4j.uri, auth=(cls.neo4j.user, cls.neo4j.password)
        )
        cls.driver.verify_connectivity()
        cls.before = cls._counts()
        cls.trace_temp = tempfile.TemporaryDirectory()
        cls.trace_dir = Path(cls.trace_temp.name)
        client = create_llm_client(cls.llm)
        dynamic = NaturalLanguageQueryService(
            LocalQueryPlanner(client),
            LocalCypherGenerator(client),
            SafetyPipeline(
                QueryExplainer(cls.driver, cls.neo4j.database),
                DynamicQueryExecutor(cls.driver, cls.neo4j.database),
                trace_dir=cls.trace_dir,
            ),
            QuerySchemaSelector(),
            model=cls.llm.model,
            generator_retries=cls.llm.max_retries,
        )
        cls.query_service = RecordingQueryService(dynamic)
        cls.answer_validator = RecordingAnswerValidator()
        cls.chat = CurriculumChatService(
            cls.query_service,
            EvidenceAnswerGenerator(client),
            validator=cls.answer_validator,
            answer_retries=cls.llm.max_retries,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        trace_dir = cls.trace_dir
        try:
            cls.after = cls._counts()
            if cls.after != cls.before:
                raise AssertionError(
                    f"read-only answer smoke changed DB: {cls.before} -> {cls.after}"
                )
        finally:
            cls.driver.close()
            cls.trace_temp.cleanup()
        if trace_dir.exists():
            raise AssertionError("temporary answer trace directory was not cleaned")

    @classmethod
    def _counts(cls) -> tuple[int, int, int]:
        with cls.driver.session(database=cls.neo4j.database) as session:
            nodes = session.run("MATCH (n) RETURN count(n) AS n").single(strict=True)["n"]
            relationships = session.run(
                "MATCH ()-[r]->() RETURN count(r) AS n"
            ).single(strict=True)["n"]
            evidence = session.run(
                "MATCH (e:Evidence) RETURN count(e) AS n"
            ).single(strict=True)["n"]
        return nodes, relationships, evidence

    def test_six_grounded_korean_answers_and_database_invariance(self) -> None:
        self.assertEqual(self.before, (1518, 3260, 511))
        questions = {
            "general": "2026학년도 교양 최소 이수학점은?",
            "balanced": "2026학년도 균형교양 이수요건은?",
            "transfer": "2026학년도 편입생도 교양을 이수해야 하나?",
            "structure": "2026학년도 컴퓨터공학과 자료구조는 몇 학년 몇 학기에 개설되나?",
            "required": "2026학년도 컴퓨터공학과 전공필수 과목은?",
            "completion": "2026학년도 컴퓨터공학과 자료구조의 이수구분은?",
        }
        results = {}
        source_results = {}
        for name, question in questions.items():
            result = self.chat.ask(question)
            results[name] = result
            source_results[name] = self.query_service.last_result
            print(
                {
                    "case": name,
                    "status": result.status.value,
                    "answer": result.answer_text,
                    "citations": len(result.citations),
                    "error": result.error_code,
                    "validation_error": self.answer_validator.last_error_code,
                }
            )

        for name, response in results.items():
            with self.subTest(case=name):
                self.assertEqual(response.status.value, "ANSWERABLE")
                self.assertTrue(response.answer_text)
                self.assertTrue(response.citations)
                self.assertTrue(all(item.source_text for item in response.citations))
                self.assertTrue(
                    all(
                        item.excerpt_page > 0
                        and item.source_pdf_page > 0
                        and item.printed_page > 0
                        for item in response.citations
                    )
                )
                source = source_results[name]
                self.assertIsNotNone(source)
                self.assertEqual(source.status, "ANSWERABLE")
                row_pairs = {
                    (row["fact_id"], row["evidence_id"])
                    for row in source.rows
                    if row["fact_status"] == "VERIFIED"
                    and row["evidence_verification_status"] == "VERIFIED"
                }
                citation_pairs = {
                    (fact_id, citation.evidence_id)
                    for citation in response.citations
                    for fact_id in citation.fact_ids
                }
                self.assertTrue(citation_pairs)
                self.assertTrue(citation_pairs.issubset(row_pairs))

        self.assertIn("34", results["general"].answer_text)
        for token in ("4", "1", "12", "영역", "과목", "학점"):
            self.assertIn(token, results["balanced"].answer_text)
        self.assertTrue(
            "면제" in results["transfer"].answer_text
            or "의무가 없다" in results["transfer"].answer_text
        )
        self.assertIn("2", results["structure"].answer_text)
        self.assertIn("1", results["structure"].answer_text)
        required_source = source_results["required"]
        unique_required = {row["fact_id"]: row for row in required_source.rows}
        self.assertEqual(len(unique_required), 9)
        self.assertEqual(sum(row["credits"] for row in unique_required.values()), 21)
        self.assertIn("9", results["required"].answer_text)
        self.assertIn("21", results["required"].answer_text)
        for row in unique_required.values():
            self.assertIn(row["name_ko"], results["required"].answer_text)
        self.assertIn("전공선택", results["completion"].answer_text)
        self.assertEqual(self._counts(), self.before)


if __name__ == "__main__":
    unittest.main()
