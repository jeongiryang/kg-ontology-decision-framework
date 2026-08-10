from __future__ import annotations

import os
import unittest

from neo4j import GraphDatabase

from kg_builder.config import Neo4jQuerySettings
from kg_builder.llm.client import LLMSettings, create_llm_client
from kg_builder.llm.cypher_generator import LocalCypherGenerator
from kg_builder.llm.planner import LocalQueryPlanner
from kg_builder.query.natural_language_service import NaturalLanguageQueryService
from kg_builder.query.query_executor import DynamicQueryExecutor
from kg_builder.query.query_explainer import QueryExplainer
from kg_builder.query.safety_pipeline import SafetyPipeline
from kg_builder.query.schema_selector import QuerySchemaSelector


@unittest.skipUnless(
    os.getenv("KG_LOCAL_LLM_INTEGRATION") == "1",
    "set KG_LOCAL_LLM_INTEGRATION=1 for local Ollama and Neo4j smoke tests",
)
class LocalLLMIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.neo4j = Neo4jQuerySettings.from_env()
        cls.llm = LLMSettings.from_env()
        cls.driver = GraphDatabase.driver(
            cls.neo4j.uri, auth=(cls.neo4j.user, cls.neo4j.password)
        )
        cls.driver.verify_connectivity()
        cls.before = cls._counts()
        client = create_llm_client(cls.llm)
        cls.service = NaturalLanguageQueryService(
            LocalQueryPlanner(client),
            LocalCypherGenerator(client),
            SafetyPipeline(
                QueryExplainer(cls.driver, cls.neo4j.database),
                DynamicQueryExecutor(cls.driver, cls.neo4j.database),
            ),
            QuerySchemaSelector(),
            model=cls.llm.model,
            generator_retries=cls.llm.max_retries,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.after = cls._counts()
            if cls.after != cls.before:
                raise AssertionError(f"read-only smoke changed DB: {cls.before} -> {cls.after}")
        finally:
            cls.driver.close()

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

    def assert_answerable(self, question: str):
        result = self.service.ask(question)
        self.assertEqual(
            result.status,
            "ANSWERABLE",
            msg=f"{result.error_stage}:{result.error_code} plan={result.query_plan}",
        )
        self.assertGreater(result.evidence_count, 0)
        self.assertTrue(all(row["fact_status"] == "VERIFIED" for row in result.rows))
        self.assertTrue(
            all(row["evidence_verification_status"] == "VERIFIED" for row in result.rows)
        )
        return result

    def test_six_representative_questions_and_database_invariance(self) -> None:
        self.assertEqual(self.before, (1518, 3260, 511))
        questions = {
            "general": "2026학년도 교양 최소 이수학점은?",
            "balanced": "2026학년도 균형교양 이수요건은?",
            "transfer": "2026학년도 편입생도 교양을 이수해야 하나?",
            "structure": "2026학년도 컴퓨터공학과 자료구조는 몇 학년 몇 학기에 개설되나?",
            "required": "2026학년도 컴퓨터공학과 전공필수 과목은?",
            "completion": "2026학년도 컴퓨터공학과 자료구조의 이수구분은?",
        }
        results = {name: self.service.ask(question) for name, question in questions.items()}
        for name, result in results.items():
            print(
                {
                    "case": name,
                    "status": result.status,
                    "filters": (result.query_plan or {}).get("filters"),
                    "fields": (result.query_plan or {}).get("requested_fields"),
                    "rows": len(result.rows),
                    "evidence": result.evidence_count,
                    "elapsed_seconds": round(result.elapsed_seconds, 3),
                    "error": result.error_code,
                }
            )
        for name, result in results.items():
            with self.subTest(question=name):
                self.assertEqual(
                    result.status,
                    "ANSWERABLE",
                    msg=f"{result.error_stage}:{result.error_code} plan={result.query_plan}",
                )
                self.assertGreater(result.evidence_count, 0)

        general = results["general"]
        if general.status == "ANSWERABLE":
            self.assertEqual({row["value"] for row in general.rows}, {34})
        balanced = results["balanced"]
        if balanced.status == "ANSWERABLE":
            by_rule = {row["rule_ids"]: row for row in balanced.rows}
            credit = by_rule["rule:cwnu:2026:general:balanced-min-credits"]
            per_area = by_rule["rule:cwnu:2026:general:balanced-each-area-one"]
            self.assertEqual((credit["value"], credit["unit"]), (12, "CREDIT"))
            self.assertEqual(
                (per_area["value"], per_area["unit"]),
                (1, "COURSE_PER_AREA"),
            )
            self.assertIn("4개 영역", per_area["description_ko"])
            self.assertIn("1과목", per_area["description_ko"])
            self.assertIn("12학점", credit["source_text"])
            self.assertIn("영역별 각 1과목", per_area["source_text"])
            for row in (credit, per_area):
                self.assertEqual(row["fact_status"], "VERIFIED")
                self.assertEqual(row["evidence_verification_status"], "VERIFIED")
                self.assertTrue(row["evidence_id"])
                self.assertEqual(
                    (row["excerpt_page"], row["source_pdf_page"], row["printed_page"]),
                    (1, 33, 25),
                )
        transfer = results["transfer"]
        if transfer.status == "ANSWERABLE":
            self.assertTrue(any("편입생" in row["description_ko"] for row in transfer.rows))
        structure = results["structure"]
        if structure.status == "ANSWERABLE":
            self.assertEqual({tuple(row["grade_year"]) for row in structure.rows}, {(2,)})
            self.assertEqual({row["semester"] for row in structure.rows}, {"FIRST"})
        required = results["required"]
        if required.status == "ANSWERABLE":
            facts = {row["fact_id"]: row for row in required.rows}
            self.assertEqual(len(facts), 9)
            self.assertEqual(sum(row["credits"] for row in facts.values()), 21)
        completion = results["completion"]
        if completion.status == "ANSWERABLE":
            self.assertEqual(
                {row["completion_type"] for row in completion.rows}, {"MAJOR_ELECTIVE"}
            )
        self.assertEqual(self._counts(), self.before)


if __name__ == "__main__":
    unittest.main()
