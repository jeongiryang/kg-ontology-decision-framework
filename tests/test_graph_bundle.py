from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kg_builder.config import ConfigurationError, Neo4jSettings
from kg_builder.graph_bundle import BundleValidationError, GraphBundle, validate_identifier
from kg_builder.neo4j_ingest import _node_query, _relationship_query
from kg_builder.neo4j_schema import render_schema, schema_statements


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "ontology/ontology_spec.json"
DATA = ROOT / "data/verified/2026/2026_curriculum_kg_data.json"
UNRESOLVED = ROOT / "data/verified/2026/2026_curriculum_unresolved.json"


class GraphBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = json.loads(DATA.read_text(encoding="utf-8"))

    def write_mutation(self, mutate) -> Path:
        data = copy.deepcopy(self.source)
        mutate(data)
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        with handle:
            json.dump(data, handle, ensure_ascii=False)
        return Path(handle.name)

    def assert_invalid(self, mutate, pattern: str) -> None:
        path = self.write_mutation(mutate)
        with self.assertRaisesRegex(BundleValidationError, pattern):
            GraphBundle.load(SPEC, path)

    def test_valid_bundle_and_endpoint_lookup(self) -> None:
        bundle = GraphBundle.load(SPEC, DATA)
        self.assertEqual(bundle.expected_counts, (1518, 3260, 511))
        course = bundle.node_lookup["course:cwnu:CDA0008"]
        self.assertEqual((course.match_label, course.id_property), ("Course", "course_id"))

    def test_unresolved_artifact_is_rejected(self) -> None:
        with self.assertRaisesRegex(BundleValidationError, "not ingestion inputs"):
            GraphBundle.load(SPEC, UNRESOLVED)

    def test_duplicate_node_id_is_rejected(self) -> None:
        def mutate(data):
            data["nodes"].append(copy.deepcopy(data["nodes"][0]))
            data["metadata"]["counts"]["nodes_total"] += 1

        self.assert_invalid(mutate, "Duplicate node id")

    def test_missing_endpoint_is_rejected(self) -> None:
        self.assert_invalid(
            lambda data: data["relationships"][0].update(from_id="missing:node"),
            "missing endpoint",
        )

    def test_undeclared_label_is_rejected(self) -> None:
        self.assert_invalid(
            lambda data: data["nodes"][0]["labels"].append("UnknownLabel"),
            "undeclared labels",
        )

    def test_undeclared_relationship_is_rejected(self) -> None:
        self.assert_invalid(
            lambda data: data["relationships"][0].update(type="UNKNOWN_REL"),
            "Undeclared relationship type",
        )

    def test_missing_required_property_is_rejected(self) -> None:
        self.assert_invalid(
            lambda data: data["nodes"][0]["properties"].pop("institution_id"),
            "wrapper id differs",
        )

    def test_invalid_controlled_vocabulary_is_rejected(self) -> None:
        offering_index = next(
            i for i, node in enumerate(self.source["nodes"]) if "CourseOffering" in node["labels"]
        )
        self.assert_invalid(
            lambda data: data["nodes"][offering_index]["properties"].update(
                completion_type="NOT_A_COMPLETION_TYPE"
            ),
            "violates completion_type",
        )

    def test_parallel_relationship_is_rejected(self) -> None:
        source_index = next(
            i
            for i, relationship in enumerate(self.source["relationships"])
            if relationship["type"] == "DEVELOPS_COMPETENCY"
        )

        def mutate(data):
            duplicate = copy.deepcopy(data["relationships"][source_index])
            duplicate["properties"]["role"] = (
                "SECONDARY" if duplicate["properties"]["role"] == "PRIMARY" else "PRIMARY"
            )
            data["relationships"].append(duplicate)
            data["metadata"]["counts"]["relationships_total"] += 1

        self.assert_invalid(mutate, "Parallel relationships")

    def test_zero_is_preserved_and_null_is_not_coerced(self) -> None:
        bundle = GraphBundle.load(SPEC, DATA)
        offering = next(
            node
            for node in bundle.nodes
            if "CourseOffering" in node["labels"]
            and node["properties"].get("practice_hours") == 0
        )
        self.assertEqual(offering["properties"]["practice_hours"], 0)
        self.assertIsInstance(offering["properties"]["practice_hours"], int)

    def test_unsafe_identifier_is_rejected(self) -> None:
        with self.assertRaises(BundleValidationError):
            validate_identifier("Course`) MATCH (n) DETACH DELETE n //")
        with self.assertRaises(BundleValidationError):
            _node_query(("Course",), "course-id")
        with self.assertRaises(BundleValidationError):
            _relationship_query("OF COURSE", "CourseOffering", "offering_id", "Course", "course_id")

    def test_schema_file_matches_generated_schema(self) -> None:
        spec = json.loads(SPEC.read_text(encoding="utf-8"))
        statements = schema_statements(spec)
        self.assertEqual(sum(item.kind == "constraint" for item in statements), 22)
        self.assertEqual(sum(item.kind == "index" for item in statements), 7)
        self.assertEqual(
            (ROOT / "ontology/schema.cypher").read_text(encoding="utf-8"),
            render_schema(spec),
        )


class ConfigurationTests(unittest.TestCase):
    def test_missing_environment_fails_without_secret_value(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigurationError) as raised:
                Neo4jSettings.from_env(dotenv_path="/nonexistent/.env")
        message = str(raised.exception)
        self.assertIn("NEO4J_PASSWORD", message)
        self.assertNotIn("password=", message.lower())

    def test_remote_or_wrong_port_uri_is_rejected(self) -> None:
        for uri in ("neo4j://example.com:7687", "bolt://localhost:9999"):
            with self.subTest(uri=uri), patch.dict(
                os.environ,
                {
                    "NEO4J_URI": uri,
                    "NEO4J_USER": "local-user",
                    "NEO4J_PASSWORD": "local-secret",
                },
                clear=True,
            ):
                with self.assertRaises(ConfigurationError):
                    Neo4jSettings.from_env(dotenv_path="/nonexistent/.env")


if __name__ == "__main__":
    unittest.main()
