"""Safe, request-local graph projections for the inspection UI.

These projections never query Neo4j.  Query structure comes from the labels and
relationship types of an EXPLAIN-approved ``ValidatedCypher``.  Result provenance
comes only from ResultValidator-approved rows whose pairs exactly match the
ClaimValidator-approved Fact/Evidence provenance.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from kg_builder.query.schema_catalog import SchemaCatalog, SchemaCatalogError
from kg_builder.query.query_trace import EMAIL_PATTERN, PHONE_PATTERN, STUDENT_ID_PATTERN


GRAPH_ENVELOPE_VERSION = 1
MAX_GRAPH_NODES = 200
MAX_GRAPH_EDGES = 300
_SAFE_TYPE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,79}\Z")
_DISPLAY_FIELDS = (
    "name_ko",
    "course_name_ko",
    "raw_label",
    "credit_category",
    "aggregate_type",
    "description_ko",
)
_SENSITIVE_MARKERS = (
    "password",
    "token",
    "api key",
    "api_key",
    "secret",
    "bolt://",
    "neo4j://",
    "/home/",
)


def _opaque_id(key: bytes, namespace: str, value: str) -> str:
    digest = hmac.new(
        key,
        f"{namespace}\x1f{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:20]
    return f"ui:{namespace}:{digest}"


def _safe_display(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    compact = " ".join(value.split())
    lowered = compact.lower()
    if not compact or any(marker in lowered for marker in _SENSITIVE_MARKERS):
        return fallback
    compact = EMAIL_PATTERN.sub("<redacted-email>", compact)
    compact = PHONE_PATTERN.sub("<redacted-phone>", compact)
    compact = STUDENT_ID_PATTERN.sub("<redacted-student-id>", compact)
    return compact[:96]


def build_query_structure_projection(
    labels: Iterable[str],
    relationships: Iterable[str],
    *,
    opaque_key: bytes,
    kind: str = "QUERY_STRUCTURE",
    verification_status: str = "SCHEMA_APPROVED",
) -> dict[str, Any] | None:
    """Project only labels/relationships used by one EXPLAIN-approved query."""

    if kind not in {"SELECTED_SCHEMA", "QUERY_STRUCTURE"} or verification_status not in {
        "SCHEMA_SELECTED",
        "SCHEMA_APPROVED",
    }:
        return None

    safe_labels = sorted(
        {
            item
            for item in labels
            if isinstance(item, str) and _SAFE_TYPE.fullmatch(item)
        }
    )
    safe_relationships = sorted(
        {
            item
            for item in relationships
            if isinstance(item, str) and _SAFE_TYPE.fullmatch(item)
        }
    )
    if not safe_labels:
        return None
    safe_labels = safe_labels[:MAX_GRAPH_NODES]
    try:
        catalog = SchemaCatalog.from_generated()
    except (OSError, ValueError, SchemaCatalogError):
        return None

    node_ids = {
        label: _opaque_id(opaque_key, "query-node", label) for label in safe_labels
    }
    nodes = [
        {
            "id": node_ids[label],
            "display_name": label,
            "node_type": label,
            "verification_status": verification_status,
        }
        for label in safe_labels
    ]
    included = set(node_ids)
    edges: list[dict[str, Any]] = []
    for relationship in safe_relationships:
        definition = catalog.relationships.get(relationship)
        if definition is None:
            continue
        sources = sorted(definition.from_labels & included)
        targets = sorted(definition.to_labels & included)
        for source in sources:
            for target in targets:
                raw_edge = f"{source}\x1f{relationship}\x1f{target}"
                edges.append(
                    {
                        "id": _opaque_id(opaque_key, "query-edge", raw_edge),
                        "source": node_ids[source],
                        "target": node_ids[target],
                        "relationship": relationship,
                    }
                )
                if len(edges) >= MAX_GRAPH_EDGES:
                    break
            if len(edges) >= MAX_GRAPH_EDGES:
                break
        if len(edges) >= MAX_GRAPH_EDGES:
            break
    return {
        "version": GRAPH_ENVELOPE_VERSION,
        "kind": kind,
        "nodes": nodes,
        "edges": edges,
    }


def build_selected_schema_projection(
    labels: Iterable[str],
    relationships: Iterable[str],
    *,
    opaque_key: bytes,
) -> dict[str, Any] | None:
    """Project only the schema members selected for the validated QueryPlan."""

    return build_query_structure_projection(
        labels,
        relationships,
        opaque_key=opaque_key,
        kind="SELECTED_SCHEMA",
        verification_status="SCHEMA_SELECTED",
    )


def _fact_display(row: Mapping[str, Any], fact_label: str) -> str:
    for field in _DISPLAY_FIELDS:
        if field in row:
            value = _safe_display(row.get(field), "")
            if value:
                return value
    return f"{fact_label} 결과"


def build_result_fact_projection(
    rows: Sequence[Mapping[str, Any]],
    *,
    opaque_key: bytes,
) -> dict[str, Any] | None:
    """Project VERIFIED Fact nodes only after ResultValidator approval."""

    if not rows:
        return None
    fact_rows: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        fact_id = row.get("fact_id")
        fact_label = row.get("fact_label")
        if (
            not isinstance(fact_id, str)
            or not fact_id
            or not isinstance(fact_label, str)
            or not _SAFE_TYPE.fullmatch(fact_label)
            or row.get("fact_status") != "VERIFIED"
            or row.get("evidence_verification_status") != "VERIFIED"
        ):
            return None
        fact_rows.setdefault(fact_id, row)
    if len(fact_rows) > MAX_GRAPH_NODES:
        return None
    nodes = []
    for raw_id, row in sorted(fact_rows.items()):
        fact_label = str(row["fact_label"])
        nodes.append(
            {
                "id": _opaque_id(opaque_key, "result-fact-node", raw_id),
                "display_name": _fact_display(row, fact_label),
                "node_type": fact_label,
                "verification_status": "VERIFIED",
            }
        )
    return {
        "version": GRAPH_ENVELOPE_VERSION,
        "kind": "RESULT_FACTS",
        "nodes": nodes,
        "edges": [],
    }


def build_provenance_projection(
    rows: Sequence[Mapping[str, Any]],
    approved_pairs: Iterable[tuple[str, str]],
    *,
    opaque_key: bytes,
) -> dict[str, Any] | None:
    """Project only VERIFIED direct Fact→Evidence pairs approved by validators."""

    approved = {
        (fact_id, evidence_id)
        for fact_id, evidence_id in approved_pairs
        if isinstance(fact_id, str)
        and fact_id
        and isinstance(evidence_id, str)
        and evidence_id
    }
    if not rows or not approved:
        return None

    row_pairs: set[tuple[str, str]] = set()
    fact_rows: dict[str, Mapping[str, Any]] = {}
    evidence_rows: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        fact_id = row.get("fact_id")
        evidence_id = row.get("evidence_id")
        fact_label = row.get("fact_label")
        if (
            not isinstance(fact_id, str)
            or not fact_id
            or not isinstance(evidence_id, str)
            or not evidence_id
            or not isinstance(fact_label, str)
            or not _SAFE_TYPE.fullmatch(fact_label)
            or row.get("fact_status") != "VERIFIED"
            or row.get("evidence_verification_status") != "VERIFIED"
        ):
            return None
        page = row.get("excerpt_page")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            return None
        row_pairs.add((fact_id, evidence_id))
        fact_rows.setdefault(fact_id, row)
        evidence_rows.setdefault(evidence_id, row)
    if row_pairs != approved:
        return None

    if len(fact_rows) + len(evidence_rows) > MAX_GRAPH_NODES:
        return None
    fact_ids = {
        raw_id: _opaque_id(opaque_key, "fact-node", raw_id)
        for raw_id in sorted(fact_rows)
    }
    evidence_ids = {
        raw_id: _opaque_id(opaque_key, "evidence-node", raw_id)
        for raw_id in sorted(evidence_rows)
    }
    nodes: list[dict[str, Any]] = []
    for raw_id, row in sorted(fact_rows.items()):
        fact_label = str(row["fact_label"])
        nodes.append(
            {
                "id": fact_ids[raw_id],
                "display_name": _fact_display(row, fact_label),
                "node_type": fact_label,
                "verification_status": "VERIFIED",
            }
        )
    for raw_id, row in sorted(evidence_rows.items()):
        page = int(row["excerpt_page"])
        nodes.append(
            {
                "id": evidence_ids[raw_id],
                "display_name": f"발췌 PDF {page}쪽",
                "node_type": "Evidence",
                "verification_status": "VERIFIED",
                "excerpt_page": page,
                "citation_used": True,
            }
        )
    edges = [
        {
            "id": _opaque_id(
                opaque_key, "provenance-edge", f"{fact_id}\x1f{evidence_id}"
            ),
            "source": fact_ids[fact_id],
            "target": evidence_ids[evidence_id],
            "relationship": "SUPPORTED_BY",
        }
        for fact_id, evidence_id in sorted(approved)
    ]
    if len(edges) > MAX_GRAPH_EDGES:
        return None
    return {
        "version": GRAPH_ENVELOPE_VERSION,
        "kind": "RESULT_PROVENANCE",
        "nodes": nodes,
        "edges": edges,
    }
