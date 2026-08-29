"""Promote already-extracted English-waiver conditions into atomic verified rules.

This is an idempotent data migration for the verified artifact only.  Values come from
the nine ``Condition`` nodes already present in the extracted JSON; no threshold is
invented by this script.  The raw artifact and PDF are never modified.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_PATH = Path("data/verified/2026/2026_curriculum_kg_data.json")
CURRICULUM_ID = "curriculum:cwnu:2026:common"
DOCUMENT_ID = "document:2026-curriculum-excerpt:8ee5ee9d45fd"

# These labels are transcriptions of the source table headings.  Threshold values are
# deliberately absent: they are always copied from existing Condition properties.
SOURCE_LABELS = {
    "TOEIC.score": ("TOEIC", "700점"),
    "TOEIC_SPEAKING.score": ("TOEIC Speaking", "Level 130점"),
    "TOEFL_IBT.score": ("TOEFL", "79(IBT)"),
    "TEPS.score": ("TEPS", "494"),
    "NEW_TEPS.score": ("New TEPS", "264"),
    "OPIC.grade": ("OPIc", "IM1(Intermediate Mid)"),
    "GTELP_LEVEL_2.score": ("G-TELP Level 2", "65"),
    "GTELP_LEVEL_3.score": ("G-TELP Level 3", "85"),
    "FLEX.score": ("FLEX", "630"),
}


def _relationship(kind: str, start: str, end: str) -> dict[str, Any]:
    return {"type": kind, "from_id": start, "to_id": end, "properties": {}}


def migrate(bundle: dict[str, Any]) -> dict[str, Any]:
    nodes = list(bundle["nodes"])
    relationships = list(bundle["relationships"])
    node_ids = {node["id"] for node in nodes}
    relation_keys = {
        (item["type"], item["from_id"], item["to_id"], json.dumps(item["properties"], sort_keys=True))
        for item in relationships
    }
    conditions = sorted(
        (
            node
            for node in nodes
            if "Condition" in node["labels"]
            and node["properties"].get("subject_field") in SOURCE_LABELS
        ),
        key=lambda node: node["properties"]["condition_id"],
    )
    if len(conditions) != len(SOURCE_LABELS):
        raise ValueError("the verified bundle does not contain all extracted waiver conditions")

    parent_id = "rule:cwnu:2026:general:college-english-waiver"
    parent = next((node for node in nodes if node["id"] == parent_id), None)
    if parent is None:
        raise ValueError("the extracted college-English waiver Rule is missing")
    parent["properties"]["status"] = "VERIFIED"
    parent["properties"]["description_ko"] = (
        "영어 공인시험 중 하나가 표의 해당 기준을 충족하면 대학영어Ⅰ·Ⅱ의 "
        "필수이수를 면제한다."
    )

    for condition in conditions:
        props = condition["properties"]
        subject = props["subject_field"]
        label, source_value = SOURCE_LABELS[subject]
        slug = props["condition_id"].rsplit(":", 1)[-1]
        rule_id = f"rule:cwnu:2026:general:college-english-waiver-threshold:{slug}"
        evidence_id = (
            "evidence:document:2026-curriculum-excerpt:8ee5ee9d45fd:"
            f"excerpt-p1:college-english-waiver-threshold:{slug}"
        )
        value = props["value"]
        unit = props.get("unit") or "POINT"
        value_text = f"{value}점" if unit == "POINT" else str(value)
        rule = {
            "id": rule_id,
            "labels": ["Rule", "CreditRequirement"],
            "properties": {
                "rule_id": rule_id,
                "rule_type": "CREDIT_REQUIREMENT",
                "operator": props["operator"],
                "value": value,
                "unit": unit,
                "status": "VERIFIED",
                "description_ko": f"대학영어 이수 면제 {label} 기준은 {value_text} 이상이다.",
                "source_value": f"{label} | {source_value}",
                "subject_field": subject,
            },
        }
        # ``subject_field`` is not a Rule property in ontology_spec, so keep it in
        # the description/source only.  It remains structured on the original
        # Condition node and is recovered by the stable slug in query policy.
        rule["properties"].pop("subject_field")
        evidence = {
            "id": evidence_id,
            "labels": ["Evidence"],
            "properties": {
                "evidence_id": evidence_id,
                "excerpt_page": 1,
                "source_pdf_page": 33,
                "printed_page": 25,
                "raw_text": f"{label} | {source_value}",
                "verification_status": "VERIFIED",
                "section_title": "2-1. 교양 이수학점",
                "table_name": "대학영어 I, II 교과목 이수 면제 요건",
                "row_key": f"college-english-waiver-threshold:{slug}",
            },
        }
        for node in (rule, evidence):
            if node["id"] not in node_ids:
                nodes.append(node)
                node_ids.add(node["id"])
        for relationship in (
            _relationship("HAS_RULE", CURRICULUM_ID, rule_id),
            _relationship("SUPPORTED_BY", rule_id, evidence_id),
            _relationship("FROM_DOCUMENT", evidence_id, DOCUMENT_ID),
        ):
            key = (
                relationship["type"],
                relationship["from_id"],
                relationship["to_id"],
                "{}",
            )
            if key not in relation_keys:
                relationships.append(relationship)
                relation_keys.add(key)

    counts = bundle["metadata"]["counts"]
    counts["nodes_total"] = len(nodes)
    counts["relationships_total"] = len(relationships)
    counts["nodes_by_label"] = dict(
        sorted(Counter(label for node in nodes for label in node["labels"]).items())
    )
    counts["relationships_by_type"] = dict(
        sorted(Counter(item["type"] for item in relationships).items())
    )
    bundle["nodes"] = nodes
    bundle["relationships"] = relationships
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument(
        "--source-ref",
        help="optionally rebuild from the bundle at this Git ref while writing --path",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check and args.source_ref:
        parser.error("--check and --source-ref cannot be used together")
    if args.source_ref:
        completed = subprocess.run(
            ["git", "show", f"{args.source_ref}:{args.path.as_posix()}"],
            check=True,
            capture_output=True,
            text=True,
        )
        original = json.loads(completed.stdout)
    else:
        original = json.loads(args.path.read_text(encoding="utf-8"))
    migrated = migrate(original)
    rendered = json.dumps(migrated, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        return 0 if rendered == args.path.read_text(encoding="utf-8") else 1
    args.path.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
