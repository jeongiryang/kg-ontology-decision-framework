"""Neo4j EXPLAIN gate for statically validated Cypher."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from neo4j import unit_of_work
from neo4j.exceptions import Neo4jError

from .cypher_validator import ValidatedCypher


class QueryExplainError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ExplainedCypher:
    validated: ValidatedCypher
    operators: tuple[str, ...]
    notifications: tuple[str, ...]


def _plan_operators(plan: Any) -> tuple[str, ...]:
    found: list[str] = []

    def walk(node: Any) -> None:
        if node is None:
            return
        if isinstance(node, dict):
            operator = node.get("operatorType")
            children = node.get("children", ())
        else:
            operator = getattr(node, "operator_type", None)
            children = getattr(node, "children", ())
        if operator:
            found.append(str(operator).split("@", 1)[0])
        for child in children or ():
            walk(child)

    walk(plan)
    return tuple(found)


def _notification_text(summary: Any) -> tuple[str, ...]:
    found: list[str] = []
    for item in getattr(summary, "gql_status_objects", ()) or ():
        if not getattr(item, "is_notification", False):
            continue
        if isinstance(item, dict):
            found.append(" ".join(str(item.get(key, "")) for key in ("code", "title", "description")))
        else:
            found.append(
                " ".join(
                    str(value)
                    for value in (
                        getattr(item, "gql_status", ""),
                        getattr(item, "status_description", ""),
                        getattr(item, "classification", ""),
                    )
                    if value
                )
            )
    return tuple(text.strip() for text in found if text.strip())


class QueryExplainer:
    def __init__(self, driver: Any, database: str, *, timeout_seconds: float = 5.0):
        self.driver = driver
        self.database = database
        self.timeout_seconds = timeout_seconds

    def explain(self, validated: ValidatedCypher) -> ExplainedCypher:
        def run(tx: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
            result = tx.run("EXPLAIN " + validated.text, validated.parameters)
            summary = result.consume()
            return _plan_operators(summary.plan), _notification_text(summary)

        try:
            with self.driver.session(database=self.database) as session:
                operators, notifications = session.execute_read(
                    unit_of_work(timeout=self.timeout_seconds)(run)
                )
        except Neo4jError as exc:
            raise QueryExplainError(
                "NEO4J_EXPLAIN_FAILED", f"Neo4j EXPLAIN failed: {exc.code or exc.__class__.__name__}"
            ) from exc
        unsafe_notifications = [
            item
            for item in notifications
            if any(
                marker in item.lower()
                for marker in (
                    "cartesianproduct",
                    "cartesian product",
                    "unknownlabel",
                    "unknownrelationshiptype",
                    "unknownpropertykey",
                    "unbounded",
                )
            )
        ]
        if unsafe_notifications:
            raise QueryExplainError(
                "NEO4J_EXPLAIN_NOTIFICATION", "; ".join(unsafe_notifications)
            )
        dangerous_operators = [
            operator
            for operator in operators
            if operator in {"AllNodesScan", "DirectedAllRelationshipsScan", "UndirectedAllRelationshipsScan"}
            or "CartesianProduct" in operator
        ]
        if dangerous_operators:
            raise QueryExplainError(
                "NEO4J_EXPLAIN_DANGEROUS_PLAN",
                f"unsafe plan operators: {sorted(set(dangerous_operators))}",
            )
        return ExplainedCypher(validated, operators, notifications)
