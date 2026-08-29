"""Internal read-only executor for EXPLAIN-approved Cypher."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from neo4j import unit_of_work
from neo4j.exceptions import Neo4jError

from .query_explainer import ExplainedCypher
from .cypher_validator import CypherValidationError, defensive_read_only_check


class QueryExecutionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


EXPAND_DETAIL = re.compile(
    r"\((\w+)\)\s*(<-|-)\s*\[:?(\w+)\]\s*(->|-)\s*\((\w+)\)"
)


@dataclass(frozen=True, slots=True)
class TraversalStep:
    """One measured step of the engine's actual execution plan.

    Neo4j 는 operator 별 소요시간을 주지 않는다(``time`` 은 비어 있다). 대신 각 단계에서
    **몇 행이 나왔고 DB 를 몇 번 읽었는지**는 준다. 시간을 지어내는 대신 이 실측값을 쓴다.
    """

    order: int
    operator: str
    relationship_type: str | None
    start_variable: str | None
    end_variable: str | None
    rows: int
    db_hits: int
    # 이 operator 가 무엇을 했는지 엔진이 적은 설명. 값은 그대로 쓰되 화면에서
    # 파라미터 이름 같은 내부 표기는 다듬는다.
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """Rows plus the engine's measured traversal, from one PROFILE run."""

    rows: list[dict[str, Any]]
    steps: tuple[TraversalStep, ...] = ()


def _profile_steps(profile: Any) -> tuple[TraversalStep, ...]:
    """Flatten the PROFILE tree into execution order (leaf first).

    자식이 먼저 실행되므로 트리를 후위 순회하면 그것이 곧 실행 순서다. 확장·탐색
    operator 만 남기고 Projection/Limit 처럼 그래프를 밟지 않는 단계는 뺀다.
    """

    collected: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        for child in node.get("children") or ():
            walk(child)
        collected.append(node)

    walk(profile)
    steps: list[TraversalStep] = []
    for node in collected:
        operator = str(node.get("operatorType", "")).split("@", 1)[0]
        # 엔진이 실제로 실행한 operator 를 **전부** 남긴다. 종전에는 확장·탐색만
        # 골라내 Filter 가 37행을 9행으로 줄이는 구간이 화면에서 사라졌다. 그 구간이
        # 곧 "어디서 좁혀졌는가" 이므로 빼면 실제 동작과 달라진다.
        if not operator:
            continue
        detail = str((node.get("args") or {}).get("Details", ""))
        match = EXPAND_DETAIL.search(detail)
        relationship = start = end = None
        if match:
            left, left_arrow, relationship, right_arrow, right = match.groups()
            start, end = (left, right) if right_arrow == "->" else (right, left)
        rows = node.get("rows")
        hits = node.get("dbHits")
        steps.append(
            TraversalStep(
                order=len(steps) + 1,
                operator=operator,
                relationship_type=relationship,
                start_variable=start,
                end_variable=end,
                rows=rows if isinstance(rows, int) else 0,
                db_hits=hits if isinstance(hits, int) else 0,
                detail=detail[:160],
            )
        )
    return tuple(steps)


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

    def execute(self, explained: ExplainedCypher) -> ExecutionOutcome:
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

        def run(tx: Any) -> ExecutionOutcome:
            # 실행되는 질의는 동일하다. PROFILE 은 엔진이 실제로 밟은 단계별 행 수와
            # DB 접근 횟수를 함께 돌려줄 뿐이며, 검증기가 승인한 본문은 바뀌지 않는다.
            result = tx.run("PROFILE " + validated.text, validated.parameters)
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
            return ExecutionOutcome(rows, _profile_steps(result.consume().profile))

        try:
            with self.driver.session(database=self.database) as session:
                return session.execute_read(unit_of_work(timeout=self.timeout_seconds)(run))
        except QueryExecutionError:
            raise
        except Neo4jError as exc:
            raise QueryExecutionError(
                "NEO4J_READ_FAILED", f"Neo4j read failed: {exc.code or exc.__class__.__name__}"
            ) from exc
