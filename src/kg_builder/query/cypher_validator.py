"""Conservative static validator for externally supplied read-only Cypher."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .query_plan import QueryPlan
from .schema_catalog import SchemaCatalog


class CypherValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ValidatedCypher:
    text: str
    parameters: dict[str, Any]
    limit: int
    labels: tuple[str, ...]
    relationship_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LexedCypher:
    sanitized: str
    string_literals: tuple[str, ...]
    semicolons: tuple[int, ...]


FORBIDDEN_KEYWORDS = re.compile(
    r"\b(?:CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|CALL|LOAD\s+CSV|FOREACH|"
    r"ALTER|GRANT|DENY|REVOKE|START|STOP|TERMINATE|USE|SHOW|PROFILE|EXPLAIN|UNION)\b",
    re.IGNORECASE,
)
PARAMETER = re.compile(r"\$([A-Za-z][A-Za-z0-9_]*)")
PROPERTY_ACCESS = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\.([A-Za-z][A-Za-z0-9_]*)\b")
NODE_OCCURRENCE = re.compile(r"\(([^()]*)\)", re.DOTALL)
RELATION_OCCURRENCE = re.compile(
    r"(?=(\(([^()]*)\)\s*(<-|-)\s*\[([^\]]*)\]\s*(->|-)\s*\(([^()]*)\)))",
    re.DOTALL,
)
RETURN_ALIAS = re.compile(r"\bAS\s+([A-Za-z][A-Za-z0-9_]*)\b", re.IGNORECASE)


def lex_cypher(text: str) -> LexedCypher:
    output = list(text)
    literals: list[str] = []
    semicolons: list[int] = []
    index = 0
    state = "normal"
    quote = ""
    literal: list[str] = []
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
        if state == "string":
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
                literals.append("".join(literal))
                state = "normal"
            else:
                literal.append(char)
            index += 1
    if state in {"string", "block_comment"}:
        raise CypherValidationError("CYPHER_UNTERMINATED_TOKEN", "unterminated string or comment")
    return LexedCypher("".join(output), tuple(literals), tuple(semicolons))


def _node_parts(body: str) -> tuple[str | None, set[str], set[str]]:
    prefix, _, map_body = body.partition("{")
    variable_match = re.match(r"\s*([A-Za-z][A-Za-z0-9_]*)", prefix)
    variable = variable_match.group(1) if variable_match else None
    labels = set(re.findall(r":\s*([A-Za-z][A-Za-z0-9_]*)", prefix))
    map_properties = set(re.findall(r"\b([A-Za-z][A-Za-z0-9_]*)\s*:", map_body))
    return variable, labels, map_properties


class CypherValidator:
    def __init__(self, catalog: SchemaCatalog, *, max_rows: int = 100):
        self.catalog = catalog
        self.max_rows = max_rows

    def validate(self, plan: QueryPlan, cypher: str) -> ValidatedCypher:
        if not isinstance(cypher, str) or not cypher.strip():
            self._fail("CYPHER_EMPTY", "Cypher must be non-empty text")
        lexed = lex_cypher(cypher)
        sanitized = lexed.sanitized
        if lexed.semicolons:
            without_trailing = sanitized.rstrip()
            allowed_trailing = len(lexed.semicolons) == 1 and without_trailing.endswith(";")
            if not allowed_trailing:
                self._fail("CYPHER_MULTI_STATEMENT", "multiple Cypher statements are not allowed")
        forbidden = FORBIDDEN_KEYWORDS.search(sanitized)
        if forbidden:
            self._fail(
                "CYPHER_FORBIDDEN_KEYWORD",
                f"forbidden Cypher keyword: {forbidden.group(0).upper()}",
            )
        if not re.search(r"\bMATCH\b", sanitized, re.IGNORECASE):
            self._fail("CYPHER_MATCH_REQUIRED", "query must contain MATCH")
        if not re.search(r"\bRETURN\b", sanitized, re.IGNORECASE):
            self._fail("CYPHER_RETURN_REQUIRED", "query must contain RETURN")
        if any(value != "VERIFIED" for value in lexed.string_literals):
            self._fail(
                "CYPHER_LITERAL_VALUE",
                "string filter values must be parameters; only VERIFIED policy literals are allowed",
            )

        limit_matches = re.findall(r"\bLIMIT\s+(\d+)\b", sanitized, re.IGNORECASE)
        if len(limit_matches) != 1:
            self._fail("CYPHER_LIMIT_REQUIRED", "query must contain exactly one literal LIMIT")
        limit = int(limit_matches[0])
        if not 1 <= limit <= self.max_rows:
            self._fail("CYPHER_LIMIT_EXCEEDED", f"LIMIT must be between 1 and {self.max_rows}")
        without_limit = re.sub(r"\bLIMIT\s+\d+\b", "", sanitized, flags=re.IGNORECASE)
        if re.search(r"(?<![A-Za-z0-9_$])\d+(?:\.\d+)?(?![A-Za-z0-9_])", without_limit):
            self._fail(
                "CYPHER_LITERAL_VALUE",
                "numeric filter values must be QueryPlan parameters",
            )

        if re.search(r"\[[^\]]*\*[^\]]*\]", sanitized, re.DOTALL):
            self._fail(
                "CYPHER_VARIABLE_LENGTH_PATH", "variable-length relationship traversal is forbidden"
            )

        variable_labels: dict[str, set[str]] = {}
        direct_node_properties: list[tuple[set[str], set[str]]] = []
        labels: set[str] = set()
        for match in NODE_OCCURRENCE.finditer(sanitized):
            variable, occurrence_labels, map_properties = _node_parts(match.group(1))
            unknown_labels = occurrence_labels - set(self.catalog.nodes)
            if unknown_labels:
                self._fail(
                    "CYPHER_UNKNOWN_LABEL", f"undeclared labels: {sorted(unknown_labels)}"
                )
            if variable and occurrence_labels:
                variable_labels.setdefault(variable, set()).update(occurrence_labels)
            labels.update(occurrence_labels)
            if occurrence_labels and map_properties:
                direct_node_properties.append((occurrence_labels, map_properties))
        if not labels:
            self._fail("CYPHER_UNLABELED_SCAN", "at least one declared node label is required")

        relationship_variables: dict[str, str] = {}
        relationship_types: set[str] = set()
        for match in RELATION_OCCURRENCE.finditer(sanitized):
            left_body, left_arrow, rel_body, right_arrow, right_body = (
                match.group(2),
                match.group(3),
                match.group(4),
                match.group(5),
                match.group(6),
            )
            rel_type_match = re.search(r":\s*([A-Za-z][A-Za-z0-9_]*)", rel_body)
            if not rel_type_match:
                self._fail("CYPHER_UNTYPED_RELATIONSHIP", "all relationships must be typed")
            rel_type = rel_type_match.group(1)
            if rel_type not in self.catalog.relationships:
                self._fail("CYPHER_UNKNOWN_RELATIONSHIP", f"undeclared relationship: {rel_type}")
            relationship_types.add(rel_type)
            _, _, relationship_map = rel_body.partition("{")
            relationship_map_properties = set(
                re.findall(r"\b([A-Za-z][A-Za-z0-9_]*)\s*:", relationship_map)
            )
            undeclared_relationship_properties = (
                relationship_map_properties - self.catalog.relationships[rel_type].properties
            )
            if undeclared_relationship_properties:
                self._fail(
                    "CYPHER_UNKNOWN_PROPERTY",
                    "undeclared relationship properties: "
                    f"{sorted(undeclared_relationship_properties)}",
                )
            rel_var_match = re.match(r"\s*([A-Za-z][A-Za-z0-9_]*)", rel_body)
            if rel_var_match:
                relationship_variables[rel_var_match.group(1)] = rel_type
            left_var, left_labels, _ = _node_parts(left_body)
            right_var, right_labels, _ = _node_parts(right_body)
            left_labels = left_labels or variable_labels.get(left_var or "", set())
            right_labels = right_labels or variable_labels.get(right_var or "", set())
            if not left_labels or not right_labels:
                self._fail(
                    "CYPHER_ENDPOINT_LABEL_REQUIRED",
                    f"relationship {rel_type} endpoints must resolve to declared labels",
                )
            if left_arrow == "-" and right_arrow == "->":
                start, end = left_labels, right_labels
            elif left_arrow == "<-" and right_arrow == "-":
                start, end = right_labels, left_labels
            else:
                self._fail(
                    "CYPHER_RELATIONSHIP_DIRECTION", "undirected relationships are not allowed"
                )
            definition = self.catalog.relationships[rel_type]
            if not self._labels_match(start, definition.from_labels) or not self._labels_match(
                end, definition.to_labels
            ):
                self._fail(
                    "CYPHER_RELATIONSHIP_ENDPOINT",
                    f"relationship {rel_type} direction or endpoint labels violate ontology",
                )

        for occurrence_labels, properties in direct_node_properties:
            allowed = self.catalog.properties_for_labels(occurrence_labels)
            unknown = properties - allowed
            if unknown:
                self._fail("CYPHER_UNKNOWN_PROPERTY", f"undeclared node properties: {sorted(unknown)}")
        for variable, prop in PROPERTY_ACCESS.findall(sanitized):
            if variable in variable_labels:
                allowed = self.catalog.properties_for_labels(variable_labels[variable])
            elif variable in relationship_variables:
                allowed = self.catalog.relationships[relationship_variables[variable]].properties
            else:
                self._fail(
                    "CYPHER_UNKNOWN_VARIABLE", f"property access uses unknown graph variable: {variable}"
                )
            if prop not in allowed:
                self._fail(
                    "CYPHER_UNKNOWN_PROPERTY",
                    f"property {variable}.{prop} is not declared for its label or relationship",
                )

        parameters = set(PARAMETER.findall(sanitized))
        missing_filters = set(plan.filters) - parameters
        if missing_filters:
            self._fail(
                "CYPHER_FILTER_NOT_PARAMETERIZED",
                f"QueryPlan filters are not referenced as parameters: {sorted(missing_filters)}",
            )
        extra_parameters = parameters - set(plan.filters)
        if extra_parameters:
            self._fail(
                "CYPHER_PARAMETER_MISSING",
                f"query parameters are absent from QueryPlan: {sorted(extra_parameters)}",
            )

        aliases = set(RETURN_ALIAS.findall(sanitized))
        required_aliases = set(plan.requested_fields) | set(plan.filters)
        missing_aliases = required_aliases - aliases
        if missing_aliases:
            self._fail(
                "CYPHER_RETURN_FIELD_MISSING",
                f"query must return requested fields and scope aliases: {sorted(missing_aliases)}",
            )
        if plan.evidence_required:
            evidence_aliases = {
                "evidence_id",
                "excerpt_page",
                "source_pdf_page",
                "printed_page",
                "source_text",
                "fact_status",
                "evidence_verification_status",
            }
            if "SUPPORTED_BY" not in relationship_types or "Evidence" not in labels:
                self._fail(
                    "CYPHER_EVIDENCE_PATH_REQUIRED",
                    "evidence-required query must traverse SUPPORTED_BY to Evidence",
                )
            missing_evidence_aliases = evidence_aliases - aliases
            if missing_evidence_aliases:
                self._fail(
                    "CYPHER_EVIDENCE_FIELDS_REQUIRED",
                    f"missing Evidence or verification aliases: {sorted(missing_evidence_aliases)}",
                )
            if lexed.string_literals.count("VERIFIED") < 2:
                self._fail(
                    "CYPHER_VERIFIED_FILTER_REQUIRED",
                    "fact and Evidence VERIFIED filters are required",
                )
            if "status" not in sanitized or "verification_status" not in sanitized:
                self._fail(
                    "CYPHER_VERIFIED_FILTER_REQUIRED",
                    "fact status and Evidence verification_status must be filtered",
                )

        return ValidatedCypher(
            text=cypher.strip().removesuffix(";").rstrip(),
            parameters=dict(plan.filters),
            limit=limit,
            labels=tuple(sorted(labels)),
            relationship_types=tuple(sorted(relationship_types)),
        )

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
