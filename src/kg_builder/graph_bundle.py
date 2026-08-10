"""Load and validate an ontology spec and a verified graph bundle."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable


DEFAULT_SPEC_PATH = Path("ontology/ontology_spec.json")
DEFAULT_DATA_PATH = Path("data/verified/2026/2026_curriculum_kg_data.json")
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
SCALAR_TYPES = (str, int, float, bool)


class BundleValidationError(ValueError):
    """Raised when the schema or graph bundle violates the ingestion contract."""


def validate_identifier(value: str) -> str:
    if not SAFE_IDENTIFIER.fullmatch(value):
        raise BundleValidationError(f"Unsafe Cypher identifier: {value!r}")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleValidationError(f"Cannot parse JSON file {path}: {exc}") from exc
    if not isinstance(result, dict):
        raise BundleValidationError(f"JSON root must be an object: {path}")
    return result


def _neo4j_value(value: Any, *, context: str) -> Any:
    if value is None or isinstance(value, SCALAR_TYPES):
        return value
    if isinstance(value, list):
        if any(item is None or not isinstance(item, SCALAR_TYPES) for item in value):
            raise BundleValidationError(
                f"Neo4j list properties must contain non-null scalar values: {context}"
            )
        scalar_kinds = {type(item) for item in value}
        if len(scalar_kinds) > 1:
            raise BundleValidationError(
                f"Neo4j list properties must be homogeneous: {context}"
            )
        return list(value)
    raise BundleValidationError(
        f"Nested maps or unsupported property values are not loadable: {context}"
    )


def _matches_declared_type(value: Any, declared: str) -> bool:
    if value is None:
        return True
    if declared in {"string", "date"}:
        return isinstance(value, str)
    if declared == "boolean":
        return isinstance(value, bool)
    if declared == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if declared in {"scalar", "any_primitive"}:
        return isinstance(value, SCALAR_TYPES)
    if declared.startswith("array[") and declared.endswith("]"):
        if not isinstance(value, list):
            return False
        item_type = declared[6:-1]
        return all(_matches_declared_type(item, item_type) for item in value)
    return False


@dataclass(frozen=True, slots=True)
class NodeIdentity:
    node_id: str
    labels: tuple[str, ...]
    match_label: str
    id_property: str


@dataclass(slots=True)
class GraphBundle:
    spec_path: Path
    data_path: Path
    spec: dict[str, Any]
    data: dict[str, Any]
    nodes: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    node_lookup: dict[str, NodeIdentity]

    @classmethod
    def load(
        cls,
        spec_path: str | Path = DEFAULT_SPEC_PATH,
        data_path: str | Path = DEFAULT_DATA_PATH,
    ) -> "GraphBundle":
        spec_file = Path(spec_path)
        data_file = Path(data_path)
        spec = _read_json(spec_file)
        data = _read_json(data_file)
        validator = _BundleValidator(
            spec=spec, data=data, spec_path=spec_file, data_path=data_file
        )
        nodes, relationships, lookup = validator.validate()
        return cls(
            spec_path=spec_file,
            data_path=data_file,
            spec=spec,
            data=data,
            nodes=nodes,
            relationships=relationships,
            node_lookup=lookup,
        )

    @property
    def expected_counts(self) -> tuple[int, int, int]:
        evidence = sum("Evidence" in node["labels"] for node in self.nodes)
        return len(self.nodes), len(self.relationships), evidence

    def node_groups(self) -> dict[tuple[tuple[str, ...], str, str], list[dict[str, Any]]]:
        groups: dict[tuple[tuple[str, ...], str, str], list[dict[str, Any]]] = defaultdict(list)
        for node in self.nodes:
            identity = self.node_lookup[node["id"]]
            key = (identity.labels, identity.match_label, identity.id_property)
            groups[key].append({"id": node["id"], "properties": node["properties"]})
        return groups

    def relationship_groups(
        self,
    ) -> dict[tuple[str, str, str, str, str], list[dict[str, Any]]]:
        groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for relationship in self.relationships:
            start = self.node_lookup[relationship["from_id"]]
            end = self.node_lookup[relationship["to_id"]]
            key = (
                relationship["type"],
                start.match_label,
                start.id_property,
                end.match_label,
                end.id_property,
            )
            groups[key].append(
                {
                    "from_id": relationship["from_id"],
                    "to_id": relationship["to_id"],
                    "properties": relationship["properties"],
                }
            )
        return groups


class _BundleValidator:
    def __init__(
        self,
        *,
        spec: dict[str, Any],
        data: dict[str, Any],
        spec_path: Path,
        data_path: Path,
    ):
        self.spec = spec
        self.data = data
        self.spec_path = spec_path
        self.data_path = data_path
        self.labels = {item["name"]: item for item in spec.get("node_labels", [])}
        self.relationship_types = {
            item["name"]: item for item in spec.get("relationship_types", [])
        }
        self.identity_rules = {
            item["label"]: item["id_property"] for item in spec.get("identity_rules", [])
        }
        self.vocabularies = {
            name: {entry["value"] for entry in body.get("values", [])}
            for name, body in spec.get("controlled_vocabularies", {}).items()
        }

    def validate(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, NodeIdentity]]:
        self._validate_artifact()
        self._validate_spec()
        raw_nodes = self.data.get("nodes")
        raw_relationships = self.data.get("relationships")
        if not isinstance(raw_nodes, list) or not isinstance(raw_relationships, list):
            raise BundleValidationError("Bundle must contain nodes and relationships arrays")

        metadata_counts = self.data.get("metadata", {}).get("counts", {})
        if metadata_counts.get("nodes_total") != len(raw_nodes):
            raise BundleValidationError("metadata node count does not match nodes array")
        if metadata_counts.get("relationships_total") != len(raw_relationships):
            raise BundleValidationError(
                "metadata relationship count does not match relationships array"
            )

        nodes, lookup = self._validate_nodes(raw_nodes)
        relationships = self._validate_relationships(raw_relationships, lookup)
        self._validate_verified_evidence(nodes, relationships)
        return nodes, relationships, lookup

    def _validate_artifact(self) -> None:
        metadata = self.data.get("metadata")
        if not isinstance(metadata, dict):
            raise BundleValidationError("Verified bundle metadata is required")
        if metadata.get("format") != "node_relationship_bundle_v1":
            raise BundleValidationError(
                "Only node_relationship_bundle_v1 verified KG data can be loaded; "
                "unresolved review artifacts are not ingestion inputs"
            )
        role = str(metadata.get("artifact_role", "")).lower()
        if "verified" not in role:
            raise BundleValidationError("Input artifact is not marked as verified KG data")
        schema = metadata.get("schema", {})
        if schema.get("schema_name") != self.spec.get("schema_name"):
            raise BundleValidationError("Bundle and ontology schema names differ")
        if schema.get("schema_version") != self.spec.get("schema_version"):
            raise BundleValidationError("Bundle and ontology schema versions differ")
        declared_hash = schema.get("sha256")
        if declared_hash:
            try:
                spec_bytes = self.spec_path.read_bytes()
            except OSError as exc:
                raise BundleValidationError(
                    f"Cannot read ontology source for hash check: {exc}"
                ) from exc
            actual_hash = hashlib.sha256(spec_bytes).hexdigest()
            if declared_hash != actual_hash:
                raise BundleValidationError("Bundle references a different ontology file hash")
        if schema.get("source_file") != self.spec_path.name:
            raise BundleValidationError("Bundle ontology source_file does not match spec path")

    def _validate_spec(self) -> None:
        if not self.labels or not self.relationship_types or not self.identity_rules:
            raise BundleValidationError("Ontology must define labels, relationships and identities")
        for identifier in (*self.labels, *self.relationship_types):
            validate_identifier(identifier)
        for label, id_property in self.identity_rules.items():
            if label not in self.labels:
                raise BundleValidationError(f"Identity rule references unknown label: {label}")
            validate_identifier(id_property)

    @lru_cache(maxsize=None)
    def _contract(self, label: str) -> tuple[dict[str, dict[str, Any]], frozenset[str]]:
        definition = self.labels[label]
        properties = {item["name"]: item for item in definition.get("properties", [])}
        required = set(definition.get("required_properties", []))
        for parent in definition.get("parent_labels", []):
            if parent not in self.labels:
                raise BundleValidationError(f"Unknown parent label: {parent}")
            parent_properties, parent_required = self._contract(parent)
            properties = {**parent_properties, **properties}
            required.update(parent_required)
        return properties, frozenset(required)

    def _validate_nodes(
        self, raw_nodes: list[Any]
    ) -> tuple[list[dict[str, Any]], dict[str, NodeIdentity]]:
        nodes: list[dict[str, Any]] = []
        lookup: dict[str, NodeIdentity] = {}
        for index, raw in enumerate(raw_nodes):
            if not isinstance(raw, dict):
                raise BundleValidationError(f"Node {index} must be an object")
            node_id = raw.get("id")
            labels = raw.get("labels")
            properties = raw.get("properties")
            if not isinstance(node_id, str) or not node_id:
                raise BundleValidationError(f"Node {index} has no stable string id")
            if node_id in lookup:
                raise BundleValidationError(f"Duplicate node id: {node_id}")
            if not isinstance(labels, list) or not labels or not all(
                isinstance(label, str) and label in self.labels for label in labels
            ):
                raise BundleValidationError(f"Node {node_id} has undeclared labels")
            if len(labels) != len(set(labels)):
                raise BundleValidationError(f"Node {node_id} has duplicate labels")
            if not isinstance(properties, dict):
                raise BundleValidationError(f"Node {node_id} properties must be an object")

            allowed: dict[str, dict[str, Any]] = {}
            required: set[str] = set()
            identity_candidates: list[tuple[str, str]] = []
            for label in labels:
                contract, label_required = self._contract(label)
                allowed.update(contract)
                required.update(label_required)
                if label in self.identity_rules:
                    identity_candidates.append((label, self.identity_rules[label]))
            if not identity_candidates:
                raise BundleValidationError(f"Node {node_id} has no identity rule")
            distinct_id_properties = {item[1] for item in identity_candidates}
            if len(distinct_id_properties) != 1:
                raise BundleValidationError(f"Node {node_id} has ambiguous identity rules")
            match_label, id_property = identity_candidates[0]
            if properties.get(id_property) != node_id:
                raise BundleValidationError(
                    f"Node wrapper id differs from {id_property}: {node_id}"
                )
            undeclared = set(properties) - set(allowed)
            if undeclared:
                raise BundleValidationError(
                    f"Node {node_id} has undeclared properties: {sorted(undeclared)}"
                )
            missing = required - set(properties)
            if missing:
                raise BundleValidationError(
                    f"Node {node_id} misses required properties: {sorted(missing)}"
                )

            normalized: dict[str, Any] = {}
            for name, value in properties.items():
                definition = allowed[name]
                if value is None and not definition.get("nullable", False):
                    raise BundleValidationError(f"Node {node_id}.{name} cannot be null")
                if not _matches_declared_type(value, definition["type"]):
                    raise BundleValidationError(
                        f"Node {node_id}.{name} does not match declared type "
                        f"{definition['type']}"
                    )
                vocabulary = definition.get("controlled_vocabulary")
                values = value if isinstance(value, list) else [value]
                if vocabulary and value is not None:
                    if vocabulary not in self.vocabularies or any(
                        item not in self.vocabularies[vocabulary] for item in values
                    ):
                        raise BundleValidationError(
                            f"Node {node_id}.{name} violates {vocabulary}"
                        )
                normalized[name] = _neo4j_value(value, context=f"{node_id}.{name}")

            ordered_labels = tuple(dict.fromkeys(labels))
            lookup[node_id] = NodeIdentity(
                node_id=node_id,
                labels=ordered_labels,
                match_label=match_label,
                id_property=id_property,
            )
            nodes.append({"id": node_id, "labels": list(ordered_labels), "properties": normalized})
        return nodes, lookup

    def _validate_relationships(
        self, raw_relationships: list[Any], lookup: dict[str, NodeIdentity]
    ) -> list[dict[str, Any]]:
        relationships: list[dict[str, Any]] = []
        exact_keys: set[tuple[str, str, str, str]] = set()
        endpoint_keys: Counter[tuple[str, str, str]] = Counter()
        for index, raw in enumerate(raw_relationships):
            if not isinstance(raw, dict):
                raise BundleValidationError(f"Relationship {index} must be an object")
            rel_type = raw.get("type")
            from_id = raw.get("from_id")
            to_id = raw.get("to_id")
            properties = raw.get("properties", {})
            if rel_type not in self.relationship_types:
                raise BundleValidationError(f"Undeclared relationship type: {rel_type}")
            if from_id not in lookup or to_id not in lookup:
                raise BundleValidationError(
                    f"Relationship {rel_type} has a missing endpoint: {from_id} -> {to_id}"
                )
            if not isinstance(properties, dict):
                raise BundleValidationError(f"Relationship {rel_type} properties must be an object")
            definition = self.relationship_types[rel_type]
            if not set(lookup[from_id].labels).intersection(definition["from_labels"]):
                raise BundleValidationError(f"Invalid start label for {rel_type}: {from_id}")
            if not set(lookup[to_id].labels).intersection(definition["to_labels"]):
                raise BundleValidationError(f"Invalid end label for {rel_type}: {to_id}")
            property_contract = {
                item["name"]: item for item in definition.get("properties", [])
            }
            undeclared = set(properties) - set(property_contract)
            if undeclared:
                raise BundleValidationError(
                    f"Relationship {rel_type} has undeclared properties: {sorted(undeclared)}"
                )
            missing = set(definition.get("required_properties", [])) - set(properties)
            if missing:
                raise BundleValidationError(
                    f"Relationship {rel_type} misses properties: {sorted(missing)}"
                )
            normalized: dict[str, Any] = {}
            for name, value in properties.items():
                if not _matches_declared_type(value, property_contract[name]["type"]):
                    raise BundleValidationError(
                        f"Relationship {rel_type}.{name} does not match declared type "
                        f"{property_contract[name]['type']}"
                    )
                vocabulary = property_contract[name].get("controlled_vocabulary")
                if vocabulary and value not in self.vocabularies.get(vocabulary, set()):
                    raise BundleValidationError(
                        f"Relationship {rel_type}.{name} violates {vocabulary}"
                    )
                normalized[name] = _neo4j_value(
                    value, context=f"{rel_type}({from_id},{to_id}).{name}"
                )

            serialized = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
            exact_key = (rel_type, from_id, to_id, serialized)
            if exact_key in exact_keys:
                raise BundleValidationError(f"Duplicate relationship: {exact_key}")
            exact_keys.add(exact_key)
            endpoint_keys[(rel_type, from_id, to_id)] += 1
            relationships.append(
                {
                    "type": rel_type,
                    "from_id": from_id,
                    "to_id": to_id,
                    "properties": normalized,
                }
            )

        parallel = [key for key, count in endpoint_keys.items() if count > 1]
        if parallel:
            raise BundleValidationError(
                "Parallel relationships with identical type/endpoints cannot be losslessly "
                f"MERGEd: {parallel[:5]}"
            )
        return relationships

    def _validate_verified_evidence(
        self, nodes: list[dict[str, Any]], relationships: list[dict[str, Any]]
    ) -> None:
        verified_evidence = {
            node["id"]
            for node in nodes
            if "Evidence" in node["labels"]
            and node["properties"].get("verification_status") == "VERIFIED"
        }
        supported = {
            relationship["from_id"]
            for relationship in relationships
            if relationship["type"] == "SUPPORTED_BY"
            and relationship["to_id"] in verified_evidence
        }
        source_labels = {
            "Rule",
            "CourseOffering",
            "TalentProfile",
            "EducationGoal",
            "CareerField",
            "Competency",
            "Alignment",
            "RoadmapEntry",
            "CourseRecommendation",
            "CurriculumAggregate",
            "CreditAllocation",
            "CorrectionRecord",
        }
        missing = []
        for node in nodes:
            status = node["properties"].get("status") or node["properties"].get(
                "verification_status"
            )
            if status == "VERIFIED" and set(node["labels"]).intersection(source_labels):
                if node["id"] not in supported:
                    missing.append(node["id"])
        if missing:
            raise BundleValidationError(
                "VERIFIED source nodes lack VERIFIED Evidence: " + ", ".join(missing[:10])
            )


def batches(items: Iterable[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    if size <= 0:
        raise ValueError("batch size must be positive")
    batch: list[dict[str, Any]] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch
