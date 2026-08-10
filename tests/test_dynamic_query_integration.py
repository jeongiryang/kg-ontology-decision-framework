from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from neo4j import GraphDatabase

from kg_builder.config import ConfigurationError, Neo4jQuerySettings
from kg_builder.query.query_executor import DynamicQueryExecutor
from kg_builder.query.query_explainer import QueryExplainError, QueryExplainer
from kg_builder.query.cypher_validator import ProvenanceContract, ValidatedCypher
from kg_builder.query.safety_pipeline import SafetyPipeline

from tests.test_dynamic_query_safety import SAFE_QUERY, plan_payload


@unittest.skipUnless(
    os.getenv("KG_NEO4J_INTEGRATION") == "1",
    "set KG_NEO4J_INTEGRATION=1 to run read-only local Neo4j tests",
)
class DynamicQueryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.settings = Neo4jQuerySettings.from_env()
        except ConfigurationError as exc:
            raise unittest.SkipTest(str(exc)) from exc
        cls.driver = GraphDatabase.driver(
            cls.settings.uri, auth=(cls.settings.user, cls.settings.password)
        )
        cls.driver.verify_connectivity()
        cls.before = cls._counts()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            after = cls._counts()
            if after != cls.before:
                raise AssertionError(f"dynamic read pipeline changed DB: {cls.before} -> {after}")
        finally:
            cls.driver.close()

    @classmethod
    def _counts(cls) -> tuple[int, int, int]:
        with cls.driver.session(database=cls.settings.database) as session:
            row = session.run(
                "MATCH (n) WITH count(n) AS nodes OPTIONAL MATCH ()-[r]->() "
                "RETURN nodes, count(r) AS relationships"
            ).single(strict=True)
            evidence = session.run(
                "MATCH (e:Evidence) RETURN count(e) AS count"
            ).single(strict=True)["count"]
        return row["nodes"], row["relationships"], evidence

    def test_explain_execute_validate_and_database_is_unchanged(self) -> None:
        self.assertEqual(self.before, (1518, 3260, 511))
        with tempfile.TemporaryDirectory() as directory:
            pipeline = SafetyPipeline(
                QueryExplainer(self.driver, self.settings.database),
                DynamicQueryExecutor(self.driver, self.settings.database),
                trace_dir=Path(directory),
            )
            outcome = pipeline.run(plan_payload(), SAFE_QUERY)
        self.assertGreater(outcome.result.row_count, 0)
        self.assertEqual(outcome.result.row_count, outcome.result.evidence_count)
        self.assertTrue(outcome.explain_operators)
        self.assertEqual(self._counts(), self.before)

    def test_explain_syntax_failure_blocks_execution(self) -> None:
        invalid = ValidatedCypher._issue(
            text="MATCH (n:Course RETURN n LIMIT 1",
            parameters={},
            limit=1,
            labels=("Course",),
            relationship_types=(),
            provenance=ProvenanceContract("n", "CourseOffering", "offering_id", "e"),
        )
        with self.assertRaises(QueryExplainError) as raised:
            QueryExplainer(self.driver, self.settings.database).explain(invalid)
        self.assertEqual(raised.exception.code, "NEO4J_EXPLAIN_FAILED")
        self.assertEqual(self._counts(), self.before)


if __name__ == "__main__":
    unittest.main()
