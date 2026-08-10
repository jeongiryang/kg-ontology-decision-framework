"""Deny-by-default validator for the deliberately small dynamic Cypher subset."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .query_plan import FILTER_BINDINGS, QueryPlan
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


@dataclass(frozen=True, slots=True, init=False)
class ValidatedCypher:
    """Validator-issued value; direct construction is intentionally rejected."""

    text: str
    parameters: dict[str, Any]
    limit: int
    labels: tuple[str, ...]
    relationship_types: tuple[str, ...]
    provenance: ProvenanceContract
    _seal: object = field(repr=False, compare=False)

    def __init__(
        self,
        text: str,
        parameters: dict[str, Any],
        limit: int,
        labels: tuple[str, ...],
        relationship_types: tuple[str, ...],
        provenance: ProvenanceContract,
        *,
        _seal: object | None = None,
    ) -> None:
        if _seal is not _VALIDATION_SEAL:
            raise TypeError("ValidatedCypher can only be issued by CypherValidator")
        for name, value in (
            ("text", text),
            ("parameters", parameters),
            ("limit", limit),
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
    ) -> "ValidatedCypher":
        return cls(
            text,
            parameters,
            limit,
            labels,
            relationship_types,
            provenance,
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
    string_literals: tuple[str, ...]
    literal_spans: tuple[LiteralSpan, ...]
    semicolons: tuple[int, ...]


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
    literals: list[str] = []
    literal_spans: list[LiteralSpan] = []
    semicolons: list[int] = []
    index = 0
    state = "normal"
    quote = ""
    literal: list[str] = []
    literal_start = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if state == "normal":
            if char == "/" and next_char == "/":
                output[index] = output[index + 1] = " "
                state = "line_comment"
                index += 2
                continue
            if char == "/" and next_char == "*":
                output[index] = output[index + 1] = " "
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
                raise CypherValidationError(
                    "CYPHER_BACKTICK_IDENTIFIER", "backtick identifiers are not allowed"
                )
            elif char == ";":
                semicolons.append(index)
            index += 1
            continue
        if state == "line_comment":
            output[index] = "\n" if char == "\n" else " "
            if char == "\n":
                state = "normal"
            index += 1
            continue
        if state == "block_comment":
            output[index] = " "
            if char == "*" and next_char == "/":
                output[index + 1] = " "
                state = "normal"
                index += 2
            else:
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
    if state in {"string", "block_comment"}:
        raise CypherValidationError("CYPHER_UNTERMINATED_TOKEN", "unterminated string or comment")
    return LexedCypher(
        "".join(output), tuple(literals), tuple(literal_spans), tuple(semicolons)
    )


def defensive_read_only_check(text: str) -> None:
    """Executor-side defense in depth; full validation still belongs to the pipeline."""

    lexed = lex_cypher(text)
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
    def __init__(self, catalog: SchemaCatalog, *, max_rows: int = 100):
        self.catalog = catalog
        self.max_rows = max_rows

    def validate(self, plan: QueryPlan, cypher: str) -> ValidatedCypher:
        if not isinstance(cypher, str) or not cypher.strip():
            self._fail("CYPHER_EMPTY", "Cypher must be non-empty text")
        lexed = lex_cypher(cypher)
        defensive_read_only_check(cypher)
        sanitized = lexed.sanitized
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
        if not 1 <= limit <= self.max_rows:
            self._fail("CYPHER_LIMIT_EXCEEDED", f"LIMIT must be between 1 and {self.max_rows}")

        return ValidatedCypher._issue(
            text=cypher.strip(),
            parameters=dict(plan.filters),
            limit=limit,
            labels=tuple(sorted(labels)),
            relationship_types=tuple(sorted(relationship_types)),
            provenance=provenance,
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
            policy = FILTER_BINDINGS[parameter]
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
        if set(sources) != required:
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
