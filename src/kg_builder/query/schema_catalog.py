"""Ontology-derived catalog used by the dynamic query safety pipeline."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SPEC_PATH = ROOT / "ontology/ontology_spec.json"
DEFAULT_QUERY_SCHEMA_PATH = ROOT / "ontology/llm_query_schema.json"


class SchemaCatalogError(ValueError):
    """Raised when the ontology or generated query schema is missing or stale."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class NodeDefinition:
    label: str
    id_property: str
    properties: frozenset[str]
    required_properties: frozenset[str]
    parent_labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RelationshipDefinition:
    name: str
    from_labels: frozenset[str]
    to_labels: frozenset[str]
    properties: frozenset[str]


class SchemaCatalog:
    def __init__(self, source: dict[str, Any], *, source_sha256: str):
        self.source = source
        self.source_sha256 = source_sha256
        self.schema_name = source["schema_name"]
        self.schema_version = source["schema_version"]
        self.nodes = {
            item["name"]: NodeDefinition(
                label=item["name"],
                id_property=item["id_property"],
                properties=frozenset(prop["name"] for prop in item.get("properties", [])),
                required_properties=frozenset(item.get("required_properties", [])),
                parent_labels=tuple(item.get("parent_labels", [])),
            )
            for item in source["node_labels"]
        }
        self.relationships = {
            item["name"]: RelationshipDefinition(
                name=item["name"],
                from_labels=frozenset(item["from_labels"]),
                to_labels=frozenset(item["to_labels"]),
                properties=frozenset(prop["name"] for prop in item.get("properties", [])),
            )
            for item in source["relationship_types"]
        }
        self.controlled_vocabularies = {
            name: frozenset(entry["value"] for entry in body["values"])
            for name, body in source["controlled_vocabularies"].items()
        }
        self.property_types = {
            (item["name"], prop["name"]): prop["type"]
            for item in source["node_labels"]
            for prop in item.get("properties", [])
        }
        self.property_vocabularies = {
            (item["name"], prop["name"]): prop.get("controlled_vocabulary")
            for item in source["node_labels"]
            for prop in item.get("properties", [])
        }

    @classmethod
    def from_spec(cls, path: Path = DEFAULT_SPEC_PATH) -> "SchemaCatalog":
        source = json.loads(path.read_text(encoding="utf-8"))
        return cls(source, source_sha256=sha256_file(path))

    @classmethod
    def from_generated(
        cls,
        generated_path: Path = DEFAULT_QUERY_SCHEMA_PATH,
        spec_path: Path = DEFAULT_SPEC_PATH,
    ) -> "SchemaCatalog":
        generated = json.loads(generated_path.read_text(encoding="utf-8"))
        actual_hash = sha256_file(spec_path)
        recorded_hash = generated.get("source", {}).get("sha256")
        if recorded_hash != actual_hash:
            raise SchemaCatalogError(
                "generated LLM query schema is stale: ontology_spec.json SHA-256 differs"
            )
        source = json.loads(spec_path.read_text(encoding="utf-8"))
        if generated.get("source", {}).get("schema_version") != source.get("schema_version"):
            raise SchemaCatalogError("generated LLM query schema version differs from source")
        return cls(source, source_sha256=actual_hash)

    def properties_for_labels(self, labels: Iterable[str]) -> frozenset[str]:
        found: set[str] = set()
        pending = list(labels)
        visited: set[str] = set()
        while pending:
            label = pending.pop()
            if label in visited or label not in self.nodes:
                continue
            visited.add(label)
            definition = self.nodes[label]
            found.update(definition.properties)
            pending.extend(definition.parent_labels)
        return frozenset(found)

    @property
    def all_node_properties(self) -> frozenset[str]:
        return frozenset().union(*(node.properties for node in self.nodes.values()))

    def relevant_labels(self, property_names: Iterable[str], evidence_required: bool) -> list[str]:
        wanted = set(property_names)
        selected = {
            label
            for label, definition in self.nodes.items()
            if definition.properties.intersection(wanted)
        }
        if evidence_required:
            selected.add("Evidence")
        changed = True
        while changed:
            changed = False
            for relation in self.relationships.values():
                if selected.intersection(relation.from_labels | relation.to_labels):
                    expanded = relation.from_labels | relation.to_labels
                    if not expanded.issubset(selected):
                        selected.update(expanded)
                        changed = True
        return sorted(selected)
