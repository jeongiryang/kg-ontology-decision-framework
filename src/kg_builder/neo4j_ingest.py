"""CLI for safe, idempotent Neo4j ingestion of the verified KG bundle."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from .config import ConfigurationError, Neo4jSettings
from .graph_bundle import (
    DEFAULT_DATA_PATH,
    DEFAULT_SPEC_PATH,
    BundleValidationError,
    GraphBundle,
    batches,
    validate_identifier,
)
from .neo4j_schema import apply_schema, schema_statements


DEFAULT_BATCH_SIZE = 500


class IngestionError(RuntimeError):
    """Raised for database safety, load, or verification failures."""


@dataclass(frozen=True, slots=True)
class DatabaseCounts:
    nodes: int
    relationships: int
    evidence: int


@dataclass(frozen=True, slots=True)
class LoadStats:
    nodes_processed: int
    nodes_created: int
    nodes_matched: int
    relationships_processed: int
    relationships_created: int
    relationships_matched: int


def _labels_fragment(labels: Sequence[str]) -> str:
    return "".join(f":{validate_identifier(label)}" for label in labels)


def _node_query(labels: Sequence[str], id_property: str) -> str:
    label_fragment = _labels_fragment(labels)
    id_property = validate_identifier(id_property)
    return (
        "UNWIND $rows AS row\n"
        f"MERGE (n{label_fragment} {{{id_property}: row.id}})\n"
        "SET n += row.properties\n"
        "RETURN count(n) AS matched"
    )


def _relationship_query(
    relationship_type: str,
    start_label: str,
    start_id_property: str,
    end_label: str,
    end_id_property: str,
) -> str:
    relationship_type = validate_identifier(relationship_type)
    start_label = validate_identifier(start_label)
    start_id_property = validate_identifier(start_id_property)
    end_label = validate_identifier(end_label)
    end_id_property = validate_identifier(end_id_property)
    return (
        "UNWIND $rows AS row\n"
        f"MATCH (a:{start_label} {{{start_id_property}: row.from_id}})\n"
        f"MATCH (b:{end_label} {{{end_id_property}: row.to_id}})\n"
        f"MERGE (a)-[r:{relationship_type}]->(b)\n"
        "SET r += row.properties\n"
        "RETURN count(r) AS matched"
    )


class Neo4jIngestor:
    def __init__(self, driver: Any, database: str, bundle: GraphBundle, batch_size: int):
        self.driver = driver
        self.database = database
        self.bundle = bundle
        self.batch_size = batch_size

    def verify_connection(self) -> None:
        self.driver.verify_connectivity()
        with self.driver.session(database=self.database) as session:
            value = session.run("RETURN 1 AS connected").single(strict=True)["connected"]
        if value != 1:
            raise IngestionError("Neo4j connectivity probe returned an unexpected value")

    def server_version(self) -> str:
        with self.driver.session(database=self.database) as session:
            components = session.run(
                "CALL dbms.components() YIELD name, versions "
                "RETURN collect({name: name, version: versions[0]}) AS components"
            ).single(strict=True)["components"]
        kernel = next(
            (component for component in components if component["name"] == "Neo4j Kernel"),
            components[0],
        )
        return f"{kernel['name']} {kernel['version']}"

    def counts(self) -> DatabaseCounts:
        with self.driver.session(database=self.database) as session:
            row = session.run(
                "MATCH (n) WITH count(n) AS nodes "
                "OPTIONAL MATCH ()-[r]->() "
                "RETURN nodes, count(r) AS relationships"
            ).single(strict=True)
            evidence = session.run("MATCH (e:Evidence) RETURN count(e) AS count").single(
                strict=True
            )["count"]
        return DatabaseCounts(row["nodes"], row["relationships"], evidence)

    def require_empty_database(self) -> None:
        counts = self.counts()
        if counts.nodes or counts.relationships:
            with self.driver.session(database=self.database) as session:
                labels = session.run(
                    "MATCH (n) UNWIND labels(n) AS label "
                    "RETURN label, count(*) AS count ORDER BY count DESC, label LIMIT 10"
                ).data()
            summary = ", ".join(f"{row['label']}={row['count']}" for row in labels)
            raise IngestionError(
                "Neo4j database is not empty; refusing to load or delete existing data. "
                f"nodes={counts.nodes}, relationships={counts.relationships}, labels=[{summary}]"
            )

    def apply_schema(self) -> tuple[int, int]:
        with self.driver.session(database=self.database) as session:
            return apply_schema(session, self.bundle.spec)

    def load_once(self) -> LoadStats:
        nodes_processed = 0
        nodes_created = 0
        relationships_processed = 0
        relationships_created = 0

        def execute_batch(tx: Any, query: str, rows: list[dict[str, Any]]) -> tuple[int, Any]:
            result = tx.run(query, rows=rows)
            matched = result.single(strict=True)["matched"]
            summary = result.consume()
            return matched, summary.counters

        with self.driver.session(database=self.database) as session:
            for (labels, _match_label, id_property), rows in self.bundle.node_groups().items():
                query = _node_query(labels, id_property)
                for batch in batches(rows, self.batch_size):
                    matched, counters = session.execute_write(execute_batch, query, batch)
                    if matched != len(batch):
                        raise IngestionError(
                            f"Node batch matched {matched}, expected {len(batch)}"
                        )
                    nodes_processed += matched
                    nodes_created += counters.nodes_created

            for key, rows in self.bundle.relationship_groups().items():
                query = _relationship_query(*key)
                for batch in batches(rows, self.batch_size):
                    matched, counters = session.execute_write(execute_batch, query, batch)
                    if matched != len(batch):
                        raise IngestionError(
                            f"Relationship batch matched {matched}, expected {len(batch)}"
                        )
                    relationships_processed += matched
                    relationships_created += counters.relationships_created
        return LoadStats(
            nodes_processed=nodes_processed,
            nodes_created=nodes_created,
            nodes_matched=nodes_processed - nodes_created,
            relationships_processed=relationships_processed,
            relationships_created=relationships_created,
            relationships_matched=relationships_processed - relationships_created,
        )

    def verify_counts(self) -> DatabaseCounts:
        actual = self.counts()
        expected = DatabaseCounts(*self.bundle.expected_counts)
        if actual != expected:
            raise IngestionError(f"Database counts differ: expected={expected}, actual={actual}")
        return actual

    def verify_representative_facts(self) -> dict[str, Any]:
        checks: list[tuple[str, str, dict[str, Any], Any]] = [
            (
                "GEA8617 course",
                "MATCH (c:Course {course_code: $code}) RETURN c.name_ko AS value",
                {"code": "GEA8617"},
                "수식없는물리로보는세상",
            ),
            (
                "GEA8817 course",
                "MATCH (c:Course {course_code: $code}) RETURN c.name_ko AS value",
                {"code": "GEA8817"},
                "융합프로젝트Ⅰ",
            ),
            (
                "CSE required offerings",
                "MATCH (:CurriculumVersion {curriculum_id: $curriculum})-[:HAS_OFFERING]->"
                "(o:CourseOffering {completion_type: 'MAJOR_REQUIRED'}) "
                "RETURN [count(o), sum(o.credits)] AS value",
                {"curriculum": "curriculum:cwnu:2026:cse"},
                [9, 21],
            ),
            (
                "Data Structure term",
                "MATCH (o:CourseOffering)-[:OF_COURSE]->(c:Course {course_code: $code}) "
                "RETURN [o.grade_year, o.semester] AS value",
                {"code": "CDA0008"},
                [[2], "FIRST"],
            ),
            (
                "General minimum credits",
                "MATCH (r:Rule {rule_id: $id}) RETURN r.value AS value",
                {"id": "rule:cwnu:2026:general:min-total-default"},
                34,
            ),
            (
                "Balanced general minimum credits",
                "MATCH (r:Rule {rule_id: $id}) RETURN r.value AS value",
                {"id": "rule:cwnu:2026:general:balanced-min-credits"},
                12,
            ),
        ]
        results: dict[str, Any] = {}
        with self.driver.session(database=self.database) as session:
            for name, query, parameters, expected in checks:
                record = session.run(query, parameters).single(strict=True)
                actual = record["value"]
                if actual != expected:
                    raise IngestionError(
                        f"Representative check failed ({name}): expected={expected!r}, "
                        f"actual={actual!r}"
                    )
                results[name] = actual

            minor = session.run(
                "MATCH (o:CourseOffering)-[:OF_COURSE]->(c:Course) "
                "WHERE o.correction_note CONTAINS '부전공 필수' "
                "RETURN collect(c.name_ko) AS names"
            ).single(strict=True)["names"]
            if set(minor) != {"자료구조", "컴퓨터구조", "운영체제"}:
                raise IngestionError(f"Minor-required course check failed: {minor}")
            results["Minor required courses"] = sorted(minor)

            evidence_checks = session.run(
                "MATCH (c:Course {course_code: 'GEA8817'})<-[:CORRECTS]-"
                "(x:CorrectionRecord)-[:SUPPORTED_BY]->(e:Evidence) "
                "OPTIONAL MATCH (o:CourseOffering)-[:OF_COURSE]->(c) "
                "RETURN x.source_value AS source_value, x.corrected_value AS corrected_value, "
                "e.raw_text CONTAINS 'GEA8617' AS raw_preserved, count(o) AS offerings"
            ).single(strict=True)
            expected_correction = {
                "source_value": "GEA8617",
                "corrected_value": "GEA8817",
                "raw_preserved": True,
                "offerings": 0,
            }
            if dict(evidence_checks) != expected_correction:
                raise IngestionError(f"GEA8817 correction check failed: {dict(evidence_checks)}")
            results["GEA8817 correction"] = expected_correction

            gea8617 = session.run(
                "MATCH (o:CourseOffering {semester: 'SECOND', "
                "completion_type: 'GENERAL_ELECTIVE'})-[:OF_COURSE]->"
                "(c:Course {course_code: 'GEA8617'}) "
                "MATCH (o)-[:SUPPORTED_BY]->(e:Evidence {verification_status: 'VERIFIED'}) "
                "RETURN count(DISTINCT o) AS offerings, count(DISTINCT e) AS evidence"
            ).single(strict=True)
            if gea8617["offerings"] != 1 or gea8617["evidence"] < 1:
                raise IngestionError(f"GEA8617 offering/Evidence check failed: {dict(gea8617)}")
            results["GEA8617 offering Evidence"] = dict(gea8617)

            practice = session.run(
                "MATCH (x:CorrectionRecord)-[:CORRECTS]->(a:CurriculumAggregate) "
                "WHERE x.correction_type = 'AGGREGATE_VALUE' "
                "RETURN [x.source_value, x.corrected_value, a.source_value, a.normalized_value] "
                "AS value"
            ).single(strict=True)["value"]
            if practice[:2] != [12, 14]:
                raise IngestionError(f"Practice-hour correction check failed: {practice}")
            results["Practice total correction"] = practice

            evidence_rules = session.run(
                "MATCH (r:Rule) WHERE r.rule_id IN $ids "
                "MATCH (r)-[:SUPPORTED_BY]->(e:Evidence {verification_status: 'VERIFIED'}) "
                "RETURN count(DISTINCT r) AS count",
                ids=[
                    "rule:cwnu:2026:general:min-total-default",
                    "rule:cwnu:2026:general:balanced-min-credits",
                    "rule:cwnu:2026:general:transfer-exemption",
                ],
            ).single(strict=True)["count"]
            if evidence_rules != 3:
                raise IngestionError("General-rule Evidence check failed")
            results["General rule Evidence"] = evidence_rules
        return results


def _load_bundle(args: argparse.Namespace) -> GraphBundle:
    return GraphBundle.load(args.spec, args.data)


def _driver(settings: Neo4jSettings) -> Any:
    return GraphDatabase.driver(settings.uri, auth=(settings.user, settings.password))


def _database_command(args: argparse.Namespace) -> int:
    bundle = _load_bundle(args)
    settings = Neo4jSettings.from_env()
    print(f"Neo4j endpoint: {settings.endpoint}; database: {settings.database}")
    with _driver(settings) as driver:
        ingestor = Neo4jIngestor(driver, settings.database, bundle, args.batch_size)
        ingestor.verify_connection()
        print("Connection: OK")
        if args.command == "check-connection":
            print(f"Server: {ingestor.server_version()}")
            print(f"Current counts: {ingestor.counts()}")
            return 0
        if args.command == "apply-schema":
            constraints, indexes = ingestor.apply_schema()
            print(f"Schema applied: constraints={constraints}, indexes={indexes}")
            return 0
        if args.command == "verify":
            counts = ingestor.verify_counts()
            facts = ingestor.verify_representative_facts()
            print(f"Counts: {counts}")
            print(json.dumps(facts, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "load":
            ingestor.require_empty_database()
            constraints, indexes = ingestor.apply_schema()
            print(f"Schema applied: constraints={constraints}, indexes={indexes}")
            first_stats = ingestor.load_once()
            first = ingestor.verify_counts()
            ingestor.verify_representative_facts()
            print(f"First load: {first}; {first_stats}")
            second_stats = ingestor.load_once()
            second = ingestor.verify_counts()
            ingestor.verify_representative_facts()
            if first != second:
                raise IngestionError(f"Idempotency failed: first={first}, second={second}")
            print(f"Second load: {second}; {second_stats}; idempotency=PASS")
            return 0
    raise IngestionError(f"Unsupported command: {args.command}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "check-connection", "apply-schema", "load", "verify"))
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_PATH)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.batch_size <= 0:
        parser.error("--batch-size must be positive")
    try:
        bundle = _load_bundle(args)
        if args.command == "validate":
            nodes, relationships, evidence = bundle.expected_counts
            statements = schema_statements(bundle.spec)
            constraints = sum(item.kind == "constraint" for item in statements)
            indexes = sum(item.kind == "index" for item in statements)
            print(
                "Bundle validation: PASS; "
                f"nodes={nodes}, relationships={relationships}, Evidence={evidence}, "
                f"constraints={constraints}, indexes={indexes}"
            )
            return 0
        return _database_command(args)
    except (BundleValidationError, ConfigurationError, IngestionError, Neo4jError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
