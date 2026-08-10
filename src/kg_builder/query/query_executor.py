"""Internal read-only executor for EXPLAIN-approved Cypher."""

from __future__ import annotations

import json
from typing import Any

from neo4j import unit_of_work
from neo4j.exceptions import Neo4jError

from .query_explainer import ExplainedCypher
from .cypher_validator import CypherValidationError, defensive_read_only_check


class QueryExecutionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class DynamicQueryExecutor:
    def __init__(
        self,
        driver: Any,
        database: str,
        *,
        timeout_seconds: float = 5.0,
        max_row_bytes: int = 65_536,
        max_response_bytes: int = 1_048_576,
    ):
        self.driver = driver
        self.database = database
        self.timeout_seconds = timeout_seconds
        self.max_row_bytes = max_row_bytes
        self.max_response_bytes = max_response_bytes

    def execute(self, explained: ExplainedCypher) -> list[dict[str, Any]]:
        if not isinstance(explained, ExplainedCypher) or not explained._is_approved():
            raise QueryExecutionError(
                "EXPLAIN_APPROVAL_REQUIRED",
                "executor accepts only QueryExplainer-issued objects through SafetyPipeline",
            )
        validated = explained.validated
        try:
            defensive_read_only_check(validated.text)
        except CypherValidationError as exc:
            raise QueryExecutionError("EXECUTOR_SAFETY_REJECTED", str(exc)) from exc

        def run(tx: Any) -> list[dict[str, Any]]:
            result = tx.run(validated.text, validated.parameters)
            rows: list[dict[str, Any]] = []
            total_bytes = 0
            for record in result:
                if len(rows) >= validated.limit:
                    raise QueryExecutionError(
                        "RESULT_LIMIT_EXCEEDED",
                        f"query returned more than validated LIMIT {validated.limit}",
                    )
                row = record.data()
                row["fact_label"] = validated.provenance.fact_label
                row_bytes = len(
                    json.dumps(row, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
                )
                if row_bytes > self.max_row_bytes:
                    raise QueryExecutionError(
                        "RESULT_ROW_BYTES_EXCEEDED",
                        f"one result row exceeds {self.max_row_bytes} bytes",
                    )
                total_bytes += row_bytes
                if total_bytes > self.max_response_bytes:
                    raise QueryExecutionError(
                        "RESULT_TOTAL_BYTES_EXCEEDED",
                        f"serialized result exceeds {self.max_response_bytes} bytes",
                    )
                rows.append(row)
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
