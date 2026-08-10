"""Generate a deterministic LLM query schema from ontology_spec.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .schema_catalog import DEFAULT_QUERY_SCHEMA_PATH, DEFAULT_SPEC_PATH, sha256_file


SCHEMA_FORMAT_VERSION = "1.0.0"
MAX_RESULT_ROWS = 100


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
            "verified_fact_status": "VERIFIED",
            "verified_evidence_status": "VERIFIED",
            "evidence_relationship": "SUPPORTED_BY",
            "answer_requires_verified_evidence": True,
            "arbitrary_cypher_public_interface": False,
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
