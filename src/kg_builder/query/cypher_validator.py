"""Deny-by-default validator for the deliberately small dynamic Cypher subset."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .query_plan import QueryPlan, SelectionMode, resolve_filter_bindings
from .result_limits import ABSOLUTE_MAX_RESULT_ROWS, maximum_rows_for
from .schema_catalog import SchemaCatalog


class CypherValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_VALIDATION_SEAL = object()


@dataclass(frozen=True, slots=True)
class ProvenanceContract:
    fact_variable: str
    fact_label: str
    fact_id_property: str
    evidence_variable: str


@dataclass(frozen=True, slots=True)
class PathStep:
    """One directed hop of the approved MATCH pattern, in written order.

    검증기는 MATCH 를 파싱하면서 이 경로를 이미 만들지만 종전에는 라벨·관계 이름을
    알파벳으로 정렬한 집합만 내보내 순서가 사라졌다. 그래서 화면은 질의가 실제로
    밟은 경로 대신 온톨로지가 허용하는 간선의 교차곱을 그릴 수밖에 없었다.
    여기서 순서를 그대로 실어 보내 "몇 번째로 어느 관계를 탔는지"를 표시 가능하게 한다.
    """

    order: int
    start_variable: str
    start_label: str
    relationship_type: str
    end_variable: str
    end_label: str


@dataclass(frozen=True, slots=True, init=False)
class ValidatedCypher:
    """Validator-issued value; direct construction is intentionally rejected."""

    text: str
    parameters: dict[str, Any]
    limit: int
    labels: tuple[str, ...]
    relationship_types: tuple[str, ...]
    provenance: ProvenanceContract
    # 승인된 MATCH 경로를 쓰인 순서 그대로. labels/relationship_types 는 정렬된
    # 집합이라 순서를 담지 못한다.
    path_edges: tuple[PathStep, ...]
    _seal: object = field(repr=False, compare=False)

    def __init__(
        self,
        text: str,
        parameters: dict[str, Any],
        limit: int,
        labels: tuple[str, ...],
        relationship_types: tuple[str, ...],
        provenance: ProvenanceContract,
        path_edges: tuple[PathStep, ...] = (),
        *,
        _seal: object | None = None,
    ) -> None:
        if _seal is not _VALIDATION_SEAL:
            raise TypeError("ValidatedCypher can only be issued by CypherValidator")
        for name, value in (
            ("text", text),
            ("parameters", parameters),
            ("limit", limit),
            ("path_edges", path_edges),
            ("labels", labels),
            ("relationship_types", relationship_types),
            ("provenance", provenance),
            ("_seal", _seal),
        ):
            object.__setattr__(self, name, value)

    @classmethod
    def _issue(
        cls,
        *,
        text: str,
        parameters: dict[str, Any],
        limit: int,
        labels: tuple[str, ...],
        relationship_types: tuple[str, ...],
        provenance: ProvenanceContract,
        path_edges: tuple[PathStep, ...] = (),
    ) -> "ValidatedCypher":
        return cls(
            text,
            parameters,
            limit,
            labels,
            relationship_types,
            provenance,
            path_edges,
            _seal=_VALIDATION_SEAL,
        )

    def _is_approved(self) -> bool:
        return self._seal is _VALIDATION_SEAL


@dataclass(frozen=True, slots=True)
class LiteralSpan:
    start: int
    end: int
    value: str


@dataclass(frozen=True, slots=True)
class LexedCypher:
    sanitized: str
    canonical: str
    string_literals: tuple[str, ...]
    literal_spans: tuple[LiteralSpan, ...]
    semicolons: tuple[int, ...]
    backtick_identifiers: tuple[LiteralSpan, ...]


IDENTIFIER = r"[A-Za-z][A-Za-z0-9_]*"
PARAMETER = re.compile(r"\$(%s)" % IDENTIFIER)
PROPERTY_ACCESS = re.compile(r"\b(%s)\.(%s)\b" % (IDENTIFIER, IDENTIFIER))
NODE_OCCURRENCE = re.compile(r"\(([^()]*)\)", re.DOTALL)
RELATION_OCCURRENCE = re.compile(
    r"(?=(\(([^()]*)\)\s*(<-|-)\s*\[([^\]]*)\]\s*(->|-)\s*\(([^()]*)\)))",
    re.DOTALL,
)
RETURN_ITEM = re.compile(
    rf"^\s*({IDENTIFIER})\.({IDENTIFIER})\s+AS\s+({IDENTIFIER})\s*$",
    re.IGNORECASE,
)
GRAPH_PARAMETER_FILTER = re.compile(
    rf"^\s*({IDENTIFIER})\.({IDENTIFIER})\s*=\s*\$({IDENTIFIER})\s*$",
    re.IGNORECASE,
)
PARAMETER_IN_PROPERTY_FILTER = re.compile(
    rf"^\s*\$({IDENTIFIER})\s+IN\s+({IDENTIFIER})\.({IDENTIFIER})\s*$",
    re.IGNORECASE,
)
PROPERTY_IN_PARAMETER_FILTER = re.compile(
    rf"^\s*({IDENTIFIER})\.({IDENTIFIER})\s+IN\s+\$({IDENTIFIER})\s*$",
    re.IGNORECASE,
)
VERIFIED_FILTER = re.compile(
    rf"^\s*({IDENTIFIER})\.(status|verification_status)\s*=\s*$",
    re.IGNORECASE,
)

NODE_PATTERN_TEXT = rf"\(\s*{IDENTIFIER}(?:\s*:\s*{IDENTIFIER})*\s*\)"
REL_PATTERN_TEXT = (
    rf"(?:<-|-)\s*\[\s*(?:{IDENTIFIER}\s*)?:\s*{IDENTIFIER}\s*\]\s*(?:->|-)"
)
PATH_PATTERN_TEXT = rf"{NODE_PATTERN_TEXT}(?:\s*{REL_PATTERN_TEXT}\s*{NODE_PATTERN_TEXT})+"
MATCH_BODY = re.compile(rf"^\s*{PATH_PATTERN_TEXT}(?:\s*,\s*{PATH_PATTERN_TEXT})*\s*$")

CLAUSE_PATTERN = re.compile(
    r"\b(OPTIONAL\s+MATCH|MATCH|WHERE|WITH|RETURN|ORDER\s+BY|SKIP|LIMIT)\b",
    re.IGNORECASE,
)
FORBIDDEN_KEYWORDS = re.compile(
    r"\b(?:CREATE|INSERT|MERGE|DELETE|DETACH|SET|REMOVE|DROP|ALTER|RENAME|"
    r"LOAD\s+CSV|FOREACH|CALL|APOC|USE|SHOW|GRANT|DENY|REVOKE|START|STOP|"
    r"TERMINATE|UNION|UNWIND|YIELD|PROFILE|EXPLAIN|FINISH)\b",
    re.IGNORECASE,
)
FUNCTION_CALL = re.compile(rf"\b({IDENTIFIER})\s*\(")


def lex_cypher(text: str) -> LexedCypher:
    output = list(text)
    canonical = list(text)
    literals: list[str] = []
    literal_spans: list[LiteralSpan] = []
    semicolons: list[int] = []
    backtick_identifiers: list[LiteralSpan] = []
    index = 0
    state = "normal"
    quote = ""
    literal: list[str] = []
    literal_start = 0
    backtick: list[str] = []
    backtick_start = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if state == "normal":
            if char == "/" and next_char == "/":
                output[index] = output[index + 1] = " "
                canonical[index] = canonical[index + 1] = " "
                state = "line_comment"
                index += 2
                continue
            if char == "/" and next_char == "*":
                output[index] = output[index + 1] = " "
                canonical[index] = canonical[index + 1] = " "
                state = "block_comment"
                index += 2
                continue
            if char in {"'", '"'}:
                quote = char
                literal = []
                literal_start = index
                output[index] = " "
                state = "string"
            elif char == "`":
                backtick = []
                backtick_start = index
                output[index] = " "
                state = "backtick"
            elif char == ";":
                semicolons.append(index)
            index += 1
            continue
        if state == "line_comment":
            output[index] = "\n" if char == "\n" else " "
            canonical[index] = "\n" if char == "\n" else " "
            if char == "\n":
                state = "normal"
            index += 1
            continue
        if state == "block_comment":
            output[index] = " "
            canonical[index] = " "
            if char == "*" and next_char == "/":
                output[index + 1] = " "
                canonical[index + 1] = " "
                state = "normal"
                index += 2
            else:
                index += 1
            continue
        if state == "backtick":
            output[index] = " "
            if char == "`":
                if next_char == "`":
                    backtick.append("`")
                    output[index + 1] = " "
                    index += 2
                    continue
                backtick_identifiers.append(
                    LiteralSpan(backtick_start, index + 1, "".join(backtick))
                )
                state = "normal"
            else:
                backtick.append(char)
            index += 1
            continue
        output[index] = " "
        if char == "\\" and next_char:
            literal.append(next_char)
            output[index + 1] = " "
            index += 2
            continue
        if char == quote:
            if next_char == quote:
                literal.append(quote)
                output[index + 1] = " "
                index += 2
                continue
            value = "".join(literal)
            literals.append(value)
            literal_spans.append(LiteralSpan(literal_start, index + 1, value))
            state = "normal"
        else:
            literal.append(char)
        index += 1
    if state in {"string", "block_comment", "backtick"}:
        raise CypherValidationError(
            "CYPHER_UNTERMINATED_TOKEN",
            "unterminated string, comment, or identifier",
        )
    return LexedCypher(
        "".join(output),
        "".join(canonical).strip(),
        tuple(literals),
        tuple(literal_spans),
        tuple(semicolons),
        tuple(backtick_identifiers),
    )


def defensive_read_only_check(text: str) -> None:
    """Executor-side defense in depth; full validation still belongs to the pipeline."""

    lexed = lex_cypher(text)
    if not lexed.canonical:
        raise CypherValidationError("CYPHER_EMPTY", "Cypher must contain a query")
    if lexed.canonical != text.strip():
        raise CypherValidationError(
            "CYPHER_COMMENT_NOT_CANONICAL",
            "executor accepts only comment-free canonical Cypher",
        )
    if lexed.backtick_identifiers:
        raise CypherValidationError(
            "CYPHER_BACKTICK_IDENTIFIER", "backtick identifiers are not allowed"
        )
    if lexed.semicolons:
        raise CypherValidationError("CYPHER_SEMICOLON", "semicolons are not allowed")
    if FORBIDDEN_KEYWORDS.search(lexed.sanitized):
        raise CypherValidationError("CYPHER_FORBIDDEN_KEYWORD", "write or unsupported clause")
    if "{" in lexed.sanitized or "}" in lexed.sanitized:
        raise CypherValidationError("CYPHER_SUBQUERY_FORBIDDEN", "subqueries and maps are forbidden")
    functions = {
        item.upper()
        for item in FUNCTION_CALL.findall(lexed.sanitized)
        if item.upper() != "MATCH"
    }
    if functions:
        raise CypherValidationError(
            "CYPHER_FUNCTION_FORBIDDEN", f"function calls are not allowed: {sorted(functions)}"
        )


def _node_parts(body: str) -> tuple[str | None, set[str]]:
    variable_match = re.match(rf"\s*({IDENTIFIER})", body)
    variable = variable_match.group(1) if variable_match else None
    labels = set(re.findall(rf":\s*({IDENTIFIER})", body))
    return variable, labels


@dataclass(frozen=True, slots=True)
class Clause:
    name: str
    start: int
    body_start: int
    body_end: int


@dataclass(frozen=True, slots=True)
class RelationshipEdge:
    start_variable: str
    end_variable: str
    relationship_type: str


class CypherValidator:
    def __init__(self, catalog: SchemaCatalog, *, max_rows: int = ABSOLUTE_MAX_RESULT_ROWS):
        self.catalog = catalog
        self.max_rows = max_rows

    def validate(self, plan: QueryPlan, cypher: str) -> ValidatedCypher:
        if not isinstance(cypher, str) or not cypher.strip():
            self._fail("CYPHER_EMPTY", "Cypher must be non-empty text")
        lexed = lex_cypher(cypher)
        if not lexed.canonical:
            self._fail("CYPHER_EMPTY", "Cypher must contain a query after comments are removed")
        defensive_read_only_check(lexed.canonical)
        sanitized = lexed.sanitized
        if lexed.backtick_identifiers:
            self._fail("CYPHER_BACKTICK_IDENTIFIER", "backtick identifiers are not allowed")
        if any(value != "VERIFIED" for value in lexed.string_literals):
            self._fail(
                "CYPHER_LITERAL_VALUE",
                "only the VERIFIED policy string literal is allowed",
            )

        clauses = self._parse_clauses(sanitized)
        clause_names = [clause.name for clause in clauses]
        self._validate_clause_sequence(clause_names)
        clause_by_name = {clause.name: clause for clause in clauses}

        match_clauses = [
            clause for clause in clauses if clause.name in {"MATCH", "OPTIONAL MATCH"}
        ]
        for clause in match_clauses:
            if not MATCH_BODY.fullmatch(sanitized[clause.body_start : clause.body_end]):
                self._fail(
                    "CYPHER_MATCH_PATTERN_UNSUPPORTED",
                    "MATCH must contain only directed, typed, fixed-length node paths",
                )

        variable_labels: dict[str, set[str]] = {}
        labels: set[str] = set()
        for match in NODE_OCCURRENCE.finditer(sanitized):
            variable, occurrence_labels = _node_parts(match.group(1))
            unknown_labels = occurrence_labels - set(self.catalog.nodes)
            if unknown_labels:
                self._fail("CYPHER_UNKNOWN_LABEL", f"undeclared labels: {sorted(unknown_labels)}")
            if variable and occurrence_labels:
                variable_labels.setdefault(variable, set()).update(occurrence_labels)
            labels.update(occurrence_labels)
        if not labels:
            self._fail("CYPHER_UNLABELED_SCAN", "at least one declared node label is required")

        relationship_types: set[str] = set()
        edges: list[RelationshipEdge] = []
        for match in RELATION_OCCURRENCE.finditer(sanitized):
            left_body, left_arrow, rel_body, right_arrow, right_body = (
                match.group(2), match.group(3), match.group(4), match.group(5), match.group(6)
            )
            rel_type_match = re.search(rf":\s*({IDENTIFIER})", rel_body)
            if not rel_type_match:
                self._fail("CYPHER_UNTYPED_RELATIONSHIP", "all relationships must be typed")
            rel_type = rel_type_match.group(1)
            if rel_type not in self.catalog.relationships:
                self._fail("CYPHER_UNKNOWN_RELATIONSHIP", f"undeclared relationship: {rel_type}")
            relationship_types.add(rel_type)
            left_var, left_labels = _node_parts(left_body)
            right_var, right_labels = _node_parts(right_body)
            left_labels = left_labels or variable_labels.get(left_var or "", set())
            right_labels = right_labels or variable_labels.get(right_var or "", set())
            if not left_var or not right_var or not left_labels or not right_labels:
                self._fail(
                    "CYPHER_ENDPOINT_LABEL_REQUIRED",
                    f"relationship {rel_type} endpoints must resolve to declared labels",
                )
            if left_arrow == "-" and right_arrow == "->":
                start_var, end_var = left_var, right_var
                start, end = left_labels, right_labels
            elif left_arrow == "<-" and right_arrow == "-":
                start_var, end_var = right_var, left_var
                start, end = right_labels, left_labels
            else:
                self._fail("CYPHER_RELATIONSHIP_DIRECTION", "undirected relationships are forbidden")
            definition = self.catalog.relationships[rel_type]
            if not self._labels_match(start, definition.from_labels) or not self._labels_match(
                end, definition.to_labels
            ):
                self._fail(
                    "CYPHER_RELATIONSHIP_ENDPOINT",
                    f"relationship {rel_type} direction or endpoints violate ontology",
                )
            edges.append(RelationshipEdge(start_var, end_var, rel_type))

        for variable, prop in PROPERTY_ACCESS.findall(sanitized):
            if variable not in variable_labels:
                self._fail("CYPHER_UNKNOWN_VARIABLE", f"unknown graph variable: {variable}")
            allowed = self.catalog.properties_for_labels(variable_labels[variable])
            if prop not in allowed:
                self._fail(
                    "CYPHER_UNKNOWN_PROPERTY",
                    f"property {variable}.{prop} is not declared for its label",
                )

        where = clause_by_name["WHERE"]
        filter_sources, verified_sources = self._validate_where(
            plan,
            sanitized[where.body_start : where.body_end],
            variable_labels,
            lexed,
            where,
        )
        all_parameters = PARAMETER.findall(sanitized)
        if set(all_parameters) != set(plan.filters) or len(all_parameters) != len(plan.filters):
            self._fail(
                "CYPHER_PARAMETER_USAGE",
                "each QueryPlan filter must be used exactly once in WHERE and nowhere else",
            )

        provenance = self._validate_provenance(variable_labels, edges, verified_sources)
        return_clause = clause_by_name["RETURN"]
        return_sources = self._validate_return(
            plan,
            sanitized[return_clause.body_start : return_clause.body_end],
            variable_labels,
            filter_sources,
            provenance,
            edges,
        )
        if "WITH" in clause_by_name:
            self._validate_with(
                sanitized[
                    clause_by_name["WITH"].body_start : clause_by_name["WITH"].body_end
                ],
                variable_labels,
            )
        if "ORDER BY" in clause_by_name:
            self._validate_order_by(
                sanitized[
                    clause_by_name["ORDER BY"].body_start : clause_by_name["ORDER BY"].body_end
                ],
                return_sources,
            )
        if "SKIP" in clause_by_name:
            self._validate_nonnegative_integer(
                sanitized[clause_by_name["SKIP"].body_start : clause_by_name["SKIP"].body_end],
                "CYPHER_SKIP_INVALID",
            )
        limit = self._validate_nonnegative_integer(
            sanitized[clause_by_name["LIMIT"].body_start : clause_by_name["LIMIT"].body_end],
            "CYPHER_LIMIT_REQUIRED",
        )
        plan_max_rows = maximum_rows_for(plan, self.max_rows)
        if not 1 <= limit <= plan_max_rows:
            self._fail(
                "CYPHER_LIMIT_EXCEEDED",
                f"LIMIT must be between 1 and {plan_max_rows}",
            )
        if plan.selection_mode is SelectionMode.COURSE_LIST and limit != plan_max_rows:
            self._fail(
                "CYPHER_LIST_LIMIT_INCOMPLETE",
                f"COURSE_LIST LIMIT must be {plan_max_rows} so an all-items request is not truncated",
            )

        return ValidatedCypher._issue(
            text=lexed.canonical,
            parameters=dict(plan.filters),
            limit=limit,
            labels=tuple(sorted(labels)),
            relationship_types=tuple(sorted(relationship_types)),
            provenance=provenance,
            path_edges=self._path_steps(edges, variable_labels),
        )

    @staticmethod
    def _path_steps(
        edges: list[RelationshipEdge], variable_labels: dict[str, set[str]]
    ) -> tuple[PathStep, ...]:
        """Number the approved MATCH hops in the order the pattern writes them.

        한 변수에 라벨이 여러 개 붙는 경우는 표시에서만 첫 라벨을 쓴다. 검증은 이미
        위에서 라벨 집합 전체로 마쳤으므로 여기서 계약이 느슨해지지 않는다.
        """

        def one_label(variable: str) -> str:
            found = sorted(variable_labels.get(variable, set()))
            return found[0] if found else variable

        return tuple(
            PathStep(
                order=index,
                start_variable=edge.start_variable,
                start_label=one_label(edge.start_variable),
                relationship_type=edge.relationship_type,
                end_variable=edge.end_variable,
                end_label=one_label(edge.end_variable),
            )
            for index, edge in enumerate(edges, start=1)
        )

    def _parse_clauses(self, sanitized: str) -> list[Clause]:
        matches = list(CLAUSE_PATTERN.finditer(sanitized))
        if not matches:
            self._fail("CYPHER_CLAUSE_REQUIRED", "query contains no supported clauses")
        clauses = []
        for index, match in enumerate(matches):
            name = " ".join(match.group(1).upper().split())
            clauses.append(
                Clause(
                    name,
                    match.start(),
                    match.end(),
                    matches[index + 1].start() if index + 1 < len(matches) else len(sanitized),
                )
            )
        return clauses

    def _validate_clause_sequence(self, names: list[str]) -> None:
        index = 0
        while index < len(names) and names[index] in {"MATCH", "OPTIONAL MATCH"}:
            index += 1
        if index == 0 or index >= len(names) or names[index] != "WHERE":
            self._fail("CYPHER_CLAUSE_SEQUENCE", "MATCH clauses must be followed by one WHERE")
        index += 1
        if index < len(names) and names[index] == "WITH":
            index += 1
        if index >= len(names) or names[index] != "RETURN":
            self._fail("CYPHER_CLAUSE_SEQUENCE", "WHERE must be followed by optional WITH and RETURN")
        index += 1
        if index < len(names) and names[index] == "ORDER BY":
            index += 1
        if index < len(names) and names[index] == "SKIP":
            index += 1
        if index >= len(names) or names[index] != "LIMIT" or index != len(names) - 1:
            self._fail("CYPHER_CLAUSE_SEQUENCE", "query must end with exactly one literal LIMIT")

    def _validate_where(
        self,
        plan: QueryPlan,
        body: str,
        variable_labels: dict[str, set[str]],
        lexed: LexedCypher,
        clause: Clause,
    ) -> tuple[dict[str, tuple[str, str]], set[tuple[str, str]]]:
        if re.search(r"\bOR\b", body, re.IGNORECASE):
            self._fail("CYPHER_WHERE_UNSUPPORTED", "only AND-combined predicates are allowed")
        terms = re.split(r"\bAND\b", body, flags=re.IGNORECASE)
        filter_sources: dict[str, tuple[str, str]] = {}
        verified_sources: set[tuple[str, str]] = set()
        for term in terms:
            term = term.strip()
            if not term:
                self._fail("CYPHER_WHERE_UNSUPPORTED", "empty WHERE predicate")
            graph_parameter = GRAPH_PARAMETER_FILTER.fullmatch(term)
            parameter_in_property = PARAMETER_IN_PROPERTY_FILTER.fullmatch(term)
            property_in_parameter = PROPERTY_IN_PARAMETER_FILTER.fullmatch(term)
            verified = VERIFIED_FILTER.fullmatch(term)
            if graph_parameter:
                variable, prop, parameter = graph_parameter.groups()
                operator = "EQUALS"
            elif parameter_in_property:
                parameter, variable, prop = parameter_in_property.groups()
                operator = "PARAMETER_IN_PROPERTY"
            elif property_in_parameter:
                variable, prop, parameter = property_in_parameter.groups()
                operator = "PROPERTY_IN_PARAMETER"
            elif verified:
                variable, prop = verified.groups()
                verified_sources.add((variable, prop.lower()))
                continue
            else:
                self._fail(
                    "CYPHER_WHERE_UNSUPPORTED",
                    f"unsupported WHERE predicate: {term[:80]}",
                )
            if parameter not in plan.filters:
                self._fail("CYPHER_PARAMETER_MISSING", f"parameter {parameter} is not in QueryPlan")
            # 같은 필터 이름이라도 fact family 마다 바인딩 대상 라벨/연산자가 다르다.
            # plan 의 selection_mode 로 확정된 바인딩만 통과시킨다.
            policy = resolve_filter_bindings(plan.selection_mode)[parameter]
            if prop != policy.property_name or operator != policy.operator:
                self._fail(
                    "CYPHER_FILTER_BINDING",
                    f"parameter {parameter} must bind to {policy.label}.{policy.property_name}",
                )
            if policy.label not in variable_labels.get(variable, set()):
                self._fail(
                    "CYPHER_FILTER_BINDING",
                    f"parameter {parameter} uses an alias without label {policy.label}",
                )
            if parameter in filter_sources:
                self._fail("CYPHER_FILTER_BINDING", f"parameter {parameter} is bound more than once")
            filter_sources[parameter] = (variable, prop)
        if set(filter_sources) != set(plan.filters):
            missing = set(plan.filters) - set(filter_sources)
            self._fail("CYPHER_FILTER_BINDING", f"unbound QueryPlan filters: {sorted(missing)}")
        where_literals = [
            span.value
            for span in lexed.literal_spans
            if clause.body_start <= span.start < clause.body_end
        ]
        if where_literals != ["VERIFIED", "VERIFIED"]:
            self._fail(
                "CYPHER_VERIFIED_FILTER_REQUIRED",
                "WHERE must contain exactly the fact and Evidence VERIFIED policy literals",
            )
        return filter_sources, verified_sources

    def _validate_provenance(
        self,
        variable_labels: dict[str, set[str]],
        edges: list[RelationshipEdge],
        verified_sources: set[tuple[str, str]],
    ) -> ProvenanceContract:
        supported = [edge for edge in edges if edge.relationship_type == "SUPPORTED_BY"]
        if len(supported) != 1:
            self._fail(
                "CYPHER_EVIDENCE_PATH_REQUIRED",
                "exactly one direct fact-[:SUPPORTED_BY]->Evidence path is required",
            )
        edge = supported[0]
        fact_labels = variable_labels.get(edge.start_variable, set())
        evidence_labels = variable_labels.get(edge.end_variable, set())
        if "Evidence" not in evidence_labels or len(fact_labels) != 1:
            self._fail(
                "CYPHER_EVIDENCE_PATH_REQUIRED",
                "fact and Evidence variables must each have an explicit ontology label",
            )
        fact_label = next(iter(fact_labels))
        fact_definition = self.catalog.nodes[fact_label]
        if "status" not in self.catalog.properties_for_labels({fact_label}):
            self._fail(
                "CYPHER_FACT_STATUS_UNSUPPORTED",
                f"fact label {fact_label} has no VERIFIED status property",
            )
        if (edge.start_variable, "status") not in verified_sources or (
            edge.end_variable,
            "verification_status",
        ) not in verified_sources:
            self._fail(
                "CYPHER_VERIFIED_FILTER_REQUIRED",
                "the directly connected fact and Evidence must both be filtered as VERIFIED",
            )
        return ProvenanceContract(
            edge.start_variable,
            fact_label,
            fact_definition.id_property,
            edge.end_variable,
        )

    def _validate_return(
        self,
        plan: QueryPlan,
        body: str,
        variable_labels: dict[str, set[str]],
        filter_sources: dict[str, tuple[str, str]],
        provenance: ProvenanceContract,
        edges: list[RelationshipEdge],
    ) -> dict[str, tuple[str, str]]:
        body = re.sub(r"^\s*DISTINCT\b", "", body, flags=re.IGNORECASE)
        parts = [part.strip() for part in body.split(",")]
        if not parts or any(not part for part in parts):
            self._fail("CYPHER_RETURN_UNSUPPORTED", "RETURN items must be comma-separated fields")
        sources: dict[str, tuple[str, str]] = {}
        for part in parts:
            match = RETURN_ITEM.fullmatch(part)
            if not match:
                self._fail(
                    "CYPHER_RETURN_UNSUPPORTED",
                    "RETURN only supports graphVariable.property AS alias",
                )
            variable, prop, alias = match.groups()
            if alias in sources:
                self._fail("CYPHER_RETURN_DUPLICATE", f"duplicate RETURN alias: {alias}")
            if variable not in variable_labels:
                self._fail("CYPHER_UNKNOWN_VARIABLE", f"unknown return variable: {variable}")
            sources[alias] = (variable, prop)

        required = set(plan.requested_fields) | set(plan.filters) | {
            "fact_id",
            "fact_status",
            "evidence_id",
            "excerpt_page",
            "source_pdf_page",
            "printed_page",
            "source_text",
            "evidence_verification_status",
        }
        if plan.selection_mode in {SelectionMode.SINGLE_COURSE, SelectionMode.COURSE_LIST}:
            required.update({"course_identity", "name_ko"})
        # 탐색 화면이 뿌리 노드를 실제 이름으로 부르려면 그 이름을 돌려받아야 한다.
        # 계약을 넓히지 않기 위해 **이 별칭 하나만** 선택적으로 허용하고, 값이
        # 온톨로지가 선언한 속성인지까지 아래에서 다시 확인한다.
        optional = {"scope_identity", "area_name"} & set(sources)
        if set(sources) - optional != required:
            self._fail(
                "CYPHER_RETURN_FIELD_MISMATCH",
                f"RETURN aliases must exactly match required contract: {sorted(required)}",
            )
        for name, source in filter_sources.items():
            if sources[name] != source:
                self._fail(
                    "CYPHER_SCOPE_RETURN_BINDING",
                    f"scope {name} must be returned from its bound graph property",
                )
        for name in plan.requested_fields:
            variable, prop = sources[name]
            if prop != name:
                self._fail(
                    "CYPHER_REQUEST_FIELD_BINDING",
                    f"requested field {name} must return the same ontology property",
                )
            if variable != provenance.fact_variable and not any(
                edge.relationship_type != "SUPPORTED_BY"
                and provenance.fact_variable in {edge.start_variable, edge.end_variable}
                and variable in {edge.start_variable, edge.end_variable}
                for edge in edges
            ):
                self._fail(
                    "CYPHER_FACT_FIELD_PROVENANCE",
                    f"requested field {name} is not on the fact or its direct neighbor",
                )
        expected = {
            "fact_id": (provenance.fact_variable, provenance.fact_id_property),
            "fact_status": (provenance.fact_variable, "status"),
            "evidence_id": (provenance.evidence_variable, "evidence_id"),
            "excerpt_page": (provenance.evidence_variable, "excerpt_page"),
            "source_pdf_page": (provenance.evidence_variable, "source_pdf_page"),
            "printed_page": (provenance.evidence_variable, "printed_page"),
            "source_text": (provenance.evidence_variable, "raw_text"),
            "evidence_verification_status": (
                provenance.evidence_variable,
                "verification_status",
            ),
        }
        if plan.selection_mode in {SelectionMode.SINGLE_COURSE, SelectionMode.COURSE_LIST}:
            course_variables = [
                variable
                for variable, labels in variable_labels.items()
                if "Course" in labels
            ]
            if len(course_variables) != 1:
                self._fail(
                    "CYPHER_COURSE_IDENTITY_REQUIRED",
                    "course queries must return one explicitly labeled Course identity",
                )
            expected["course_identity"] = (course_variables[0], "course_id")
            expected["name_ko"] = (course_variables[0], "name_ko")
        if "scope_identity" in sources:
            variable, prop = sources["scope_identity"]
            labels = variable_labels.get(variable, set())
            if "CurriculumVersion" not in labels or prop != "version_name":
                self._fail(
                    "CYPHER_SCOPE_IDENTITY_INVALID",
                    "scope_identity must return CurriculumVersion.version_name",
                )
        if "area_name" in sources:
            variable, prop = sources["area_name"]
            labels = variable_labels.get(variable, set())
            if (
                plan.selection_mode is not SelectionMode.COURSE_LIST
                or not {"area_id", "area_ids"}.intersection(plan.filters)
                or "EducationArea" not in labels
                or prop != "name_ko"
            ):
                self._fail(
                    "CYPHER_AREA_NAME_INVALID",
                    "area_name must return EducationArea.name_ko for a scoped course list",
                )
        for alias, source in expected.items():
            if sources[alias] != source:
                self._fail(
                    "CYPHER_PROVENANCE_RETURN_BINDING",
                    f"{alias} must be returned from the direct fact-Evidence path",
                )
        return sources

    def _validate_with(self, body: str, variable_labels: dict[str, set[str]]) -> None:
        variables = [item.strip() for item in body.split(",")]
        if not variables or any(
            not re.fullmatch(IDENTIFIER, item) or item not in variable_labels for item in variables
        ):
            self._fail(
                "CYPHER_WITH_UNSUPPORTED",
                "WITH may only pass through existing graph variables without aliases or aggregation",
            )

    def _validate_order_by(
        self, body: str, return_sources: dict[str, tuple[str, str]]
    ) -> None:
        for item in body.split(","):
            match = re.fullmatch(
                rf"\s*({IDENTIFIER})(?:\s+(ASC|DESC))?\s*", item, re.IGNORECASE
            )
            if not match or match.group(1) not in return_sources:
                self._fail(
                    "CYPHER_ORDER_BY_UNSUPPORTED",
                    "ORDER BY may only use returned aliases with optional ASC or DESC",
                )

    def _validate_nonnegative_integer(self, body: str, code: str) -> int:
        if not re.fullmatch(r"\s*\d+\s*", body):
            self._fail(code, "SKIP and LIMIT must be non-negative integer literals")
        return int(body.strip())

    def _labels_match(self, actual: set[str], allowed: frozenset[str]) -> bool:
        effective = set(actual)
        pending = list(actual)
        while pending:
            label = pending.pop()
            definition = self.catalog.nodes.get(label)
            if not definition:
                continue
            for parent in definition.parent_labels:
                if parent not in effective:
                    effective.add(parent)
                    pending.append(parent)
        return bool(effective.intersection(allowed))

    @staticmethod
    def _fail(code: str, message: str) -> None:
        raise CypherValidationError(code, message)
