"""Generate a deterministic LLM query schema from ontology_spec.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .query_plan import FILTER_BINDINGS
from .result_limits import ABSOLUTE_MAX_RESULT_ROWS, DEFAULT_MAX_RESULT_ROWS
from .schema_catalog import DEFAULT_QUERY_SCHEMA_PATH, DEFAULT_SPEC_PATH, sha256_file


SCHEMA_FORMAT_VERSION = "1.0.0"
MAX_RESULT_ROWS = ABSOLUTE_MAX_RESULT_ROWS


def build_query_schema(spec_path: Path = DEFAULT_SPEC_PATH) -> dict[str, Any]:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    nodes = []
    for item in sorted(spec["node_labels"], key=lambda value: value["name"]):
        nodes.append(
            {
                "label": item["name"],
                "name_ko": item["name_ko"],
                "description": item["description"],
                "id_property": item["id_property"],
                "parent_labels": sorted(item.get("parent_labels", [])),
                "required_properties": sorted(item.get("required_properties", [])),
                "properties": sorted(
                    [
                        {
                            "name": prop["name"],
                            "type": prop["type"],
                            "required": prop.get("required", False),
                            "nullable": prop.get("nullable", not prop.get("required", False)),
                            "controlled_vocabulary": prop.get("controlled_vocabulary"),
                            "description_ko": prop["description_ko"],
                        }
                        for prop in item.get("properties", [])
                    ],
                    key=lambda prop: prop["name"],
                ),
            }
        )
    relationships = []
    for item in sorted(spec["relationship_types"], key=lambda value: value["name"]):
        relationships.append(
            {
                "type": item["name"],
                "name_ko": item["name_ko"],
                "description": item["description"],
                "from_labels": sorted(item["from_labels"]),
                "to_labels": sorted(item["to_labels"]),
                "required_properties": sorted(item.get("required_properties", [])),
                "properties": sorted(
                    [
                        {
                            "name": prop["name"],
                            "type": prop["type"],
                            "required": prop.get("required", False),
                            "nullable": prop.get("nullable", not prop.get("required", False)),
                            "controlled_vocabulary": prop.get("controlled_vocabulary"),
                            "description_ko": prop["description_ko"],
                        }
                        for prop in item.get("properties", [])
                    ],
                    key=lambda prop: prop["name"],
                ),
            }
        )
    vocabularies = {
        name: sorted(
            [
                {
                    "value": entry["value"],
                    "classification": entry["classification"],
                    "description_ko": entry["description_ko"],
                }
                for entry in body["values"]
            ],
            key=lambda entry: entry["value"],
        )
        for name, body in sorted(spec["controlled_vocabularies"].items())
    }
    nodes_by_name = {item["name"]: item for item in spec["node_labels"]}
    for name, binding in FILTER_BINDINGS.items():
        properties = {prop["name"] for prop in nodes_by_name[binding.label]["properties"]}
        if binding.property_name not in properties:
            raise ValueError(
                f"query filter {name} refers to undeclared {binding.label}.{binding.property_name}"
            )
    supported_by = next(
        item for item in spec["relationship_types"] if item["name"] == "SUPPORTED_BY"
    )

    def properties_with_parents(label: str) -> set[str]:
        found: set[str] = set()
        pending = [label]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            definition = nodes_by_name[current]
            found.update(prop["name"] for prop in definition.get("properties", []))
            pending.extend(definition.get("parent_labels", []))
        return found

    def labels_with_parents(label: str) -> set[str]:
        found: set[str] = set()
        pending = [label]
        while pending:
            current = pending.pop()
            if current in found:
                continue
            found.add(current)
            pending.extend(nodes_by_name[current].get("parent_labels", []))
        return found

    supported_fact_labels = sorted(
        label
        for label in nodes_by_name
        if "status" in properties_with_parents(label)
        and labels_with_parents(label).intersection(supported_by["from_labels"])
    )
    return {
        "schema_format_version": SCHEMA_FORMAT_VERSION,
        "artifact_role": "LLM_QUERY_SCHEMA",
        "source": {
            "path": "ontology/ontology_spec.json",
            "schema_name": spec["schema_name"],
            "schema_version": spec["schema_version"],
            "sha256": sha256_file(spec_path),
        },
        "target_graph_model": spec["target_graph_model"],
        "nodes": nodes,
        "relationships": relationships,
        "controlled_vocabularies": vocabularies,
        "query_policy": {
            "read_only": True,
            "maximum_result_rows": MAX_RESULT_ROWS,
            "default_maximum_result_rows": DEFAULT_MAX_RESULT_ROWS,
            "verified_fact_status": "VERIFIED",
            "verified_evidence_status": "VERIFIED",
            "evidence_relationship": "SUPPORTED_BY",
            "answer_requires_verified_evidence": True,
            "arbitrary_cypher_public_interface": False,
            "filter_bindings": {
                name: {
                    "label": binding.label,
                    "property": binding.property_name,
                    "operator": binding.operator,
                }
                for name, binding in sorted(FILTER_BINDINGS.items())
            },
            "provenance": {
                "relationship": "SUPPORTED_BY",
                "fact_labels": supported_fact_labels,
                "evidence_label": "Evidence",
                "direct_path_required": True,
                "fact_id_required": True,
                "course_identity_is_not_a_fact": True,
                "course_schedule_fact_label": "CourseOffering",
            },
            "cypher_generation_constraints": {
                "where_predicates_must_not_be_parenthesized": True,
                "all_user_filters_must_use_parameters": True,
                "literal_limit_maximum": MAX_RESULT_ROWS,
                "direct_verified_fact_evidence_path_required": True,
            },
        },
    }


def render_query_schema(spec_path: Path = DEFAULT_SPEC_PATH) -> str:
    return json.dumps(build_query_schema(spec_path), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_query_schema(
    spec_path: Path = DEFAULT_SPEC_PATH,
    output_path: Path = DEFAULT_QUERY_SCHEMA_PATH,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_query_schema(spec_path), encoding="utf-8")


def check_query_schema(
    spec_path: Path = DEFAULT_SPEC_PATH,
    output_path: Path = DEFAULT_QUERY_SCHEMA_PATH,
) -> bool:
    return output_path.exists() and output_path.read_text(encoding="utf-8") == render_query_schema(
        spec_path
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "check"))
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_QUERY_SCHEMA_PATH)
    args = parser.parse_args(argv)
    if args.command == "generate":
        write_query_schema(args.spec, args.output)
        print(f"generated {args.output}")
        return 0
    if not check_query_schema(args.spec, args.output):
        print("generated query schema is missing or stale")
        return 1
    print("generated query schema matches ontology_spec.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
