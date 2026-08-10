"""Neo4j EXPLAIN gate for statically validated Cypher."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from neo4j import unit_of_work
from neo4j.exceptions import Neo4jError

from .cypher_validator import ValidatedCypher


class QueryExplainError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_EXPLAIN_SEAL = object()


@dataclass(frozen=True, slots=True, init=False)
class ExplainedCypher:
    validated: ValidatedCypher
    operators: tuple[str, ...]
    notifications: tuple[str, ...]
    _seal: object = field(repr=False, compare=False)

    def __init__(
        self,
        validated: ValidatedCypher,
        operators: tuple[str, ...],
        notifications: tuple[str, ...],
        *,
        _seal: object | None = None,
    ) -> None:
        if _seal is not _EXPLAIN_SEAL or not validated._is_approved():
            raise TypeError("ExplainedCypher can only be issued by QueryExplainer")
        object.__setattr__(self, "validated", validated)
        object.__setattr__(self, "operators", operators)
        object.__setattr__(self, "notifications", notifications)
        object.__setattr__(self, "_seal", _seal)

    @classmethod
    def _issue(
        cls,
        validated: ValidatedCypher,
        operators: tuple[str, ...],
        notifications: tuple[str, ...],
    ) -> "ExplainedCypher":
        return cls(validated, operators, notifications, _seal=_EXPLAIN_SEAL)

    def _is_approved(self) -> bool:
        return self._seal is _EXPLAIN_SEAL and self.validated._is_approved()


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


WRITE_OPERATOR_MARKERS = (
    "create",
    "delete",
    "detachdelete",
    "setproperty",
    "setproperties",
    "setlabels",
    "removelabels",
    "merge",
    "foreach",
    "procedurecall",
    "administration",
    "schema",
    "loadcsv",
    "drop",
    "alter",
    "grant",
    "deny",
    "revoke",
    "terminate",
)


def unsafe_plan_operators(operators: tuple[str, ...]) -> tuple[str, ...]:
    unsafe = []
    for operator in operators:
        normalized = re.sub(r"[^a-z]", "", operator.lower())
        if (
            operator in {
                "AllNodesScan",
                "DirectedAllRelationshipsScan",
                "UndirectedAllRelationshipsScan",
            }
            or "CartesianProduct" in operator
            or any(marker in normalized for marker in WRITE_OPERATOR_MARKERS)
        ):
            unsafe.append(operator)
    return tuple(unsafe)


class QueryExplainer:
    def __init__(self, driver: Any, database: str, *, timeout_seconds: float = 5.0):
        self.driver = driver
        self.database = database
        self.timeout_seconds = timeout_seconds

    def explain(self, validated: ValidatedCypher) -> ExplainedCypher:
        if not isinstance(validated, ValidatedCypher) or not validated._is_approved():
            raise QueryExplainError(
                "CYPHER_VALIDATION_REQUIRED", "QueryExplainer requires validator-issued Cypher"
            )

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
        dangerous_operators = unsafe_plan_operators(operators)
        if dangerous_operators:
            raise QueryExplainError(
                "NEO4J_EXPLAIN_DANGEROUS_PLAN",
                f"unsafe plan operators: {sorted(set(dangerous_operators))}",
            )
        return ExplainedCypher._issue(validated, operators, notifications)
