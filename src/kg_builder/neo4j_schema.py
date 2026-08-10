"""Generate and apply Neo4j constraints and indexes from ontology V0.2."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .graph_bundle import BundleValidationError, validate_identifier


INDEX_SPECS: tuple[tuple[str, str], ...] = (
    ("Course", "course_code"),
    ("Course", "name_ko"),
    ("CurriculumVersion", "academic_year"),
    ("Rule", "status"),
    ("CourseOffering", "status"),
    ("Evidence", "excerpt_page"),
    ("Evidence", "source_pdf_page"),
)


def _snake(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


@dataclass(frozen=True, slots=True)
class SchemaStatement:
    name: str
    kind: str
    cypher: str


def schema_statements(spec: dict[str, Any]) -> list[SchemaStatement]:
    labels = {item["name"]: item for item in spec.get("node_labels", [])}
    statements: list[SchemaStatement] = []
    unique_pairs: set[tuple[str, str]] = set()

    for rule in spec.get("identity_rules", []):
        label = validate_identifier(rule["label"])
        property_name = validate_identifier(rule["id_property"])
        if label not in labels:
            raise BundleValidationError(f"Identity rule references unknown label: {label}")
        pair = (label, property_name)
        if pair in unique_pairs:
            continue
        unique_pairs.add(pair)
        name = validate_identifier(f"{_snake(label)}_{property_name}_unique")
        statements.append(
            SchemaStatement(
                name=name,
                kind="constraint",
                cypher=(
                    f"CREATE CONSTRAINT {name} IF NOT EXISTS\n"
                    f"FOR (n:{label})\n"
                    f"REQUIRE n.{property_name} IS UNIQUE"
                ),
            )
        )

    for label, property_name in INDEX_SPECS:
        validate_identifier(label)
        validate_identifier(property_name)
        if label not in labels:
            raise BundleValidationError(f"Index references unknown label: {label}")
        available = _all_property_names(label, labels)
        if property_name not in available:
            raise BundleValidationError(
                f"Index references unknown property: {label}.{property_name}"
            )
        if (label, property_name) in unique_pairs:
            continue
        name = validate_identifier(f"{_snake(label)}_{property_name}_idx")
        statements.append(
            SchemaStatement(
                name=name,
                kind="index",
                cypher=(
                    f"CREATE INDEX {name} IF NOT EXISTS\n"
                    f"FOR (n:{label})\n"
                    f"ON (n.{property_name})"
                ),
            )
        )
    return statements


def _all_property_names(label: str, labels: dict[str, dict[str, Any]]) -> set[str]:
    definition = labels[label]
    names = {item["name"] for item in definition.get("properties", [])}
    for parent in definition.get("parent_labels", []):
        names.update(_all_property_names(parent, labels))
    return names


def render_schema(spec: dict[str, Any]) -> str:
    statements = schema_statements(spec)
    header = (
        "// Generated from ontology/ontology_spec.json V0.2.\n"
        "// Re-runnable: every statement uses IF NOT EXISTS.\n\n"
    )
    return header + ";\n\n".join(item.cypher for item in statements) + ";\n"


def write_schema_file(spec: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(render_schema(spec), encoding="utf-8")


def apply_schema(session: Any, spec: dict[str, Any]) -> tuple[int, int]:
    statements = schema_statements(spec)
    for statement in statements:
        session.run(statement.cypher).consume()
    constraints = sum(item.kind == "constraint" for item in statements)
    indexes = sum(item.kind == "index" for item in statements)
    return constraints, indexes


def statement_names(statements: Iterable[SchemaStatement]) -> list[str]:
    return [statement.name for statement in statements]
