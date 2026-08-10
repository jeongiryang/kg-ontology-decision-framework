"""Internal read-only executor for EXPLAIN-approved Cypher."""

from __future__ import annotations

from typing import Any

from neo4j import unit_of_work
from neo4j.exceptions import Neo4jError

from .query_explainer import ExplainedCypher


class QueryExecutionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class DynamicQueryExecutor:
    def __init__(self, driver: Any, database: str, *, timeout_seconds: float = 5.0):
        self.driver = driver
        self.database = database
        self.timeout_seconds = timeout_seconds

    def execute(self, explained: ExplainedCypher) -> list[dict[str, Any]]:
        validated = explained.validated

        def run(tx: Any) -> list[dict[str, Any]]:
            result = tx.run(validated.text, validated.parameters)
            rows = [record.data() for record in result]
            if len(rows) > validated.limit:
                raise QueryExecutionError(
                    "RESULT_LIMIT_EXCEEDED",
                    f"query returned more than validated LIMIT {validated.limit}",
                )
            return rows

        try:
            with self.driver.session(database=self.database) as session:
                return session.execute_read(unit_of_work(timeout=self.timeout_seconds)(run))
        except QueryExecutionError:
            raise
        except Neo4jError as exc:
            raise QueryExecutionError(
                "NEO4J_READ_FAILED", f"Neo4j read failed: {exc.code or exc.__class__.__name__}"
            ) from exc
