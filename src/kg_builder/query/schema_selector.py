"""Select a compact ontology-derived subgraph for one QueryPlan."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

from .fact_families import family_for_mode
from .query_plan import QueryPlan, resolve_filter_bindings
from .schema_catalog import DEFAULT_QUERY_SCHEMA_PATH, DEFAULT_SPEC_PATH, SchemaCatalog, sha256_file


class SchemaSelectionError(ValueError):
    pass


class QuerySchemaSelector:
    def __init__(
        self,
        generated_path: Path = DEFAULT_QUERY_SCHEMA_PATH,
        spec_path: Path = DEFAULT_SPEC_PATH,
    ):
        self.generated_path = generated_path
        self.spec_path = spec_path
        self.generated = json.loads(generated_path.read_text(encoding="utf-8"))
        if self.generated.get("source", {}).get("sha256") != sha256_file(spec_path):
            raise SchemaSelectionError("generated LLM query schema is stale")
        self.catalog = SchemaCatalog.from_generated(generated_path, spec_path)
        self.nodes = {item["label"]: item for item in self.generated["nodes"]}
        self.relationships = {item["type"]: item for item in self.generated["relationships"]}

    def select(self, plan: QueryPlan) -> dict[str, Any]:
        bindings = resolve_filter_bindings(plan.selection_mode)
        seeds = {bindings[name].label for name in plan.filters}
        requested = set(plan.requested_fields)
        # 확장 family 는 selection_mode 가 fact label 을 1:1로 확정한다. 확장 family 도
        # 학과 범위를 쓰기 때문에, 라벨 추론보다 먼저 판정해야 CourseOffering 으로
        # 잘못 흡수되지 않는다.
        family = family_for_mode(plan.selection_mode)
        if family is not None:
            fact_label = family.fact_label
            seeds.update({"CurriculumVersion", "Department", fact_label})
        elif seeds.intersection({"Course", "CourseOffering", "Department"}) or requested.intersection(
            {"course_code", "name_ko", "grade_year", "semester", "credits", "completion_type"}
        ):
            fact_label = "CourseOffering"
            seeds.update({"CurriculumVersion", "Department", "CourseOffering", "Course"})
        elif seeds.intersection({"Rule", "EducationArea", "ApplicabilityScope"}) or requested.intersection(
            {"rule_id", "rule_type", "operator", "value", "unit", "description_ko"}
        ):
            fact_label = "Rule"
            seeds.update({"CurriculumVersion", "Rule"})
        else:
            candidates = [
                label
                for label in self.generated["query_policy"]["provenance"]["fact_labels"]
                if requested.intersection(self.catalog.properties_for_labels({label}))
            ]
            if len(candidates) != 1:
                raise SchemaSelectionError("unable to select one evidence-backed fact family")
            fact_label = candidates[0]
            seeds.add(fact_label)
        seeds.add("Evidence")

        selected_nodes = set(seeds)
        selected_relationships: set[str] = set()
        seed_list = sorted(seeds)
        for index, source in enumerate(seed_list):
            for target in seed_list[index + 1 :]:
                path = self._shortest_path(source, target)
                if path is None:
                    continue
                nodes, relationships = path
                selected_nodes.update(nodes)
                selected_relationships.update(relationships)

        if "SUPPORTED_BY" not in selected_relationships:
            raise SchemaSelectionError("selected schema has no fact-Evidence path")
        node_items = [self._node_payload(label) for label in sorted(selected_nodes)]
        relation_items = [self.relationships[name] for name in sorted(selected_relationships)]
        return {
            "source": self.generated["source"],
            "selected_fact_family": fact_label,
            "nodes": node_items,
            "relationships": relation_items,
            "controlled_vocabularies": self._selected_vocabularies(node_items, relation_items),
            "query_policy": self.generated["query_policy"],
        }

    def _node_payload(self, label: str) -> dict[str, Any]:
        item = dict(self.nodes[label])
        effective = self.catalog.properties_for_labels({label})
        item["effective_property_names"] = sorted(effective)
        return item

    def _shortest_path(
        self, source: str, target: str
    ) -> tuple[list[str], list[str]] | None:
        queue: deque[tuple[str, list[str], list[str]]] = deque([(source, [source], [])])
        visited = {source}
        while queue:
            current, nodes, relationships = queue.popleft()
            if current == target:
                return nodes, relationships
            for neighbor, relationship in self._neighbors(current):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append((neighbor, nodes + [neighbor], relationships + [relationship]))
        return None

    def _neighbors(self, label: str) -> list[tuple[str, str]]:
        effective = self._labels_with_parents(label)
        found: set[tuple[str, str]] = set()
        for name, relation in self.relationships.items():
            from_labels = set(relation["from_labels"])
            to_labels = set(relation["to_labels"])
            if effective.intersection(from_labels):
                for candidate in self.nodes:
                    if self._labels_with_parents(candidate).intersection(to_labels):
                        found.add((candidate, name))
            if effective.intersection(to_labels):
                for candidate in self.nodes:
                    if self._labels_with_parents(candidate).intersection(from_labels):
                        found.add((candidate, name))
        return sorted(found)

    def _labels_with_parents(self, label: str) -> set[str]:
        found: set[str] = set()
        pending = [label]
        while pending:
            current = pending.pop()
            if current in found:
                continue
            found.add(current)
            pending.extend(self.catalog.nodes[current].parent_labels)
        return found

    def _selected_vocabularies(
        self, nodes: list[dict[str, Any]], relationships: list[dict[str, Any]]
    ) -> dict[str, Any]:
        names = {
            prop["controlled_vocabulary"]
            for item in nodes + relationships
            for prop in item.get("properties", [])
            if prop.get("controlled_vocabulary")
        }
        return {
            name: self.generated["controlled_vocabularies"][name]
            for name in sorted(names)
        }
