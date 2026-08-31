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

from kg_builder.query.fact_families import BASE_FILTER_BINDINGS
from kg_builder.query.schema_catalog import SchemaCatalog, SchemaCatalogError
from kg_builder.query.query_trace import EMAIL_PATTERN, PHONE_PATTERN, STUDENT_ID_PATTERN


GRAPH_ENVELOPE_VERSION = 1
MAX_GRAPH_NODES = 650
MAX_GRAPH_EDGES = 800
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
# The generated schema currently supplies Korean names for every public label and
# relationship.  These fallbacks are deliberately generic Korean phrases rather
# than internal identifiers: an incomplete schema must not put an English Neo4j
# label into the general-user graph UI.
_SAFE_NODE_TYPE_FALLBACK = "확인된 그래프 항목"
_SAFE_RELATIONSHIP_FALLBACK = "확인된 관계"


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


def _node_type_ko(catalog: SchemaCatalog, label: str) -> str:
    """Return an ontology Korean type name safe for the public graph."""

    value = catalog.label_ko(label)
    return value if value and value != label else _SAFE_NODE_TYPE_FALLBACK


def _relationship_ko(catalog: SchemaCatalog, relationship: str) -> str:
    """Return a Korean relationship name without leaking an internal type."""

    value = catalog.relationship_ko(relationship)
    return value if value and value != relationship else _SAFE_RELATIONSHIP_FALLBACK


def build_query_structure_projection(
    labels: Iterable[str],
    relationships: Iterable[str],
    *,
    opaque_key: bytes,
    path_edges: Sequence[Mapping[str, Any]] = (),
    parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Project the approved MATCH path in the order the query actually walks it.

    종전에는 라벨·관계 이름 집합만 받아 온톨로지가 허용하는 끝점의 **교차곱**으로
    간선을 그렸다. 그래서 질의가 밟지 않은 간선이 나타나고, 같은 관계 타입을 두 번
    타는 경로는 하나로 뭉개졌다. 화면에 뜬 것은 경로가 아니라 스키마 도식이었다.

    이제 검증기가 승인한 hop 목록을 순서대로 받아 그대로 그린다. `order` 는 질의가
    쓴 순서이며 Neo4j 내부 실행 순서가 아니다. 승인된 경로를 받지 못하면 라벨만
    표시하고 간선은 만들지 않는다.
    """

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
    try:
        catalog = SchemaCatalog.from_generated()
    except (OSError, ValueError, SchemaCatalogError):
        return None
    safe_labels = [label for label in safe_labels if label in catalog.nodes]
    safe_relationships = [
        relationship
        for relationship in safe_relationships
        if relationship in catalog.relationships
    ]
    if not safe_labels:
        return None

    steps = _approved_path_steps(
        path_edges,
        set(safe_labels),
        set(safe_relationships),
    )
    # A multi-node query needs its validator-approved hops.  Showing all selected
    # labels without those hops would turn a schema candidate set into a made-up
    # query graph.  A single label is the one safe zero-hop query shape.
    if path_edges and not steps:
        return None
    if not _path_matches_catalog(steps, catalog):
        return None
    if not steps and len(safe_labels) != 1:
        return None
    path_labels = (
        {
            label
            for step in steps
            for label in (step["start_label"], step["end_label"])
        }
        if steps
        else set(safe_labels)
    )
    if len(path_labels) > MAX_GRAPH_NODES or len(steps) > MAX_GRAPH_EDGES:
        return None
    visible_labels = sorted(path_labels)
    node_ids = {
        label: _opaque_id(opaque_key, "query-node", label) for label in visible_labels
    }
    nodes = [
        {
            "id": node_ids[label],
            "display_name": _scoped_label(catalog, label, parameters or {}),
            "node_type": label,
            "node_type_ko": _node_type_ko(catalog, label),
            "verification_status": "SCHEMA_APPROVED",
            # 경로에 등장하는 순서. 등장하지 않는 라벨은 null 로 두고 화면이
            # 흐리게 그린다.
            "visit_order": next(
                (
                    index
                    for index, step in enumerate(steps, start=1)
                    if label == step["start_label"]
                ),
                None,
            ),
        }
        for label in visible_labels
    ]

    edges: list[dict[str, Any]] = []
    if steps:
        for step in steps[:MAX_GRAPH_EDGES]:
            source, target = step["start_label"], step["end_label"]
            relationship = step["relationship_type"]
            raw_edge = f"{step['order']}\x1f{source}\x1f{relationship}\x1f{target}"
            edges.append(
                {
                    "id": _opaque_id(opaque_key, "query-edge", raw_edge),
                    "source": node_ids[source],
                    "target": node_ids[target],
                    "relationship": relationship,
                    "relationship_ko": _relationship_ko(catalog, relationship),
                    "traversal_order": step["order"],
                }
            )
    # 승인된 path가 없을 때 ontology endpoint의 교차곱으로 간선을 만들어 내지 않는다.
    _assign_visit_order(nodes, edges, steps)
    return {
        "version": GRAPH_ENVELOPE_VERSION,
        "kind": "QUERY_STRUCTURE",
        # 경로를 받은 경우에만 순서 표기가 의미를 갖는다. 화면이 이 값으로
        # 애니메이션 재생 여부를 정한다.
        "ordered": bool(edges and steps),
        "nodes": nodes,
        "edges": edges,
    }


def build_traversal_projection(
    path_edges: Sequence[Mapping[str, Any]],
    parameters: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    approved_pairs: Iterable[tuple[str, str]],
    *,
    opaque_key: bytes,
    traversal_steps: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any] | None:
    """One picture: the approved path from its root down to the real matched facts.

    종전에는 스키마 경로와 조회 결과를 별개의 그림 두 장으로 그렸다. 그래서 "루트에서
    출발해 어떤 노드까지 갔는가"를 한눈에 볼 수 없었다. 여기서는 승인된 경로의 앞부분
    (조회 범위를 좁히는 노드들)을 그대로 두고, 마지막 fact 자리와 Evidence 자리를
    **실제로 매칭된 노드 인스턴스**로 펼쳐 하나의 트리로 만든다.

    상단 범위 노드는 질의 파라미터에서, 하단 인스턴스는 ResultValidator 가 승인한 행과
    ClaimValidator 가 승인한 provenance 쌍에서만 나온다. 어느 쪽도 지어내지 않는다.
    """

    approved = _approved_pairs(approved_pairs)
    if not path_edges or not approved:
        return None
    try:
        catalog = SchemaCatalog.from_generated()
    except (OSError, ValueError, SchemaCatalogError):
        return None
    steps = _approved_path_steps(
        path_edges,
        set(catalog.nodes),
        set(catalog.relationships),
    )
    if not steps or not _path_matches_catalog(steps, catalog):
        return None
    # 엔진이 실제로 밟은 단계를 관계 타입으로 묶는다. 같은 관계를 여러 번 타면
    # 실행 순서대로 소진한다.
    measured: dict[str, list[Mapping[str, Any]]] = {}
    for item in traversal_steps or ():
        if not isinstance(item, Mapping):
            continue
        rel = item.get("relationship_type")
        if isinstance(rel, str) and rel:
            measured.setdefault(rel, []).append(item)

    def take_measure(relationship: str) -> dict[str, Any]:
        bucket = measured.get(relationship) or []
        item = bucket.pop(0) if bucket else None
        if not item:
            return {}
        return {
            "rows": item.get("rows"),
            "db_hits": item.get("db_hits"),
            "operator": item.get("operator"),
        }
    verified_rows = _verified_projection_rows(rows, approved)
    if verified_rows is None:
        return None
    fact_rows, evidence_rows = verified_rows
    fact_label = next(iter(fact_rows.values())).get("fact_label")
    if not isinstance(fact_label, str) or not _SAFE_TYPE.fullmatch(fact_label):
        return None
    if any(row.get("fact_label") != fact_label for row in fact_rows.values()):
        return None

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    scope_ids: dict[str, str] = {}
    order = 0

    # 질의가 뿌리 노드의 실제 이름을 돌려줬으면 그것을 쓴다. 없으면 라벨 한국어명에
    # 파라미터를 붙인 이름으로 물러난다.
    scope_identity = next(
        (
            row.get("scope_identity")
            for row in rows
            if isinstance(row.get("scope_identity"), str) and row.get("scope_identity")
        ),
        None,
    )
    root_label = steps[0].get("start_label") if steps else None

    def scope_node(label: str) -> str:
        nonlocal order
        if label in scope_ids:
            return scope_ids[label]
        order += 1
        node_id = _opaque_id(opaque_key, "scope-node", label)
        scope_ids[label] = node_id
        real_name = (
            _safe_display(scope_identity, "")
            if scope_identity and label == root_label
            else ""
        )
        nodes.append({
            "id": node_id,
            "display_name": real_name or _scoped_label(catalog, label, parameters),
            "node_type": label,
            "node_type_ko": _node_type_ko(catalog, label),
            "verification_status": "SCHEMA_APPROVED",
            "visit_order": order,
        })
        return node_id

    # 1) fact/Evidence 이전까지의 범위 경로를 그대로 세운다.
    hop = 0
    for step in steps:
        start, end = step.get("start_label"), step.get("end_label")
        if not isinstance(start, str) or not isinstance(end, str):
            return None
        # fact 라벨이나 Evidence 를 **양끝 어디로든** 건드리는 hop 은 스코프 척추에
        # 넣지 않는다. 그 자리는 아래에서 실제 인스턴스로 펼치기 때문이다. 종전에는
        # 들어오는 hop 만 걸러서, fact 라벨에서 나가는 hop(OF_COURSE 등)이 스코프
        # 노드를 하나 더 만들어 실제로 없는 노드가 그려졌다(2026-08-29 실측).
        if {start, end} & {fact_label, "Evidence"}:
            continue
        hop += 1
        edges.append({
            "id": _opaque_id(opaque_key, "scope-edge", f"{hop}\x1f{start}\x1f{end}"),
            "source": scope_node(start),
            "target": scope_node(end),
            "relationship": step.get("relationship_type", ""),
            "relationship_ko": _relationship_ko(
                catalog, step.get("relationship_type", "")
            ),
            "traversal_order": hop,
            **take_measure(step.get("relationship_type", "")),
        })
    # Fact가 경로의 루트인 경우에는 type-level scope node를 다시 만들지 않는다.
    # 그 노드는 실제 ResultValidator 승인 Fact 인스턴스와 동일한 자리를 가리켜 ghost
    # node가 되기 때문이다. 실제 상위 scope가 있을 때만 Fact branch의 부모로 둔다.
    parent_label = next(
        (
            s.get("start_label")
            for s in steps
            if s.get("end_label") == fact_label
            and s.get("start_label") != fact_label
        ),
        None,
    )
    parent_id = scope_node(parent_label) if isinstance(parent_label, str) else None
    into_fact = next(
        (
            s
            for s in steps
            if s.get("end_label") == fact_label
            and s.get("start_label") != fact_label
        ),
        None,
    )

    # 2) 실제 매칭된 fact 인스턴스
    if len(fact_rows) + len(evidence_rows) + len(nodes) > MAX_GRAPH_NODES:
        return None

    fact_ids: dict[str, str] = {}
    # 같은 관계를 행마다 반복해 그리므로 실측치는 한 번만 읽어 공유한다.
    fact_measure = (
        take_measure(into_fact.get("relationship_type", "")) if into_fact else {}
    )
    for raw_id, row in sorted(fact_rows.items()):
        order += 1
        node_id = _opaque_id(opaque_key, "fact-node", raw_id)
        fact_ids[raw_id] = node_id
        nodes.append({
            "id": node_id,
            "display_name": _fact_display(
                row, fact_label, _node_type_ko(catalog, fact_label)
            ),
            "node_type": fact_label,
            "node_type_ko": _node_type_ko(catalog, fact_label),
            "verification_status": "VERIFIED",
            "visit_order": order,
            "group_name": _safe_display(row.get("area_name"), "") or None,
        })
        if parent_id and into_fact:
            hop += 1
            edges.append({
                "id": _opaque_id(opaque_key, "fact-edge", f"{parent_label}\x1f{raw_id}"),
                "source": parent_id,
                "target": node_id,
                "relationship": into_fact.get("relationship_type", ""),
                "relationship_ko": _relationship_ko(
                    catalog,
                    into_fact.get("relationship_type", "")
                ),
                "traversal_order": hop,
                **fact_measure,
            })

    # 2-b) fact 에서 나가는 이웃 hop(OF_COURSE 등)을 행이 실제로 들고 있는 값으로
    # 펼친다. 행에 그 이웃 라벨의 속성이 없으면 그 hop 은 그리지 않는다. 알 수 없는
    # 것을 라벨 이름으로 대신 채우면 실제로 없는 노드를 그리게 된다.
    neighbour_ids: dict[tuple[str, str], str] = {}
    for step in steps:
        if step.get("start_label") != fact_label:
            continue
        target_label = step.get("end_label")
        if not isinstance(target_label, str) or target_label == "Evidence":
            continue
        target_props = catalog.properties_for_labels({target_label})
        display_field = (
            "area_name"
            if target_label == "EducationArea"
            else next((f for f in _DISPLAY_FIELDS if f in target_props), None)
        )
        if display_field is None:
            continue
        neighbour_measure = take_measure(step.get("relationship_type", ""))
        for raw_id, row in sorted(fact_rows.items()):
            value = _safe_display(row.get(display_field), "")
            if not value:
                continue
            key = (target_label, value)
            if key not in neighbour_ids:
                order += 1
                node_id = _opaque_id(opaque_key, "neighbour-node", f"{target_label}\x1f{value}")
                neighbour_ids[key] = node_id
                nodes.append({
                    "id": node_id,
                    "display_name": value,
                    "node_type": target_label,
                    "node_type_ko": _node_type_ko(catalog, target_label),
                    "verification_status": "VERIFIED",
                    "visit_order": order,
                })
            hop += 1
            edges.append({
                "id": _opaque_id(
                    opaque_key, "neighbour-edge", f"{raw_id}\x1f{target_label}\x1f{value}"
                ),
                "source": fact_ids[raw_id],
                "target": neighbour_ids[key],
                "relationship": step.get("relationship_type", ""),
                "relationship_ko": _relationship_ko(
                    catalog, step.get("relationship_type", "")
                ),
                "traversal_order": hop,
                **neighbour_measure,
            })

    # 3) 승인된 provenance 쌍만 Evidence 로 잇는다.
    evidence_ids: dict[str, str] = {}
    supported_ko = _relationship_ko(catalog, "SUPPORTED_BY")
    supported_measure = take_measure("SUPPORTED_BY")
    for raw_id, row in sorted(evidence_rows.items()):
        order += 1
        node_id = _opaque_id(opaque_key, "evidence-node", raw_id)
        evidence_ids[raw_id] = node_id
        page = row.get("excerpt_page")
        nodes.append({
            "id": node_id,
            "display_name": f"발췌 PDF {page}쪽" if isinstance(page, int) else "원문 근거",
            "node_type": "Evidence",
            "node_type_ko": _node_type_ko(catalog, "Evidence"),
            "verification_status": "VERIFIED",
            "visit_order": order,
            "excerpt_page": page if isinstance(page, int) else None,
        })
    for fid, eid in sorted(approved):
        if fid not in fact_ids or eid not in evidence_ids:
            return None
        hop += 1
        edges.append({
            "id": _opaque_id(opaque_key, "provenance-edge", f"{fid}\x1f{eid}"),
            "source": fact_ids[fid],
            "target": evidence_ids[eid],
            "relationship": "SUPPORTED_BY",
            "relationship_ko": supported_ko,
            "traversal_order": hop,
            **supported_measure,
        })
    if len(edges) > MAX_GRAPH_EDGES or len(nodes) > MAX_GRAPH_NODES:
        return None
    return {
        "version": GRAPH_ENVELOPE_VERSION,
        "kind": "RESULT_TRAVERSAL",
        "ordered": True,
        "nodes": nodes,
        "edges": edges,
    }


_DETAIL_CACHE = re.compile(r"cache\[([^\]]+)\]")
_DETAIL_REL = re.compile(r"\((\w+)\)\s*(?:<-|-)\s*\[:?(\w+)\]\s*(?:->|-)\s*\((\w+)\)")
_DETAIL_PROP = re.compile(r"\b(\w+)\.(\w+)\b")
_DETAIL_LABEL = re.compile(r"\b\w+:(\w+)\b")

_OPERATOR_KO = {
    "NodeIndexSeek": "색인으로 시작 노드 찾기",
    "NodeUniqueIndexSeek": "고유 색인으로 시작 노드 찾기",
    "NodeByLabelScan": "라벨로 노드 훑기",
    "AllNodesScan": "전체 노드 훑기",
    "Expand(All)": "관계 타고 확장",
    "Filter": "조건으로 거르기",
    "Limit": "개수 제한",
    "Projection": "필요한 값만 뽑기",
    "ProduceResults": "결과 내보내기",
    "EagerAggregation": "집계",
    "Distinct": "중복 제거",
    "Sort": "정렬",
}


def describe_operator_ko(
    step: Mapping[str, Any],
    catalog: SchemaCatalog,
    label_by_relationship: Mapping[str, tuple[str, str]],
) -> str:
    """Say in Korean what this engine step actually did.

    값은 모두 PROFILE 이 보고한 것에서만 나온다. 속성·라벨·관계의 한국어 표기는
    ontology_spec.json 이 이미 갖고 있으므로 사전을 새로 만들지 않는다.
    """

    operator = str(step.get("operator") or "")
    # `cache[o.status]` 는 엔진 내부 표기다. 속성 이름만 남겨 읽히게 한다.
    detail = _DETAIL_CACHE.sub(r"\1", str(step.get("detail") or ""))
    rows = step.get("rows")
    base = _OPERATOR_KO.get(operator, operator)

    def particle(word: str, with_final: str, without_final: str) -> str:
        """받침 유무로 조사를 고른다. `교과목로` 처럼 어색해지는 것을 막는다."""
        if not word:
            return without_final
        last = word[-1]
        if not ("가" <= last <= "힣"):
            return without_final
        return with_final if (ord(last) - 0xAC00) % 28 else without_final

    def prop_ko(prop: str) -> str:
        for (label, name), korean in catalog.property_labels_ko.items():
            if name == prop and korean:
                return korean
        return prop

    if operator.startswith("Expand"):
        match = _DETAIL_REL.search(detail)
        if match:
            _, relationship, _ = match.groups()
            pair = label_by_relationship.get(relationship)
            rel_ko = catalog.relationship_ko(relationship)
            if pair:
                start_ko, end_ko = catalog.label_ko(pair[0]), catalog.label_ko(pair[1])
                to = particle(end_ko, "으로", "로")
                return (
                    f"{start_ko}에서 '{rel_ko}' 관계를 타고 {end_ko}{to} 넘어갑니다"
                    + (f" · {rows}건" if isinstance(rows, int) else "")
                )
            return f"'{rel_ko}' 관계를 타고 넘어갑니다"
    if operator.startswith("NodeIndexSeek") or operator.startswith("NodeUniqueIndexSeek"):
        labels = _DETAIL_LABEL.findall(detail)
        props = [prop_ko(m[1]) for m in _DETAIL_PROP.findall(detail)]
        where = f" {props[0]} 값으로" if props else ""
        if labels:
            return f"{catalog.label_ko(labels[0])}를{where} 색인에서 찾아 탐색을 시작합니다"
    if operator == "Filter":
        props = [prop_ko(m[1]) for m in _DETAIL_PROP.findall(detail)]
        labels = [catalog.label_ko(x) for x in _DETAIL_LABEL.findall(detail)]
        checks = list(dict.fromkeys(props + labels))
        if checks:
            return (
                f"{', '.join(checks[:3])} 조건에 맞는 것만 남깁니다"
                + (f" · {rows}건 남음" if isinstance(rows, int) else "")
            )
    if operator == "CacheProperties":
        return "다음 단계에서 쓸 속성값을 미리 읽어 둡니다"
    if operator == "Limit":
        return f"결과를 최대 {detail.strip() or '지정된 수'}건으로 제한합니다"
    if operator == "Projection":
        return "답변에 쓸 값만 골라 냅니다"
    if operator == "ProduceResults":
        return f"최종 {rows}건을 돌려줍니다" if isinstance(rows, int) else "결과를 돌려줍니다"
    return base


def _scoped_label(
    catalog: SchemaCatalog, label: str, parameters: Mapping[str, Any]
) -> str:
    """Name a path node by the scope the query actually bound to it.

    라벨의 한국어 이름만 쓰면 `교육과정 버전` 처럼 종류만 보인다. 그 라벨에 실제로
    묶인 파라미터가 있으면 앞에 붙여 `2026학년도 교육과정 버전` 으로 만든다. 값은
    검증을 통과해 Neo4j 로 실제 전달된 파라미터라 지어내는 것이 없다.

    학수번호·내부 ID 처럼 사람이 읽기 어려운 값은 붙이지 않는다.
    """

    base = _node_type_ko(catalog, label)
    prefixes: list[str] = []
    for name, value in parameters.items():
        binding = BASE_FILTER_BINDINGS.get(name)
        if binding is None or binding.label != label:
            continue
        if name == "academic_year" and isinstance(value, int) and not isinstance(value, bool):
            prefixes.append(f"{value}학년도")
        elif isinstance(value, str) and value and ":" not in value and len(value) <= 18:
            # MAJOR_REQUIRED 같은 통제어휘 값은 명세의 한국어 표기로 바꾼다.
            vocabulary = catalog.property_vocabularies.get((label, binding.property_name))
            korean = (
                catalog.vocabulary_labels_ko.get((vocabulary, value))
                if vocabulary
                else ""
            )
            # CSE 같은 내부 코드나 영문 controlled value를 이름처럼 보이게 하지
            # 않는다. 한국어 표기가 명세에 있을 때만 범위 이름에 붙인다.
            if korean:
                prefixes.append(korean)
    return f"{' '.join(prefixes)} {base}".strip() if prefixes else base


def _approved_path_steps(
    path_edges: Sequence[Mapping[str, Any]],
    allowed_labels: set[str],
    allowed_relationships: set[str],
) -> list[dict[str, Any]]:
    """Keep only well-formed hops whose endpoints are approved labels."""

    steps: list[dict[str, Any]] = []
    for item in path_edges or ():
        if not isinstance(item, Mapping):
            continue
        order = item.get("order")
        start = item.get("start_label")
        relationship = item.get("relationship_type")
        end = item.get("end_label")
        if (
            not isinstance(order, int)
            or isinstance(order, bool)
            or order < 1
            or not all(
                isinstance(name, str) and _SAFE_TYPE.fullmatch(name)
                for name in (start, relationship, end)
            )
            or start not in allowed_labels
            or end not in allowed_labels
            or relationship not in allowed_relationships
        ):
            # 한 hop 이라도 계약을 벗어나면 순서 표기를 포기한다. 부분적으로 맞는
            # 경로는 틀린 경로보다 낫지 않다.
            return []
        steps.append(
            {
                "order": order,
                "start_label": start,
                "relationship_type": relationship,
                "end_label": end,
            }
        )
    steps.sort(key=lambda step: step["order"])
    return steps


def _path_matches_catalog(
    steps: Sequence[Mapping[str, Any]], catalog: SchemaCatalog
) -> bool:
    """Require every approved hop to follow an ontology-declared direction."""

    for step in steps:
        relationship = catalog.relationships.get(str(step.get("relationship_type") or ""))
        if relationship is None:
            return False
        if (
            step.get("start_label") not in relationship.from_labels
            or step.get("end_label") not in relationship.to_labels
        ):
            return False
    return True


def _assign_visit_order(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    steps: list[dict[str, Any]],
) -> None:
    """Number nodes by when the path first reaches them."""

    if not steps:
        for node in nodes:
            node["visit_order"] = None
        return
    reached: dict[str, int] = {}
    position = 0
    for step in steps:
        for label in (step["start_label"], step["end_label"]):
            if label not in reached:
                position += 1
                reached[label] = position
    for node in nodes:
        node["visit_order"] = reached.get(node["node_type"])


def _fact_display(row: Mapping[str, Any], fact_label: str, fallback_ko: str = "") -> str:
    for field in _DISPLAY_FIELDS:
        if field in row:
            value = _safe_display(row.get(field), "")
            if value:
                return value
    return f"{fallback_ko or fact_label} 결과"


def _approved_pairs(
    approved_pairs: Iterable[tuple[str, str]],
) -> set[tuple[str, str]]:
    return {
        (fact_id, evidence_id)
        for fact_id, evidence_id in approved_pairs
        if isinstance(fact_id, str)
        and fact_id
        and isinstance(evidence_id, str)
        and evidence_id
    }


def _verified_projection_rows(
    rows: Sequence[Mapping[str, Any]],
    approved: set[tuple[str, str]],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]] | None:
    """Return only exact ResultValidator and ClaimValidator-approved row pairs.

    The final graph cannot have a weaker boundary than the standalone provenance
    projection.  In particular, a row that is not in the approved pair set must
    not leave a Fact or Evidence node behind merely because another row passed.
    """

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
    return fact_rows, evidence_rows


def build_provenance_projection(
    rows: Sequence[Mapping[str, Any]],
    approved_pairs: Iterable[tuple[str, str]],
    *,
    opaque_key: bytes,
) -> dict[str, Any] | None:
    """Project only VERIFIED direct Fact→Evidence pairs approved by validators."""

    approved = _approved_pairs(approved_pairs)
    verified_rows = _verified_projection_rows(rows, approved)
    if verified_rows is None:
        return None
    fact_rows, evidence_rows = verified_rows

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
    try:
        catalog = SchemaCatalog.from_generated()
    except (OSError, ValueError, SchemaCatalogError):
        catalog = None
    supported_by_ko = (
        _relationship_ko(catalog, "SUPPORTED_BY")
        if catalog
        else _SAFE_RELATIONSHIP_FALLBACK
    )

    nodes: list[dict[str, Any]] = []
    for raw_id, row in sorted(fact_rows.items()):
        fact_label = str(row["fact_label"])
        label_ko = (
            _node_type_ko(catalog, fact_label)
            if catalog
            else _SAFE_NODE_TYPE_FALLBACK
        )
        nodes.append(
            {
                "id": fact_ids[raw_id],
                "display_name": _fact_display(row, fact_label, label_ko),
                "node_type": fact_label,
                "node_type_ko": label_ko,
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
                "node_type_ko": (
                    _node_type_ko(catalog, "Evidence")
                    if catalog
                    else _SAFE_NODE_TYPE_FALLBACK
                ),
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
            "relationship_ko": supported_by_ko,
            "traversal_order": index,
        }
        for index, (fact_id, evidence_id) in enumerate(sorted(approved), start=1)
    ]
    if len(edges) > MAX_GRAPH_EDGES:
        return None
    return {
        "version": GRAPH_ENVELOPE_VERSION,
        "kind": "RESULT_PROVENANCE",
        "nodes": nodes,
        "edges": edges,
    }
