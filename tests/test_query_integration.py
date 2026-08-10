from __future__ import annotations

import os
import unittest

from neo4j import GraphDatabase

from kg_builder.config import Neo4jSettings
from kg_builder.query_contracts import Answerability, Intent, QueryRequest
from kg_builder.query_service import Neo4jReadExecutor, QueryService


@unittest.skipUnless(
    os.getenv("KG_NEO4J_INTEGRATION") == "1",
    "set KG_NEO4J_INTEGRATION=1 to run read-only local Neo4j tests",
)
class QueryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.settings = Neo4jSettings.from_env()
        cls.driver = GraphDatabase.driver(
            cls.settings.uri, auth=(cls.settings.user, cls.settings.password)
        )
        cls.driver.verify_connectivity()
        cls.service = QueryService(Neo4jReadExecutor(cls.driver, cls.settings.database))
        cls.before = cls._counts()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.after = cls._counts()
            if cls.after != cls.before:
                raise AssertionError(
                    f"read-only query tests changed database counts: {cls.before} -> {cls.after}"
                )
        finally:
            cls.driver.close()

    @classmethod
    def _counts(cls) -> tuple[int, int, int]:
        with cls.driver.session(database=cls.settings.database) as session:
            row = session.run(
                "MATCH (n) WITH count(n) AS nodes "
                "OPTIONAL MATCH ()-[r]->() "
                "RETURN nodes, count(r) AS relationships"
            ).single(strict=True)
            evidence = session.run(
                "MATCH (e:Evidence) RETURN count(e) AS count"
            ).single(strict=True)["count"]
        return row["nodes"], row["relationships"], evidence

    def test_all_supported_intents_against_verified_graph(self) -> None:
        self.assertEqual(self.before, (1518, 3260, 511))
        cases = [
            (
                Intent.GET_GENERAL_EDUCATION_MIN_CREDITS,
                {"academic_year": 2026},
                lambda answer: answer["requirement"]["value"] == 34,
            ),
            (
                Intent.GET_BALANCED_GENERAL_REQUIREMENT,
                {"academic_year": 2026},
                lambda answer: {item["value"] for item in answer["requirements"]} == {1, 12},
            ),
            (
                Intent.GET_TRANSFER_GENERAL_EXEMPTION,
                {"academic_year": 2026, "admission_type": "TRANSFER"},
                lambda answer: answer["exempt"] is True,
            ),
            (
                Intent.GET_COURSE_OFFERING,
                {
                    "academic_year": 2026,
                    "department": "컴퓨터공학과",
                    "course_name": "자료구조",
                },
                lambda answer: answer["offerings"][0]["semester"] == "FIRST",
            ),
            (
                Intent.GET_MAJOR_REQUIRED_COURSES,
                {"academic_year": 2026, "department": "컴퓨터공학과"},
                lambda answer: answer["course_count"] == 9
                and answer["total_credits"] == 21,
            ),
            (
                Intent.GET_COURSE_COMPLETION_TYPE,
                {
                    "academic_year": 2026,
                    "department": "컴퓨터공학과",
                    "course_code": "CDA0008",
                },
                lambda answer: answer["completion_types"] == ["MAJOR_ELECTIVE"],
            ),
        ]
        for intent, parameters, assertion in cases:
            with self.subTest(intent=intent):
                response = self.service.execute(
                    QueryRequest.from_dict({"intent": intent.value, "parameters": parameters})
                )
                self.assertEqual(response.answerability, Answerability.ANSWERABLE)
                self.assertTrue(response.evidence)
                self.assertTrue(assertion(response.answer))
        self.assertEqual(self._counts(), self.before)


if __name__ == "__main__":
    unittest.main()
